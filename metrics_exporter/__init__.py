"""
Fleet Metrics Exporter — Prometheus-compatible /metrics endpoint.

Zero-dependency except `prometheus_client` (optional) and stdlib.
If prometheus_client is unavailable, falls back to plain-text exposition.
"""
import time
import threading
from typing import Optional, Dict, Any, List
from collections import deque


class Counter:
    """Thread-safe Prometheus counter."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self._value = 0.0
        self._lock = threading.Lock()

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    @property
    def value(self) -> float:
        with self._lock:
            return self._value

    def expose(self) -> str:
        return f"# HELP {self.name} {self.help_text}\n# TYPE {self.name} counter\n{self.name} {self.value}\n"


class Gauge:
    """Thread-safe Prometheus gauge."""

    def __init__(self, name: str, help_text: str):
        self.name = name
        self.help_text = help_text
        self._value = 0.0
        self._lock = threading.Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = float(value)

    @property
    def value(self) -> float:
        with self._lock:
            return self._value

    def expose(self) -> str:
        return f"# HELP {self.name} {self.help_text}\n# TYPE {self.name} gauge\n{self.name} {self.value}\n"


class Histogram:
    """Thread-safe Prometheus histogram with 10 buckets."""

    DEFAULT_BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf")]

    def __init__(self, name: str, help_text: str, buckets: Optional[List[float]] = None):
        self.name = name
        self.help_text = help_text
        self.buckets = buckets or self.DEFAULT_BUCKETS
        self._counts = {b: 0 for b in self.buckets}
        self._sum = 0.0
        self._total = 0.0
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._total += 1
            for b in self.buckets:
                if value <= b:
                    self._counts[b] += 1

    def expose(self) -> str:
        lines = [
            f"# HELP {self.name} {self.help_text}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            for b in self.buckets:
                label = "+Inf" if b == float("inf") else b
                lines.append(f'{self.name}_bucket{{le="{label}"}} {self._counts[b]}')
            lines.append(f"{self.name}_sum {self._sum}")
            lines.append(f"{self.name}_count {int(self._total)}")
        return "\n".join(lines) + "\n"


class FleetMetrics:
    """
    Prometheus-compatible metrics collector for the Cocapn Fleet.

    Metrics:
      breeding_cycles_total     — Counter
      agents_spawned_total      — Counter
      agents_sunset_total       — Counter
      errors_total              — Counter
      active_agents             — Gauge
      thermal_budget_used_pct   — Gauge
      circuit_breaker_state     — Gauge (0=closed, 1=half_open, 2=open)
      breeding_cycle_duration_seconds — Histogram
      http_request_duration_seconds   — Histogram
    """

    def __init__(self):
        self.breeding_cycles_total = Counter(
            "breeding_cycles_total",
            "Total number of breeding cycles executed.",
        )
        self.agents_spawned_total = Counter(
            "agents_spawned_total",
            "Total number of agents spawned.",
        )
        self.agents_sunset_total = Counter(
            "agents_sunset_total",
            "Total number of agents sunset (retired).",
        )
        self.errors_total = Counter(
            "errors_total",
            "Total number of errors across all operations.",
        )

        self.active_agents = Gauge(
            "active_agents",
            "Current number of active agents.",
        )
        self.thermal_budget_used_pct = Gauge(
            "thermal_budget_used_pct",
            "Percentage of thermal budget currently consumed.",
        )
        self.circuit_breaker_state = Gauge(
            "circuit_breaker_state",
            "Circuit breaker state: 0=closed, 1=half_open, 2=open.",
        )

        self.breeding_cycle_duration_seconds = Histogram(
            "breeding_cycle_duration_seconds",
            "Duration of breeding cycles in seconds.",
        )
        self.http_request_duration_seconds = Histogram(
            "http_request_duration_seconds",
            "Duration of HTTP requests in seconds.",
        )

    def record_breeding_cycle(self, duration_seconds: float, agents_spawned: int, agents_sunset: int) -> None:
        """Record a completed breeding cycle."""
        self.breeding_cycles_total.inc()
        self.breeding_cycle_duration_seconds.observe(duration_seconds)
        if agents_spawned:
            self.agents_spawned_total.inc(agents_spawned)
        if agents_sunset:
            self.agents_sunset_total.inc(agents_sunset)

    def record_error(self) -> None:
        self.errors_total.inc()

    def record_http(self, duration_seconds: float) -> None:
        self.http_request_duration_seconds.observe(duration_seconds)

    def set_active_agents(self, count: int) -> None:
        self.active_agents.set(float(count))

    def set_thermal_budget(self, pct: float) -> None:
        self.thermal_budget_used_pct.set(pct)

    def set_circuit_breaker(self, state: str) -> None:
        mapping = {"closed": 0.0, "half_open": 1.0, "open": 2.0}
        self.circuit_breaker_state.set(mapping.get(state, 0.0))

    def expose(self) -> str:
        """Return full Prometheus exposition format."""
        parts = [
            self.breeding_cycles_total.expose(),
            self.agents_spawned_total.expose(),
            self.agents_sunset_total.expose(),
            self.errors_total.expose(),
            self.active_agents.expose(),
            self.thermal_budget_used_pct.expose(),
            self.circuit_breaker_state.expose(),
            self.breeding_cycle_duration_seconds.expose(),
            self.http_request_duration_seconds.expose(),
        ]
        return "\n".join(parts)

    @staticmethod
    def make_response() -> tuple:
        """Return (body, status, headers) for a WSGI/Flask/FastAPI handler."""
        m = FleetMetrics()
        body = m.expose()
        return body, 200, {"Content-Type": "text/plain; charset=utf-8"}
