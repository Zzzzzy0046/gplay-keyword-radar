from __future__ import annotations

import logging
import os
import random
import re
import time
from datetime import date, datetime
from typing import Any

import httpx

from gplay_radar.config import NetworkConfig

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class RequestBudgetExceeded(RuntimeError):
    """Raised when daily HTTP request budget is reached."""


class SchemaChangedError(RuntimeError):
    """Raised when an upstream public payload no longer matches expected schema."""


def parse_installs(installs_str: str | None) -> int | None:
    if not installs_str:
        return None
    digits = re.sub(r"[^\d]", "", str(installs_str))
    if not digits:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def parse_released_date(released_str: str | None, ref_date: date | None = None) -> tuple[str | None, int | None]:
    """Parse Google Play released strings like 'May 31, 2017' into (YYYY-MM-DD, age_days)."""
    if not released_str:
        return None, None
    cleaned = str(released_str).strip()
    ref = ref_date or date.today()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%d %b %Y"):
        try:
            parsed = datetime.strptime(cleaned, fmt).date()
            age = max(0, (ref - parsed).days)
            return parsed.isoformat(), age
        except ValueError:
            continue
    return cleaned, None


def ensure_env_proxy(proxy: str | None) -> None:
    """Propagate configured proxy into env vars so urllib-based libraries also use it."""
    if proxy:
        os.environ.setdefault("HTTP_PROXY", proxy)
        os.environ.setdefault("HTTPS_PROXY", proxy)


class ThrottledHttpClient:
    """Shared throttled HTTP client with jitter, retry/backoff, and daily request budget."""

    def __init__(self, network_cfg: NetworkConfig, proxy: str | None = None) -> None:
        self.cfg = network_cfg
        self.proxy = proxy
        self.requests_used = 0
        self._last_request_ts = 0.0
        ensure_env_proxy(proxy)
        client_kwargs: dict[str, Any] = {
            "headers": dict(DEFAULT_HEADERS),
            "timeout": network_cfg.timeout_seconds,
            "follow_redirects": True,
        }
        if proxy:
            client_kwargs["proxy"] = proxy
        self._client = httpx.Client(**client_kwargs)

    @property
    def remaining_budget(self) -> int:
        return max(0, self.cfg.daily_request_budget - self.requests_used)

    def has_budget(self, count: int = 1) -> bool:
        return (self.requests_used + count) <= self.cfg.daily_request_budget

    def wait_slot(self, consume_budget: bool = True) -> None:
        if consume_budget:
            if self.requests_used >= self.cfg.daily_request_budget:
                raise RequestBudgetExceeded(
                    f"Daily request budget ({self.cfg.daily_request_budget}) reached."
                )
            self.requests_used += 1

        now = time.monotonic()
        if self._last_request_ts > 0:
            delay = random.uniform(self.cfg.min_delay_seconds, self.cfg.max_delay_seconds)
            elapsed = now - self._last_request_ts
            if elapsed < delay:
                time.sleep(delay - elapsed)
        self._last_request_ts = time.monotonic()

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        client: httpx.Client | None = None,
        allow_429_no_retry: bool = False,
    ) -> httpx.Response:
        active_client = client or self._client
        merged_headers = dict(headers or {})
        if extra_headers:
            merged_headers.update(extra_headers)

        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries + 1):
            self.wait_slot(consume_budget=True)
            try:
                resp = active_client.request(
                    method,
                    url,
                    params=params,
                    data=data,
                    headers=merged_headers or None,
                )
                if resp.status_code == 429:
                    if allow_429_no_retry or attempt >= self.cfg.max_retries:
                        return resp
                    backoff = (2 ** attempt) * 2.0 + random.uniform(0.5, 1.5)
                    logger.warning("HTTP 429 on %s; backing off %.1fs (attempt %d)", url, backoff, attempt + 1)
                    time.sleep(backoff)
                    continue
                if resp.status_code >= 500 and attempt < self.cfg.max_retries:
                    backoff = (2 ** attempt) * 1.2 + random.uniform(0.2, 0.8)
                    logger.warning("HTTP %d on %s; retrying in %.1fs", resp.status_code, url, backoff)
                    time.sleep(backoff)
                    continue
                return resp
            except httpx.HTTPError as exc:
                last_err = exc
                if attempt >= self.cfg.max_retries:
                    raise
                backoff = (2 ** attempt) * 1.2 + random.uniform(0.2, 0.8)
                logger.warning("Network error on %s (%s); retrying in %.1fs", url, exc, backoff)
                time.sleep(backoff)
        if last_err:
            raise last_err
        raise RuntimeError(f"Unexpected request failure for {url}")

    def close(self) -> None:
        self._client.close()
