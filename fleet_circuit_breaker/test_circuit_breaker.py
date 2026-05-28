"""
pytest suite for FleetHTTPClient & Breaker.

Covers:
  • breaker opens after 5 failures
  • breaker half-opens after cooldown
  • retry succeeds on transient failure
  • fallback returns cached response when breaker open
  • health() returns correct state
  • unknown service raises ValueError
  • exponential backoff timing
  • cache TTL expiry
  • fallback body content fidelity
  • no retry on CircuitOpenError
"""
import time
import json
import threading
from unittest.mock import patch, MagicMock

import pytest
import requests

from fleet_circuit_breaker import (
    FleetHTTPClient,
    Breaker,
    CircuitState,
    CircuitOpenError,
    CachedResponse,
)


@pytest.fixture
def mock_services_file(tmp_path):
    path = tmp_path / "services.yaml"
    path.write_text("""
services:
  test-svc:
    url: http://test.local
    connect_timeout: 1
    read_timeout: 2
    retry_max: 0
    retry_base: 0.1
    breaker_failures: 5
    breaker_window: 60
    breaker_cooldown: 1
    cache_ttl: 1
""")
    return str(path)


@pytest.fixture
def client(mock_services_file):
    return FleetHTTPClient(mock_services_file)


@pytest.fixture
def client_retry(tmp_path):
    """Client with retries enabled for transient-failure tests."""
    path = tmp_path / "services.yaml"
    path.write_text("""
services:
  test-svc:
    url: http://test.local
    connect_timeout: 1
    read_timeout: 2
    retry_max: 2
    retry_base: 0.1
    breaker_failures: 5
    breaker_window: 60
    breaker_cooldown: 1
    cache_ttl: 1
""")
    return FleetHTTPClient(str(path))


# ---------------------------------------------------------------------------
# Breaker unit tests
# ---------------------------------------------------------------------------

