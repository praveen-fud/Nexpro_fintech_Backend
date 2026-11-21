"""
Request rate limiting and failed-login lockout.

Deliberately dependency-free: a fixed-memory sliding-window counter held in
the process. That is the right trade for a single-instance deployment and it
cannot fail open because a cache server is unreachable.

The one thing to know before scaling out: these counters are per process, so
N instances behind a load balancer each allow the configured budget. Set
``RATE_LIMIT_BACKEND`` work aside and move this to Redis (the compose file
already runs one) before running more than one replica of the API.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status

from app.core.config import get_settings


@dataclass(frozen=True)
class Window:
    """A parsed ``"<requests>/<seconds>"`` budget."""

    limit: int
    seconds: int

    @classmethod
    def parse(cls, spec: str) -> Window:
        try:
            raw_limit, raw_seconds = spec.split("/", 1)
            window = cls(limit=int(raw_limit), seconds=int(raw_seconds))
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                f"Invalid rate-limit spec {spec!r}; expected '<requests>/<seconds>', e.g. '10/300'."
            ) from exc
        if window.limit < 1 or window.seconds < 1:
            raise ValueError(f"Rate-limit spec {spec!r} must have positive values.")
        return window


@dataclass
class _Bucket:
    hits: deque[float] = field(default_factory=deque)


class SlidingWindowLimiter:
    """Thread-safe sliding-window counter.

    Bounded by construction: a key's deque never holds more than ``limit``
    timestamps, and keys idle for longer than their window are swept, so a
    flood of unique keys (spoofed IPs) cannot grow memory without bound.
    """

    def __init__(self, *, max_keys: int = 50_000) -> None:
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = threading.Lock()
        self._max_keys = max_keys
        self._last_sweep = time.monotonic()

    def _sweep(self, now: float, horizon: float) -> None:
        """Drop buckets whose most recent hit is older than the widest window
        in play. Cheap because it only runs every 60s or when oversized."""
        stale = [key for key, bucket in self._buckets.items() if not bucket.hits or bucket.hits[-1] < now - horizon]
        for key in stale:
            del self._buckets[key]
        self._last_sweep = now

    def check(self, *, scope: str, key: str, window: Window) -> tuple[bool, int]:
        """Record a hit. Returns ``(allowed, retry_after_seconds)``."""
        now = time.monotonic()
        cutoff = now - window.seconds
        bucket_key = (scope, key)

        with self._lock:
            if now - self._last_sweep > 60 or len(self._buckets) > self._max_keys:
                self._sweep(now, horizon=max(window.seconds, 3600))

            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = _Bucket()
                self._buckets[bucket_key] = bucket

            hits = bucket.hits
            while hits and hits[0] < cutoff:
                hits.popleft()

            if len(hits) >= window.limit:
                retry_after = max(1, int(hits[0] + window.seconds - now) + 1)
                return False, retry_after

            hits.append(now)
            return True, 0

    def reset(self, *, scope: str, key: str) -> None:
        with self._lock:
            self._buckets.pop((scope, key), None)

    def clear(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._last_sweep = time.monotonic()


class FailureTracker:
    """Counts failures per subject (an account identifier) and locks it out
    once the threshold is reached.

    This runs alongside the per-IP limiter, not instead of it: a password
    spray spread over a botnet shows up here even though each individual IP
    stays under its own budget.
    """

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def locked_for(self, subject: str, *, max_failures: int, lockout_seconds: int) -> int:
        """Seconds remaining on an active lockout, or 0 if not locked."""
        now = time.monotonic()
        with self._lock:
            times = [t for t in self._failures.get(subject, []) if t > now - lockout_seconds]
            if times:
                self._failures[subject] = times
            else:
                self._failures.pop(subject, None)
            if len(times) >= max_failures:
                return max(1, int(times[0] + lockout_seconds - now) + 1)
            return 0

    def record_failure(self, subject: str, *, lockout_seconds: int) -> None:
        now = time.monotonic()
        with self._lock:
            times = [t for t in self._failures.get(subject, []) if t > now - lockout_seconds]
            times.append(now)
            # Keep the list short; only the oldest kept entry matters.
            self._failures[subject] = times[-64:]

    def reset(self, subject: str) -> None:
        with self._lock:
            self._failures.pop(subject, None)

    def clear(self) -> None:
        with self._lock:
            self._failures.clear()


limiter = SlidingWindowLimiter()
login_failures = FailureTracker()


def client_ip(request: Request) -> str:
    """Best-effort client address.

    Only the right-most ``trusted_proxy_hops`` entries of X-Forwarded-For are
    trustworthy; everything to their left is attacker-controlled text. Taking
    the left-most entry (the common mistake) would let a client send
    ``X-Forwarded-For: <random>`` on every request and never hit a limit.
    """
    settings = get_settings()
    hops = settings.trusted_proxy_hops

    if hops > 0:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if parts:
                # The hop nearest us that we still trust.
                index = max(0, len(parts) - hops)
                return parts[index]

    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def _enforce(request: Request, *, scope: str, spec: str, key: str | None = None) -> None:
    settings = get_settings()
    if not settings.rate_limit_enabled:
        return
    window = Window.parse(spec)
    allowed, retry_after = limiter.check(scope=scope, key=key or client_ip(request), window=window)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many requests. Please wait a moment and try again.",
            headers={"Retry-After": str(retry_after)},
        )


def rate_limit(scope: str, spec_attr: str):
    """FastAPI dependency factory enforcing one named budget on a route.

    ``spec_attr`` names the Settings field holding the window, so the budget
    stays configurable per environment instead of being frozen at import.
    """

    async def _dependency(request: Request) -> None:
        spec = getattr(get_settings(), spec_attr)
        _enforce(request, scope=scope, spec=spec)

    return _dependency


def enforce_subject_limit(request: Request, *, scope: str, spec_attr: str, subject: str) -> None:
    """Apply a budget keyed on something other than the IP (e.g. the email
    being targeted), callable from inside a route handler."""
    spec = getattr(get_settings(), spec_attr)
    _enforce(request, scope=scope, spec=spec, key=subject.strip().lower())


def reset_all() -> None:
    """Test helper: clear every counter between cases."""
    limiter.clear()
    login_failures.clear()
