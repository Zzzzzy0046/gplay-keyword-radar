from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from gplay_radar.collectors.http_client import DEFAULT_HEADERS, SchemaChangedError, ThrottledHttpClient
from gplay_radar.models import TrendsRecord

logger = logging.getLogger(__name__)

TRENDS_WARMUP_URL = "https://trends.google.com/trends/explore"
TRENDS_EXPLORE_URL = "https://trends.google.com/trends/api/explore"
TRENDS_MULTILINE_URL = "https://trends.google.com/trends/api/widgetdata/multiline"


def _strip_xssi_json(text: str) -> dict[str, Any]:
    brace = text.find("{")
    if brace < 0:
        raise SchemaChangedError("Google Trends response missing JSON object")
    try:
        return json.loads(text[brace:])
    except json.JSONDecodeError as exc:
        raise SchemaChangedError(f"Google Trends JSON decode failed: {exc}") from exc


def parse_trends_multiline(text: str, keyword: str) -> TrendsRecord:
    """Parse Google Trends multiline response for a single self-normalized keyword."""
    payload = _strip_xssi_json(text)
    points = payload.get("default", {}).get("timelineData")
    if not isinstance(points, list) or not points:
        return TrendsRecord(
            keyword=keyword,
            avg_7d=None,
            avg_prev21d=None,
            slope_pct=None,
            latest_value=None,
            peak_value=None,
            series=[],
            status="empty",
        )

    series: list[dict[str, Any]] = []
    values: list[int] = []
    for pt in points:
        val_arr = pt.get("value") or []
        val = int(val_arr[0]) if val_arr else 0
        # Ignore incomplete partial trailing day if Google flags it and we have plenty of points
        series.append(
            {
                "time": str(pt.get("formattedAxisTime") or pt.get("formattedTime") or ""),
                "value": val,
                "is_partial": bool(pt.get("isPartial", False)),
            }
        )
        values.append(val)

    complete_vals = [p["value"] for p in series if not p["is_partial"]] or values
    recent_window = complete_vals[-7:] if len(complete_vals) >= 7 else complete_vals
    prev_window = complete_vals[:-7][-21:] if len(complete_vals) > 7 else []

    avg_7d = round(sum(recent_window) / len(recent_window), 2) if recent_window else 0.0
    avg_prev21d = round(sum(prev_window) / len(prev_window), 2) if prev_window else avg_7d
    denom = max(avg_prev21d, 5.0)
    slope_pct = round(((avg_7d - avg_prev21d) / denom) * 100.0, 1)

    return TrendsRecord(
        keyword=keyword,
        avg_7d=avg_7d,
        avg_prev21d=avg_prev21d,
        slope_pct=slope_pct,
        latest_value=complete_vals[-1] if complete_vals else None,
        peak_value=max(complete_vals) if complete_vals else None,
        series=series[-14:],  # store compact trailing 14d points
        status="ok",
    )


class TrendsCollector:
    """Fetches 30d daily Google Trends interest for high-potential keywords with graceful 429 degradation."""

    def __init__(self, http_client: ThrottledHttpClient, timeframe: str = "today 1-m") -> None:
        self.http = http_client
        self.timeframe = timeframe
        kwargs: dict[str, Any] = {
            "headers": dict(DEFAULT_HEADERS),
            "timeout": http_client.cfg.timeout_seconds,
            "follow_redirects": True,
        }
        if http_client.proxy:
            kwargs["proxy"] = http_client.proxy
        self._tclient = httpx.Client(**kwargs)
        self._warmed_up = False
        self._consecutive_429 = 0
        self.degraded = False

    def _warmup(self, country: str) -> None:
        if self._warmed_up:
            return
        self._warmed_up = True
        try:
            # Even when warmup returns 429, Google still sets the NID cookie required by /api/explore
            self.http.request(
                "GET",
                TRENDS_WARMUP_URL,
                params={"geo": country.upper(), "q": "apps"},
                client=self._tclient,
                allow_429_no_retry=True,
            )
        except Exception as exc:
            logger.warning("Trends cookie warmup warning: %s", exc)

    def fetch_keyword(self, keyword: str, country: str = "us", lang: str = "en") -> TrendsRecord:
        if self.degraded or self._consecutive_429 >= 2:
            self.degraded = True
            return TrendsRecord(
                keyword=keyword,
                avg_7d=None,
                avg_prev21d=None,
                slope_pct=None,
                latest_value=None,
                peak_value=None,
                status="unavailable",
            )

        self._warmup(country)
        hl = f"{lang}-{country.upper()}"
        req_payload = json.dumps(
            {
                "comparisonItem": [
                    {"keyword": keyword, "geo": country.upper(), "time": self.timeframe}
                ],
                "category": 0,
                "property": "",
            },
            separators=(",", ":"),
        )
        try:
            r1 = self.http.request(
                "GET",
                TRENDS_EXPLORE_URL,
                params={"hl": hl, "tz": "0", "req": req_payload},
                client=self._tclient,
                allow_429_no_retry=True,
            )
            if r1.status_code == 429:
                self._consecutive_429 += 1
                if self._consecutive_429 >= 2:
                    self.degraded = True
                logger.warning("Google Trends explore returned 429 for %r", keyword)
                return TrendsRecord(keyword, None, None, None, None, None, status="unavailable")
            if r1.status_code != 200:
                logger.warning("Google Trends explore HTTP %d for %r", r1.status_code, keyword)
                return TrendsRecord(keyword, None, None, None, None, None, status="unavailable")

            explore_data = _strip_xssi_json(r1.text)
            ts_widgets = [w for w in explore_data.get("widgets", []) if w.get("id") == "TIMESERIES"]
            if not ts_widgets:
                return TrendsRecord(keyword, None, None, None, None, None, status="empty")

            ts_widget = ts_widgets[0]
            r2 = self.http.request(
                "GET",
                TRENDS_MULTILINE_URL,
                params={
                    "hl": hl,
                    "tz": "0",
                    "req": json.dumps(ts_widget["request"], separators=(",", ":")),
                    "token": ts_widget["token"],
                },
                client=self._tclient,
                allow_429_no_retry=True,
            )
            if r2.status_code == 429:
                self._consecutive_429 += 1
                if self._consecutive_429 >= 2:
                    self.degraded = True
                logger.warning("Google Trends multiline returned 429 for %r", keyword)
                return TrendsRecord(keyword, None, None, None, None, None, status="unavailable")
            if r2.status_code != 200:
                return TrendsRecord(keyword, None, None, None, None, None, status="unavailable")

            self._consecutive_429 = 0
            return parse_trends_multiline(r2.text, keyword=keyword)
        except Exception as exc:
            logger.warning("Google Trends failed for %r: %s", keyword, exc)
            return TrendsRecord(keyword, None, None, None, None, None, status="unavailable")

    def close(self) -> None:
        self._tclient.close()
