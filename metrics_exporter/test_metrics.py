"""
pytest suite for FleetMetrics and primitive metric types.

Covers:
  • Counter increments correctly
  • Gauge sets correctly
  • Histogram buckets and sum/count
  • FleetMetrics expose() produces valid Prometheus text
  • record_breeding_cycle updates all dependent metrics
  • record_error increments errors_total
  • record_http populates http_request_duration_seconds
  • set_circuit_breaker maps states to numeric gauge values
  • Thread safety (parallel increments)
"""
import threading
import time

import pytest

from metrics_exporter import (
    Counter,
    Gauge,
    Histogram,
    FleetMetrics,
)


class TestCounter:
    def test_inc_default(self):
        c = Counter("c", "help")
        c.inc()
        assert c.value == 1.0

    def test_inc_by_amount(self):
        c = Counter("c", "help")
        c.inc(5.5)
        assert c.value == 5.5

    def test_expose_format(self):
        c = Counter("c", "help")
        c.inc(2)
        text = c.expose()
        assert "# HELP c help" in text
        assert "# TYPE c counter" in text
        assert "c 2.0" in text


class TestGauge:
    def test_set(self):
        g = Gauge("g", "help")
        g.set(42.0)
        assert g.value == 42.0

    def test_expose_format(self):
        g = Gauge("g", "help")
        g.set(7)
        text = g.expose()
        assert "# HELP g help" in text
        assert "# TYPE g gauge" in text
        assert "g 7.0" in text


class TestHistogram:
    def test_observe_counts_buckets(self):
        h = Histogram("h", "help", buckets=[0.1, 1.0, float("inf")])
        h.observe(0.05)
        h.observe(0.5)
        h.observe(2.0)
        text = h.expose()
        assert 'h_bucket{le="0.1"} 1' in text
        assert 'h_bucket{le="1.0"} 2' in text
        assert 'h_bucket{le="+Inf"} 3' in text
        assert "h_sum 2.55" in text
        assert "h_count 3" in text

    def test_expose_format(self):
        h = Histogram("h", "help")
        h.observe(0.5)
        text = h.expose()
        assert "# HELP h help" in text
        assert "# TYPE h histogram" in text


class TestFleetMetrics:
    def test_expose_contains_all_metrics(self):
        m = FleetMetrics()
        text = m.expose()
        assert "breeding_cycles_total" in text
        assert "agents_spawned_total" in text
        assert "agents_sunset_total" in text
        assert "errors_total" in text
        assert "active_agents" in text
        assert "thermal_budget_used_pct" in text
        assert "circuit_breaker_state" in text
        assert "breeding_cycle_duration_seconds" in text
        assert "http_request_duration_seconds" in text

    def test_record_breeding_cycle(self):
        m = FleetMetrics()
        m.record_breeding_cycle(duration_seconds=1.23, agents_spawned=3, agents_sunset=1)
        assert m.breeding_cycles_total.value == 1.0
        assert m.agents_spawned_total.value == 3.0
        assert m.agents_sunset_total.value == 1.0
        assert m.breeding_cycle_duration_seconds._total == 1.0
        assert m.breeding_cycle_duration_seconds._sum == 1.23

    def test_record_error(self):
        m = FleetMetrics()
        m.record_error()
        m.record_error()
        assert m.errors_total.value == 2.0

    def test_record_http(self):
        m = FleetMetrics()
        m.record_http(0.45)
        assert m.http_request_duration_seconds._total == 1.0
        assert m.http_request_duration_seconds._sum == 0.45

    def test_set_active_agents(self):
        m = FleetMetrics()
        m.set_active_agents(17)
        assert m.active_agents.value == 17.0

    def test_set_thermal_budget(self):
        m = FleetMetrics()
        m.set_thermal_budget(73.5)
        assert m.thermal_budget_used_pct.value == 73.5

    @pytest.mark.parametrize("state,expected", [
        ("closed", 0.0),
        ("half_open", 1.0),
        ("open", 2.0),
        ("unknown", 0.0),
    ])
    def test_set_circuit_breaker(self, state, expected):
        m = FleetMetrics()
        m.set_circuit_breaker(state)
        assert m.circuit_breaker_state.value == expected

    def test_thread_safety(self):
        m = FleetMetrics()
        errors = []

        def worker():
            try:
                for _ in range(100):
                    m.record_breeding_cycle(0.01, 1, 0)
                    m.record_error()
                    m.record_http(0.02)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert m.breeding_cycles_total.value == 1000.0
        assert m.agents_spawned_total.value == 1000.0
        assert m.errors_total.value == 1000.0
        assert m.http_request_duration_seconds._total == 1000.0

    def test_make_response(self):
        body, status, headers = FleetMetrics.make_response()
        assert status == 200
        assert headers["Content-Type"] == "text/plain; charset=utf-8"
        assert "breeding_cycles_total" in body
