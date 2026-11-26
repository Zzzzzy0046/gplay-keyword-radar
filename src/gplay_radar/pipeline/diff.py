from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

from gplay_radar.config import ChartsConfig
from gplay_radar.models import ChartAppRecord, DarkHorseApp, KeywordCandidate, SuggestRecord
from gplay_radar.pipeline.normalize import (
    extract_phrases_from_chart_app,
    is_valid_candidate_keyword,
    normalize_keyword,
)


def detect_chart_dark_horses(
    today_charts: list[ChartAppRecord],
    prev_ranks: dict[tuple[str, str, str], int],
    charts_cfg: ChartsConfig,
    has_prev_snapshot: bool,
    blacklist: set[str] | None = None,
    lang: str = "en",
) -> list[DarkHorseApp]:
    """Identify breakout / dark-horse apps from today's Top Free & Top Grossing charts."""
    horses: list[DarkHorseApp] = []
    seen_keys: set[tuple[str, str, str]] = set()

    for app in today_charts:
        key = (app.category, app.collection, app.app_id)
        if key in seen_keys:
            continue

        prev_rank = prev_ranks.get(key) if has_prev_snapshot else None
        rank_delta = (prev_rank - app.rank) if prev_rank is not None else None
        reasons: list[str] = []

        # Signal 1: Low/mid install app ranking high (works even on Day 1 cold start!)
        is_top_zone = (app.collection == "topselling_free" and app.rank <= 50) or (
            app.collection == "topgrossing" and app.rank <= 35
        )
        if (
            is_top_zone
            and app.min_installs is not None
            and app.min_installs <= charts_cfg.dark_horse_max_installs
        ):
            reasons.append(f"低安装进前排({app.installs_str} @ #{app.rank})")

        # Signal 2: Rank surge vs previous chart snapshot
        if rank_delta is not None and rank_delta >= charts_cfg.dark_horse_rank_jump:
            reasons.append(f"榜单飙升(+{rank_delta}位: #{prev_rank}->#{app.rank})")

        # Signal 3: New entry into Top 60
        if has_prev_snapshot and prev_rank is None and app.rank <= 60:
            reasons.append(f"新进榜(#{app.rank})")

        if not reasons:
            continue

        seen_keys.add(key)
        phrases = extract_phrases_from_chart_app(
            app.title, description=app.description, blacklist=blacklist, lang=lang
        )
        horses.append(
            DarkHorseApp(
                app_id=app.app_id,
                title=app.title,
                developer=app.developer,
                category=app.category,
                collection=app.collection,
                rank=app.rank,
                prev_rank=prev_rank,
                rank_delta=rank_delta,
                installs_str=app.installs_str,
                min_installs=app.min_installs,
                reasons=reasons,
                extracted_phrases=phrases,
            )
        )

    # Sort dark horses by rank surge / low installs / high rank
    def _sort_key(h: DarkHorseApp) -> tuple[int, int, int]:
        surge_bonus = h.rank_delta if (h.rank_delta and h.rank_delta > 0) else 0
        installs_val = h.min_installs if h.min_installs is not None else 10_000_000_000
        return (-len(h.reasons), -surge_bonus, h.rank if installs_val <= 500_000 else h.rank + 40)

    horses.sort(key=_sort_key)
    return horses


