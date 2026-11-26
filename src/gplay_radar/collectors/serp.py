from __future__ import annotations

import logging
import time

import google_play_scraper as gps

from gplay_radar.collectors.http_client import ThrottledHttpClient, parse_installs
from gplay_radar.models import SerpAppRecord

logger = logging.getLogger(__name__)


class SerpCollector:
    """Fetches Google Play search results (SERP Top N) using shared rate limiting."""

    def __init__(self, http_client: ThrottledHttpClient) -> None:
        self.http = http_client

    def search(
        self,
        keyword: str,
        country: str = "us",
        lang: str = "en",
        top_n: int = 10,
    ) -> list[SerpAppRecord]:
        last_err: Exception | None = None
        for attempt in range(self.http.cfg.max_retries + 1):
            self.http.wait_slot(consume_budget=True)
            try:
                raw_results = gps.search(keyword, lang=lang, country=country, n_hits=top_n)
                records: list[SerpAppRecord] = []
                for item in raw_results:
                    if len(records) >= top_n:
                        break
                    app_id = str(item.get("appId") or "").strip()
                    title = str(item.get("title") or "").strip()
                    if not app_id or not title:
                        continue
                    installs_str = str(item.get("installs") or "").strip()
                    score_val = item.get("score")
                    records.append(
                        SerpAppRecord(
                            keyword=keyword,
                            rank=len(records) + 1,
                            app_id=app_id,
                            title=title,
                            developer=str(item.get("developer") or "").strip(),
                            installs_str=installs_str,
                            min_installs=parse_installs(installs_str),
                            score=round(float(score_val), 2) if isinstance(score_val, (int, float)) else None,
                        )
                    )
                return records
            except Exception as exc:
                last_err = exc
                if attempt >= self.http.cfg.max_retries:
                    break
                backoff = (2 ** attempt) * 1.5
                logger.warning("SERP search failed for %r (%s); retry in %.1fs", keyword, exc, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"SERP search failed for {keyword!r}: {last_err}")
