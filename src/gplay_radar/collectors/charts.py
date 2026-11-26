from __future__ import annotations

import html
import json
import logging
import re

from gplay_radar.collectors.http_client import (
    SchemaChangedError,
    ThrottledHttpClient,
    parse_installs,
)
from gplay_radar.models import ChartAppRecord

logger = logging.getLogger(__name__)

PLAY_BATCHEXECUTE_URL = "https://play.google.com/_/PlayStoreUi/data/batchexecute"

# Field projection codes verified live against Google Play vyAe2 RPC
_VYA_FIELD_MASK = [
    64, 1, 195, 71, 8, 72, 9, 10, 11, 139, 12, 16, 145, 148, 150, 151, 152,
    27, 30, 31, 96, 32, 34, 163, 100, 165, 104, 169, 108, 110, 113, 55, 56, 57, 122,
]


def _clean_html_text(raw: str | None) -> str:
    if not raw:
        return ""
    text = re.sub(r"<br\s*/?>", " ", str(raw), flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def parse_charts_response(text: str, category: str, collection: str) -> list[ChartAppRecord]:
    """Parse Google Play vyAe2 batchexecute top-chart response into ChartAppRecord list."""
    marker = text.find("[[")
    if marker < 0:
        raise SchemaChangedError("Charts response missing JSON envelope marker '[['")
    try:
        outer = json.loads(text[marker:])
        raw_inner = outer[0][2]
        if raw_inner is None:
            return []
        inner = json.loads(raw_inner)
    except Exception as exc:
        raise SchemaChangedError(f"Charts JSON decode failed: {exc}") from exc

    try:
        items = inner[0][1][0][28][0]
    except (IndexError, TypeError) as exc:
        raise SchemaChangedError(f"Charts vyAe2 items path changed: {exc}") from exc

    if not isinstance(items, list):
        raise SchemaChangedError("Charts vyAe2 items is not a list")

    records: list[ChartAppRecord] = []
    for idx, wrapper in enumerate(items, start=1):
        try:
            it = wrapper[0]
            app_id = str(it[0][0]).strip()
            title = str(it[3]).strip()
            score = None
            if len(it) > 4 and isinstance(it[4], list) and len(it[4]) > 1 and isinstance(it[4][1], (int, float)):
                score = round(float(it[4][1]), 2)
            description = ""
            if len(it) > 13 and isinstance(it[13], list) and len(it[13]) > 1 and it[13][1]:
                description = _clean_html_text(it[13][1])[:500]
            developer = str(it[14]).strip() if len(it) > 14 and it[14] else ""
            installs_str = str(it[15]).strip() if len(it) > 15 and it[15] else ""
            if not app_id or not title:
                continue
            records.append(
                ChartAppRecord(
                    category=category,
                    collection=collection,
                    rank=idx,
                    app_id=app_id,
                    title=title,
                    developer=developer,
                    installs_str=installs_str,
                    min_installs=parse_installs(installs_str),
                    score=score,
                    description=description,
                )
            )
        except (IndexError, TypeError) as exc:
            logger.debug("Skipping malformed chart item at rank %d: %s", idx, exc)
            continue
    return records


class ChartsCollector:
    """Fetches Google Play Top Free / Top Grossing category charts via vyAe2 RPC."""

    def __init__(self, http_client: ThrottledHttpClient) -> None:
        self.http = http_client

    def fetch_chart(
        self,
        category: str,
        collection: str = "topselling_free",
        count: int = 100,
        country: str = "us",
        lang: str = "en",
    ) -> list[ChartAppRecord]:
        inner = json.dumps(
            [
                [
                    None,
                    [[8, [20, count]], True, None, _VYA_FIELD_MASK, [None, None, []]],
                    [2, collection, category],
                ]
            ],
            separators=(",", ":"),
        )
        freq = json.dumps([[["vyAe2", inner, None, "generic"]]], separators=(",", ":"))
        resp = self.http.request(
            "POST",
            PLAY_BATCHEXECUTE_URL,
            params={
                "rpcids": "vyAe2",
                "hl": lang,
                "gl": country,
                "authuser": "",
                "soc-app": "121",
                "soc-platform": "1",
                "soc-device": "1",
            },
            data={"f.req": freq},
            extra_headers={
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
                "Origin": "https://play.google.com",
                "Referer": "https://play.google.com/",
            },
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Charts RPC returned HTTP {resp.status_code} for {collection}/{category}"
            )
        return parse_charts_response(resp.text, category=category, collection=collection)
