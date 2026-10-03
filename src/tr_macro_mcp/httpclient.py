"""Polite HTTP client: one User-Agent, per-host request spacing, retries with backoff."""

import logging
import time
from collections.abc import Mapping

import anyio
import httpx

from tr_macro_mcp.config import USER_AGENT
from tr_macro_mcp.errors import UpstreamError

log = logging.getLogger(__name__)

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class PoliteClient:
    def __init__(
        self,
        *,
        min_interval: float,
        timeout: float,
        max_attempts: int = 3,
        backoff_base: float = 1.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._min_interval = min_interval
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._host_locks: dict[str, anyio.Lock] = {}
        self._last_request: dict[str, float] = {}

    async def get(self, url: str, headers: Mapping[str, str] | None = None) -> httpx.Response:
        """GET with retries. Returns any final response (including 4xx); raises on exhaustion."""
        host = httpx.URL(url).host
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await self._spaced_get(host, url, headers)
            except httpx.TransportError as exc:
                last_error = exc
                log.warning("GET %s failed (attempt %d): %s", host, attempt, exc)
            else:
                if response.status_code not in RETRY_STATUSES:
                    return response
                last_error = UpstreamError(f"HTTP {response.status_code} from {host}")
                log.warning("GET %s -> %d (attempt %d)", host, response.status_code, attempt)
            if attempt < self._max_attempts:
                await anyio.sleep(self._backoff_base * 2 ** (attempt - 1))
        raise UpstreamError(f"{host} unreachable after {self._max_attempts} attempts: {last_error}")

    async def _spaced_get(
        self, host: str, url: str, headers: Mapping[str, str] | None
    ) -> httpx.Response:
        lock = self._host_locks.setdefault(host, anyio.Lock())
        async with lock:
            wait = self._last_request.get(host, 0.0) + self._min_interval - time.monotonic()
            if wait > 0:
                await anyio.sleep(wait)
            try:
                return await self._client.get(url, headers=headers)
            finally:
                self._last_request[host] = time.monotonic()

    async def aclose(self) -> None:
        await self._client.aclose()