def build_keyword_candidates(
    today_suggests: list[SuggestRecord],
    history_suggests: list[dict[str, Any]],
    dark_horses: list[DarkHorseApp],
    blacklist: set[str],
    lang: str = "en",
) -> tuple[str, list[KeywordCandidate]]:
    """Combine Suggest diff signals and Chart dark-horse phrases into ranked candidate list."""
    has_history = len(history_suggests) > 0
    run_mode = "diff" if has_history else "baseline"

    # Historical lookup: suggestion -> list of best daily ranks & distinct days seen
    hist_daily_best: dict[str, dict[str, int]] = defaultdict(dict)
    for row in history_suggests:
        kw = normalize_keyword(row["suggestion"])
        if not kw:
            continue
        rdate = str(row["run_date"])
        rank = int(row["rank"])
        if rdate not in hist_daily_best[kw] or rank < hist_daily_best[kw][rdate]:
            hist_daily_best[kw][rdate] = rank

    # Aggregate today's suggestions
    cand_map: dict[str, KeywordCandidate] = {}
    suggest_seeds_per_kw: dict[str, set[str]] = defaultdict(set)
    sources_per_kw: dict[str, set[str]] = defaultdict(set)

    for rec in today_suggests:
        kw = normalize_keyword(rec.suggestion)
        if not is_valid_candidate_keyword(kw, blacklist=blacklist, lang=lang):
            continue
        if kw not in cand_map:
            cand_map[kw] = KeywordCandidate(keyword=kw)
        c = cand_map[kw]
        if c.best_suggest_rank is None or rec.rank < c.best_suggest_rank:
            c.best_suggest_rank = rec.rank
        suggest_seeds_per_kw[kw].add(rec.seed_query)
        sources_per_kw[kw].add(f"suggest:{rec.group_name}")

    # Attach chart dark-horse phrases
    for dh in dark_horses:
        dh_summary = {
            "app_id": dh.app_id,
            "title": dh.title,
            "category": dh.category,
            "collection": dh.collection,
            "rank": dh.rank,
            "rank_delta": dh.rank_delta,
            "installs": dh.installs_str,
            "reasons": dh.reasons,
        }
        for phrase in dh.extracted_phrases:
            kw = normalize_keyword(phrase)
            if not is_valid_candidate_keyword(kw, blacklist=blacklist, lang=lang):
                continue
            if kw not in cand_map:
                cand_map[kw] = KeywordCandidate(keyword=kw)
            c = cand_map[kw]
            if not any(existing["app_id"] == dh.app_id for existing in c.chart_apps):
                c.chart_apps.append(dh_summary)
            sources_per_kw[kw].add(f"chart:{dh.category}")

    # Compute diff attributes and pre-enrichment ranking score
    candidates: list[KeywordCandidate] = []
    for kw, c in cand_map.items():
        c.prefix_count = len(suggest_seeds_per_kw.get(kw, set()))
        c.sources = sorted(sources_per_kw.get(kw, set()))

        prior_days = hist_daily_best.get(kw, {})
        c.seen_days_7d = len(prior_days)
        if has_history and c.best_suggest_rank is not None:
            if not prior_days:
                c.is_new_in_suggest = True
            else:
                prev_med = float(median(prior_days.values()))
                c.prev_median_suggest_rank = round(prev_med, 2)
                c.suggest_rank_delta = round(prev_med - c.best_suggest_rank, 2)

        # Pre-score determines which Top K candidates spend SERP enrichment budget
        pre = 0.0
        if run_mode == "diff":
            if c.is_new_in_suggest:
                pre += 20.0
                if c.prefix_count >= 2:
                    pre += 8.0  # anti-flapping boost when multiple prefixes confirm
            if c.suggest_rank_delta is not None and c.suggest_rank_delta > 0:
                pre += min(16.0, c.suggest_rank_delta * 3.0)
        if c.best_suggest_rank is not None:
            pre += max(1.0, 12.0 - c.best_suggest_rank)
        pre += min(10.0, c.prefix_count * 3.0)
        if c.chart_apps:
            pre += min(22.0, 12.0 + len(c.chart_apps) * 4.0)
            if c.best_suggest_rank is not None:
                pre += 8.0  # strong dual-signal convergence: Chart dark horse + Suggest confirmed!
        # Slight preference for specific 2-3 word demand queries over vague single words
        word_count = len(kw.split())
        if 2 <= word_count <= 3:
            pre += 3.0

        c.pre_score = round(pre, 2)
        candidates.append(c)

    candidates.sort(key=lambda x: (-x.pre_score, x.best_suggest_rank or 99, x.keyword))
    return run_mode, candidates
