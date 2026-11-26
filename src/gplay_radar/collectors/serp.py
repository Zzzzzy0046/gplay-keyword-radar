from __future__ import annotations

import json
import logging
from typing import Any

from google_play_scraper.constants.element import ElementSpecs
from google_play_scraper.constants.regex import Regex

from gplay_radar.collectors.http_client import ThrottledHttpClient, parse_installs
from gplay_radar.models import SerpAppRecord

logger = logging.getLogger(__name__)

PLAY_SEARCH_URL = "https://play.google.com/store/search"


def parse_serp_html(html_text: str, keyword: str, top_n: int = 10) -> list[SerpAppRecord]:
    """Parse Google Play search HTML robustly across US, EU (DE/FR/ES), LATAM, MENA & TR."""
    dataset: dict[str, Any] = {}
    for match in Regex.SCRIPT.findall(html_text):
        key_match = Regex.KEY.findall(match)
        val_match = Regex.VALUE.findall(match)
        if key_match and val_match:
            try:
                dataset[key_match[0]] = json.loads(val_match[0])
            except json.JSONDecodeError:
                continue

    raw_items: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def _append_item(extracted: dict[str, Any]) -> None:
        app_id = str(extracted.get("appId") or "").strip()
        title = str(extracted.get("title") or "").strip()
        if not app_id or not title or app_id in seen_ids:
            return
        seen_ids.add(app_id)
        raw_items.append(extracted)

    # Scan ds:4 first (most common), then any other ds:* block dynamically
    ordered_keys = (["ds:4"] if "ds:4" in dataset else []) + [
        k for k in dataset.keys() if k != "ds:4"
    ]

    for key in ordered_keys:
        block = dataset.get(key)
        if not (
            isinstance(block, list)
            and block
            and isinstance(block[0], list)
            and len(block[0]) > 1
            and isinstance(block[0][1], list)
        ):
            continue

        sections = block[0][1]
        for sec in sections:
            if not isinstance(sec, list):
                continue
            # Optional highlighted top card (may be None on EU/DMA layouts)
            if (
                len(sec) > 23
                and isinstance(sec[23], list)
                and len(sec[23]) > 16
                and sec[23][16] is not None
            ):
                try:
                    top = {
                        f: spec.extract_content(sec[23][16])
                        for f, spec in ElementSpecs.SearchResultOnTop.items()
                    }
                    _append_item(top)
                except Exception:
                    pass

            # Standard search results grid
            if (
                len(sec) > 22
                and isinstance(sec[22], list)
                and sec[22]
                and isinstance(sec[22][0], list)
            ):
                for entry in sec[22][0]:
                    if len(raw_items) >= top_n:
                        break
                    try:
                        extracted = {
                            f: spec.extract_content(entry)
                            for f, spec in ElementSpecs.SearchResult.items()
                        }
                        _append_item(extracted)
                    except Exception:
                        continue
            if len(raw_items) >= top_n:
                break
        if raw_items:
            break

    records: list[SerpAppRecord] = []
    for item in raw_items[:top_n]:
        installs_str = str(item.get("installs") or "").strip()
        score_val = item.get("score")
        records.append(
            SerpAppRecord(
                keyword=keyword,
                rank=len(records) + 1,
                app_id=str(item["appId"]).strip(),
                title=str(item["title"]).strip(),
                developer=str(item.get("developer") or "").strip(),
                installs_str=installs_str,
                min_installs=parse_installs(installs_str),
                score=round(float(score_val), 2) if isinstance(score_val, (int, float)) else None,
            )
        )
    return records


class SerpCollector:
    """Fetches Google Play search results (SERP Top N) directly via throttled httpx."""

    def __init__(self, http_client: ThrottledHttpClient) -> None:
        self.http = http_client

    def search(
        self,
        keyword: str,
        country: str = "us",
        lang: str = "en",
        top_n: int = 10,
    ) -> list[SerpAppRecord]:
        resp = self.http.request(
            "GET",
            PLAY_SEARCH_URL,
            params={"q": keyword, "c": "apps", "hl": lang, "gl": country},
            extra_headers={"Accept-Language": f"{lang}-{country.upper()},{lang};q=0.9,en;q=0.8"},
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"SERP HTTP {resp.status_code} for keyword={keyword!r} ({country}/{lang})"
            )
        return parse_serp_html(resp.text, keyword=keyword, top_n=top_n)
