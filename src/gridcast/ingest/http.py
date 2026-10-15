"""A polite HTTP client: identifies itself, rate-limits, retries with backoff (plan §0 rule 10)."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import structlog

log = structlog.get_logger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class FetchError(RuntimeError):
    """Raised when a request still fails after all retries."""


class PoliteClient:
    def __init__(
        self,
        user_agent: str,
        *,
        min_interval_s: float = 1.0,
        max_retries: int = 4,
        backoff_base_s: float = 2.0,
        timeout_s: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            timeout=timeout_s,
            follow_redirects=False,
            transport=transport,
        )
        self._min_interval_s = min_interval_s
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None
        self.requests_made = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _wait_turn(self) -> None:
        if self._last_request is not None:
            remaining = self._min_interval_s - (self._clock() - self._last_request)
            if remaining > 0:
                self._sleep(remaining)
        self._last_request = self._clock()

    def get(self, url: str) -> httpx.Response:
        """GET ``url``. Returns 2xx and 4xx responses (except 429); retries the rest."""
        attempt = 0
        while True:
            self._wait_turn()
            self.requests_made += 1
            try:
                response = self._client.get(url)
            except httpx.TransportError as exc:
                reason = f"{type(exc).__name__}: {exc}"
                retry_after = None
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                reason = f"HTTP {response.status_code}"
                retry_after = _retry_after_seconds(response)
            if attempt >= self._max_retries:
                raise FetchError(f"{url}: giving up after {attempt + 1} attempts ({reason})")
            delay = max(retry_after or 0.0, self._backoff_base_s * 2**attempt)
            log.warning("http_retry", url=url, reason=reason, attempt=attempt + 1, delay_s=delay)
            self._sleep(delay)
            attempt += 1


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Seconds from a numeric ``Retry-After`` header (HTTP-date form is ignored)."""
    value = response.headers.get("Retry-After", "")
    return float(value) if value.isdigit() else None
