from __future__ import annotations

import logging
import time
from datetime import date, datetime, timezone

import google_play_scraper as gps

from gplay_radar.collectors.http_client import (
    ThrottledHttpClient,
    parse_installs,
    parse_released_date,
)
from gplay_radar.models import AppDetailRecord

logger = logging.getLogger(__name__)


class AppDetailCollector:
    """Fetches detailed Google Play app metadata (released date, realInstalls, ratings)."""

    def __init__(self, http_client: ThrottledHttpClient) -> None:
        self.http = http_client

    def fetch(
        self,
        app_id: str,
        country: str = "us",
        lang: str = "en",
        ref_date: date | None = None,
    ) -> AppDetailRecord | None:
        last_err: Exception | None = None
        for attempt in range(self.http.cfg.max_retries + 1):
            self.http.wait_slot(consume_budget=True)
            try:
                info = gps.app(app_id, lang=lang, country=country)
                released_iso, age_days = parse_released_date(info.get("released"), ref_date=ref_date)
                installs_str = str(info.get("installs") or "").strip()
                real_installs = info.get("realInstalls")
                if not isinstance(real_installs, int):
                    real_installs = parse_installs(installs_str)
                ratings = info.get("ratings")
                if not isinstance(ratings, int):
                    ratings = None
                score_val = info.get("score")
                score = round(float(score_val), 2) if isinstance(score_val, (int, float)) else None
                updated_ts = info.get("updated")
                if not isinstance(updated_ts, int):
                    updated_ts = None

                return AppDetailRecord(
                    app_id=app_id,
                    country=country,
                    lang=lang,
                    title=str(info.get("title") or "").strip(),
                    developer=str(info.get("developer") or "").strip(),
                    genre_id=str(info.get("genreId") or "").strip(),
                    released=released_iso,
                    age_days=age_days,
                    installs_str=installs_str,
                    real_installs=real_installs,
                    ratings=ratings,
                    score=score,
                    summary=str(info.get("summary") or "").strip(),
                    updated_ts=updated_ts,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
            except Exception as exc:
                last_err = exc
                # NotFoundError should not be retried repeatedly
                if "NotFound" in type(exc).__name__ or "404" in str(exc):
                    logger.info("App %s not found in %s/%s; skipping.", app_id, country, lang)
                    return None
                if attempt >= self.http.cfg.max_retries:
                    break
                backoff = (2 ** attempt) * 1.5
                logger.warning("App detail failed for %s (%s); retry in %.1fs", app_id, exc, backoff)
                time.sleep(backoff)
        logger.warning("Giving up app detail fetch for %s: %s", app_id, last_err)
        return None
