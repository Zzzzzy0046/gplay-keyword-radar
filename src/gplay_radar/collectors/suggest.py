from __future__ import annotations

import json
import logging
import string
from datetime import date

from gplay_radar.collectors.http_client import SchemaChangedError, ThrottledHttpClient
from gplay_radar.config import SuggestConfig

logger = logging.getLogger(__name__)

PLAY_BATCHEXECUTE_URL = "https://play.google.com/_/PlayStoreUi/data/batchexecute"


def parse_suggest_response(text: str) -> list[str]:
    """Parse Google Play IJ4APc batchexecute suggest payload into ordered suggestions."""
    marker = text.find("[[")
    if marker < 0:
        raise SchemaChangedError("Suggest response missing JSON envelope marker '[['")
    try:
        outer = json.loads(text[marker:])
    except json.JSONDecodeError as exc:
        raise SchemaChangedError(f"Suggest outer JSON decode error: {exc}") from exc

    if not isinstance(outer, list) or not outer or not isinstance(outer[0], list) or len(outer[0]) < 3:
        raise SchemaChangedError("Suggest outer JSON envelope shape changed")

    raw_inner = outer[0][2]
    if raw_inner is None:
        return []

    try:
        inner = json.loads(raw_inner)
    except (TypeError, json.JSONDecodeError) as exc:
        raise SchemaChangedError(f"Suggest inner JSON decode error: {exc}") from exc

    if inner is None:
        return []

    try:
        entries = inner[0][0]
    except (IndexError, TypeError) as exc:
        raise SchemaChangedError(f"Suggest inner array structure changed: {exc}") from exc

    if not entries:
        return []

    results: list[str] = []
    seen: set[str] = set()
    for item in entries:
        if isinstance(item, list) and item and isinstance(item[0], str):
            kw = item[0].strip().lower()
            if kw and kw not in seen:
                seen.add(kw)
                results.append(kw)
    return results


def select_daily_az_letters(run_date: date, letters_per_day: int) -> list[str]:
    """Deterministically rotate through a-z letters by day-of-year."""
    if letters_per_day <= 0:
        return []
    alphabet = string.ascii_lowercase
    if letters_per_day >= len(alphabet):
        return list(alphabet)
    day_idx = run_date.timetuple().tm_yday
    start = (day_idx * letters_per_day) % len(alphabet)
    return [alphabet[(start + i) % len(alphabet)] for i in range(letters_per_day)]


def build_seed_queries(suggest_cfg: SuggestConfig, run_date: date) -> list[tuple[str, str]]:
    """Build deduplicated list of (seed_query, group_name) for a daily suggest sweep."""
    letters = select_daily_az_letters(run_date, suggest_cfg.az_letters_per_day)
    queries: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(q: str, group: str) -> None:
        norm = " ".join(q.strip().lower().split())
        if norm and norm not in seen:
            seen.add(norm)
            queries.append((norm, group))

    for grp in suggest_cfg.seed_groups:
        for seed in grp.seeds:
            _add(seed, grp.name)
            for prefix in suggest_cfg.intent_prefixes:
                if not seed.startswith(f"{prefix} ") and seed != prefix:
                    _add(f"{prefix} {seed}", grp.name)
            if grp.expand_az:
                for ch in letters:
                    _add(f"{seed} {ch}", grp.name)
    return queries


class SuggestCollector:
    """Fetches native Google Play search autocomplete via batchexecute RPC IJ4APc."""

    def __init__(self, http_client: ThrottledHttpClient) -> None:
        self.http = http_client

    def fetch(self, term: str, country: str = "us", lang: str = "en") -> list[str]:
        inner = json.dumps([[None, [term], [10], [2], 4]], separators=(",", ":"))
        freq = json.dumps([[["IJ4APc", inner]]], separators=(",", ":"))
        resp = self.http.request(
            "POST",
            PLAY_BATCHEXECUTE_URL,
            params={
                "rpcids": "IJ4APc",
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
            raise RuntimeError(f"Suggest RPC returned HTTP {resp.status_code} for term={term!r}")
        return parse_suggest_response(resp.text)
