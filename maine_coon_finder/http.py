"""A polite HTTP client: one shared session, a minimum gap between requests
to the same host, and back-off when a site says slow down."""

from __future__ import annotations

import logging
import threading
import time
from urllib.parse import urlparse

import httpx

log = logging.getLogger(__name__)


class BlockedError(RuntimeError):
    """The site refused us (403/429/captcha). The source backs off."""


class PoliteClient:
    def __init__(self, user_agent: str, min_gap_seconds: float = 2.0, timeout: float = 25.0):
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept-Language": "en-GB,en;q=0.9",
            },
            follow_redirects=True,
            timeout=timeout,
        )
        self._gap = min_gap_seconds
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def _wait_turn(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._last.get(host, 0.0) + self._gap - now
            self._last[host] = max(now, self._last.get(host, 0.0) + self._gap)
        if wait > 0:
            time.sleep(wait)

    def get(self, url: str, **kwargs) -> httpx.Response:
        host = urlparse(url).netloc
        self._wait_turn(host)
        resp = self._client.get(url, **kwargs)
        if resp.status_code in (403, 429, 503):
            raise BlockedError(f"{host} answered {resp.status_code} for {url}")
        resp.raise_for_status()
        return resp

    def get_bytes(self, url: str, max_bytes: int = 8_000_000) -> tuple[bytes, str]:
        resp = self.get(url)
        data = resp.content
        if len(data) > max_bytes:
            # A cut-off image is a broken image: better to skip it.
            raise ValueError(f"image too large ({len(data)} bytes)")
        ctype = resp.headers.get("content-type", "image/jpeg").split(";")[0].strip()
        return data, ctype

    def close(self) -> None:
        self._client.close()
