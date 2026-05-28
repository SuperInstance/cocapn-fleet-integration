"""
Fleet Circuit Breaker — Cocapn Fleet HTTP Resilience Layer

Per-service circuit breakers, exponential backoff, cached fallbacks.
Zero-dependency except `requests` and stdlib.
"""
import time
import threading
from typing import Optional, Dict, Any
from dataclasses import dataclass

import requests
import yaml


@dataclass
class CircuitState:
    """Mutable circuit state — locked via Breaker._lock."""
    failures: int = 0
    last_failure_time: float = 0.0
    state: str = "closed"          # closed | open | half_open


class Breaker:
    """
    Simple in-memory circuit breaker.

    Opens after `failure_threshold` errors within `window_seconds`.
    Half-opens after `cooldown_seconds`.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        window_seconds: float = 60.0,
        cooldown_seconds: float = 30.0,
    ):
        self.failure_threshold = failure_threshold
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self._state = CircuitState()
        self._lock = threading.Lock()

    def _is_window_active(self) -> bool:
        return (time.time() - self._state.last_failure_time) < self.window_seconds

    def call(self, fn, *args, **kwargs):
        """
        Execute `fn` if breaker allows.  On exception, record failure
        and re-raise.  Callers must catch.
        """
        with self._lock:
            if self._state.state == "open":
                if (time.time() - self._state.last_failure_time) >= self.cooldown_seconds:
                    self._state.state = "half_open"
                else:
                    raise CircuitOpenError("Circuit breaker is OPEN")

        try:
            result = fn(*args, **kwargs)
        except Exception as exc:
            with self._lock:
                now = time.time()
                if (now - self._state.last_failure_time) >= self.window_seconds:
                    self._state.failures = 0
                self._state.failures += 1
                self._state.last_failure_time = now
                if self._state.failures >= self.failure_threshold:
                    self._state.state = "open"
            raise exc

        with self._lock:
            now = time.time()
            if self._state.state == "half_open":
                # Success while half-open → close it.
                self._state.state = "closed"
                self._state.failures = 0
            elif self._state.state == "closed":
                # Reset failures if window elapsed, otherwise keep counting.
                if (now - self._state.last_failure_time) >= self.window_seconds:
                    self._state.failures = 0

        return result

    @property
    def health(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "state": self._state.state,
                "failures": self._state.failures,
                "last_failure_age_seconds": round(time.time() - self._state.last_failure_time, 2),
            }


class CircuitOpenError(Exception):
    pass


class CachedResponse:
    """TTL cache for last-known-good HTTP responses."""

    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl = ttl_seconds
        self._data: Optional[Any] = None
        self._at: float = 0.0
        self._lock = threading.Lock()

    def set(self, data: Any) -> None:
        with self._lock:
            self._data = data
            self._at = time.time()

    def get(self) -> Optional[Any]:
        with self._lock:
            if self._data is None:
                return None
            if (time.time() - self._at) > self.ttl:
                return None
            return self._data


class FleetHTTPClient:
    """
    Production-grade HTTP client for inter-repo fleet communication.

    Features:
      • Per-service circuit breakers (5 errors / 60 s)
      • Exponential retry (max 3, base 1 s)
      • Timeouts (5 s connect, 30 s read)
      • Cached last-known-good fallback (TTL 60 s)
      • `.health()` for observability
    """

    DEFAULT_CONNECT_TIMEOUT = 5
    DEFAULT_READ_TIMEOUT = 30
    DEFAULT_RETRY_MAX = 3
    DEFAULT_RETRY_BASE = 1.0
    DEFAULT_BREAKER_FAILURES = 5
    DEFAULT_BREAKER_WINDOW = 60.0
    DEFAULT_BREAKER_COOLDOWN = 30.0
    DEFAULT_CACHE_TTL = 60.0

    def __init__(self, services_yaml_path: str):
        with open(services_yaml_path, "r") as fh:
            raw = yaml.safe_load(fh)
        self._services: Dict[str, Dict[str, Any]] = raw.get("services", {})

        self._breakers: Dict[str, Breaker] = {}
        self._caches: Dict[str, CachedResponse] = {}
        self._session = requests.Session()

        for name, cfg in self._services.items():
            self._breakers[name] = Breaker(
                failure_threshold=cfg.get("breaker_failures", self.DEFAULT_BREAKER_FAILURES),
                window_seconds=cfg.get("breaker_window", self.DEFAULT_BREAKER_WINDOW),
                cooldown_seconds=cfg.get("breaker_cooldown", self.DEFAULT_BREAKER_COOLDOWN),
            )
            self._caches[name] = CachedResponse(
                ttl_seconds=cfg.get("cache_ttl", self.DEFAULT_CACHE_TTL)
            )

    def request(
        self,
        service: str,
        method: str = "GET",
        path: str = "/",
        **kwargs,
    ) -> requests.Response:
        """
        Send an HTTP request to a fleet service with circuit breaker,
        retry, and fallback semantics.
        """
        cfg = self._services.get(service)
        if cfg is None:
            raise ValueError(f"Unknown fleet service: {service}")

        base_url = cfg["url"].rstrip("/")
        url = f"{base_url}{path}"

        connect_timeout = cfg.get("connect_timeout", self.DEFAULT_CONNECT_TIMEOUT)
        read_timeout = cfg.get("read_timeout", self.DEFAULT_READ_TIMEOUT)
        retry_max = cfg.get("retry_max", self.DEFAULT_RETRY_MAX)
        retry_base = cfg.get("retry_base", self.DEFAULT_RETRY_BASE)

        breaker = self._breakers[service]
        cache = self._caches[service]

        def _do_request():
            return self._session.request(
                method,
                url,
                timeout=(connect_timeout, read_timeout),
                **kwargs,
            )

        last_exc: Optional[Exception] = None

        for attempt in range(retry_max + 1):
            try:
                resp = breaker.call(_do_request)
                if resp.ok:
                    # Cache successful response body (text only for now)
                    cache.set({"status_code": resp.status_code, "text": resp.text, "headers": dict(resp.headers)})
                return resp
            except (requests.RequestException, CircuitOpenError) as exc:
                last_exc = exc
                if isinstance(exc, CircuitOpenError):
                    break
                if attempt < retry_max:
                    sleep_time = retry_base * (2 ** attempt)
                    time.sleep(sleep_time)

        # All retries exhausted or breaker open — try fallback.
        cached = cache.get()
        if cached:
            resp = requests.Response()
            resp.status_code = cached["status_code"]
            resp._content = cached["text"].encode("utf-8")
            resp.headers.update(cached["headers"])
            return resp

        if last_exc is None:
            last_exc = CircuitOpenError("Circuit breaker is OPEN and no cached response available")
        raise last_exc

    def get(self, service: str, path: str = "/", **kwargs) -> requests.Response:
        return self.request(service, "GET", path, **kwargs)

    def post(self, service: str, path: str = "/", **kwargs) -> requests.Response:
        return self.request(service, "POST", path, **kwargs)

    def health(self) -> Dict[str, Dict[str, Any]]:
        """Return circuit breaker health for every registered service."""
        return {name: breaker.health for name, breaker in self._breakers.items()}