class TestBreaker:
    def test_closed_by_default(self):
        b = Breaker()
        assert b.health["state"] == "closed"

    def test_opens_after_5_failures(self):
        b = Breaker(failure_threshold=5, window_seconds=60)
        for _ in range(5):
            with pytest.raises(RuntimeError):
                b.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert b.health["state"] == "open"

    def test_half_opens_after_cooldown(self):
        b = Breaker(failure_threshold=5, window_seconds=60, cooldown_seconds=0.1)
        for _ in range(5):
            with pytest.raises(RuntimeError):
                b.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert b.health["state"] == "open"
        time.sleep(0.15)
        # After cooldown the breaker half-opens for one probe call.
        # If it were still fully open this would raise CircuitOpenError,
        # not RuntimeError.
        with pytest.raises(RuntimeError):
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert b.health["state"] == "open"

    def test_half_open_success_closes(self):
        b = Breaker(failure_threshold=5, window_seconds=60, cooldown_seconds=0.1)
        for _ in range(5):
            with pytest.raises(RuntimeError):
                b.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        time.sleep(0.15)
        b.call(lambda: "ok")
        assert b.health["state"] == "closed"

    def test_failure_window_resets(self):
        b = Breaker(failure_threshold=5, window_seconds=0.1)
        for _ in range(4):
            with pytest.raises(RuntimeError):
                b.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        time.sleep(0.15)
        # Old failures outside window; this one is fresh.
        with pytest.raises(RuntimeError):
            b.call(lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert b.health["state"] == "closed"  # only 1 failure in current window

    def test_concurrent_access(self):
        b = Breaker(failure_threshold=100, window_seconds=60)
        errors = []

        def worker():
            try:
                for _ in range(20):
                    b.call(lambda: "ok")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        assert b.health["state"] == "closed"


# ---------------------------------------------------------------------------
# CachedResponse tests
# ---------------------------------------------------------------------------

class TestCachedResponse:
    def test_get_returns_none_initially(self):
        c = CachedResponse()
        assert c.get() is None

    def test_ttl_expiry(self):
        c = CachedResponse(ttl_seconds=0.05)
        c.set("data")
        assert c.get() == "data"
        time.sleep(0.08)
        assert c.get() is None


# ---------------------------------------------------------------------------
# FleetHTTPClient tests
# ---------------------------------------------------------------------------

class TestFleetHTTPClient:
    def test_unknown_service_raises(self, client):
        with pytest.raises(ValueError, match="Unknown fleet service"):
            client.request("no-such-service")

    def test_retry_succeeds_on_transient_failure(self, client_retry):
        with patch.object(client_retry._session, "request") as mock_req:
            # Fail twice, succeed third time.
            mock_req.side_effect = [
                requests.ConnectionError("fail 1"),
                requests.Timeout("fail 2"),
                MagicMock(ok=True, status_code=200, text="win", headers={}),
            ]
            resp = client_retry.get("test-svc", "/ping")
            assert resp.status_code == 200
            assert resp.text == "win"
            assert mock_req.call_count == 3

    def test_fallback_returns_cached_when_breaker_open(self, client):
        with patch.object(client._session, "request") as mock_req:
            # Seed cache with a success.
            mock_req.return_value = MagicMock(ok=True, status_code=200, text="cached-body", headers={"X-Tag": "v1"})
            client.get("test-svc", "/data")

            # Trip the breaker directly (bypass cache fallback in request()).
            breaker = client._breakers["test-svc"]
            for _ in range(5):
                with pytest.raises(requests.ConnectionError):
                    breaker.call(lambda: (_ for _ in ()).throw(requests.ConnectionError("boom")))
            assert breaker.health["state"] == "open"

            # Next request via client: breaker open → cache fallback.
            resp = client.get("test-svc", "/data")
            assert resp.status_code == 200
            assert resp.text == "cached-body"

    def test_no_retry_on_circuit_open(self, client):
        with patch.object(client._session, "request") as mock_req:
            mock_req.side_effect = requests.ConnectionError("boom")
            for _ in range(5):
                with pytest.raises(requests.ConnectionError):
                    client.get("test-svc", "/data")
            # Breaker is now open.
            mock_req.reset_mock()
            with pytest.raises((CircuitOpenError, requests.ConnectionError)):
                client.get("test-svc", "/data")
            # Should not have retried because breaker short-circuits.
            assert mock_req.call_count == 0

    def test_health_returns_all_services(self, client):
        h = client.health()
        assert "test-svc" in h
        assert h["test-svc"]["state"] == "closed"

    def test_exponential_backoff_timing(self, client_retry):
        with patch.object(client_retry._session, "request") as mock_req, \
             patch("time.sleep") as mock_sleep:
            mock_req.side_effect = [
                requests.ConnectionError("fail 1"),
                requests.ConnectionError("fail 2"),
                MagicMock(ok=True, status_code=200, text="ok", headers={}),
            ]
            client_retry.get("test-svc", "/ping")
            # retry_base=0.1 → sleeps at 0.1, 0.2
            assert mock_sleep.call_args_list == [
                ((0.1,),),
                ((0.2,),),
            ]

    def test_cache_ttl_expires(self, client):
        with patch.object(client._session, "request") as mock_req:
            mock_req.return_value = MagicMock(ok=True, status_code=200, text="fresh", headers={})
            client.get("test-svc", "/data")
            time.sleep(1.1)  # cache_ttl=1
            # Trip breaker, then request → cache expired → raises.
            mock_req.side_effect = requests.ConnectionError("boom")
            for _ in range(5):
                with pytest.raises(requests.ConnectionError):
                    client.get("test-svc", "/data")
            with pytest.raises(CircuitOpenError):
                client.get("test-svc", "/data")

    def test_fallback_body_content_fidelity(self, client):
        with patch.object(client._session, "request") as mock_req:
            body = json.dumps({"status": "degraded", "code": 42})
            mock_req.return_value = MagicMock(ok=True, status_code=200, text=body, headers={"Content-Type": "application/json"})
            client.get("test-svc", "/api")

            # Trip the breaker directly so cache remains intact.
            breaker = client._breakers["test-svc"]
            for _ in range(5):
                with pytest.raises(requests.ConnectionError):
                    breaker.call(lambda: (_ for _ in ()).throw(requests.ConnectionError("boom")))
            assert breaker.health["state"] == "open"

            resp = client.get("test-svc", "/api")
            assert json.loads(resp.text) == {"status": "degraded", "code": 42}
            assert resp.headers["Content-Type"] == "application/json"

    def test_post_method(self, client):
        with patch.object(client._session, "request") as mock_req:
            mock_req.return_value = MagicMock(ok=True, status_code=201, text="created", headers={})
            resp = client.post("test-svc", "/items", json={"name": "foo"})
            assert resp.status_code == 201
            assert resp.text == "created"
            mock_req.assert_called_once()
            _, kwargs = mock_req.call_args
            assert kwargs["json"] == {"name": "foo"}
