from __future__ import annotations

from typing import Any

from gplay_radar.config import ScoringConfig
from gplay_radar.models import (
    AppDetailRecord,
    KeywordCandidate,
    KeywordScoreRecord,
    SerpAppRecord,
    TrendsRecord,
)
from gplay_radar.pipeline.normalize import (
    GENERIC_FUNCTIONAL_NOUNS,
    STOPWORDS,
    extract_brand_prefix,
    matches_static_blacklist,
    normalize_keyword,
)


def detect_brand_keyword(
    keyword: str,
    serp_apps: list[SerpAppRecord],
    details_map: dict[str, AppDetailRecord],
    blacklist: set[str],
    scoring_cfg: ScoringConfig,
) -> tuple[bool, str]:
    """Identify brand-dominated keywords using static blacklist + SERP dominance heuristic."""
    norm_kw = normalize_keyword(keyword)
    matched_static = matches_static_blacklist(norm_kw, blacklist)
    if matched_static:
        return True, f"静态品牌词典命中({matched_static})"

    if not serp_apps:
        return False, ""

    top1 = serp_apps[0]
    top1_detail = details_map.get(top1.app_id)
    top1_brand = extract_brand_prefix(top1.title)
    top1_dev = normalize_keyword(top1.developer)

    kw_matches_top1_brand = bool(
        top1_brand
        and len(top1_brand) >= 4
        and (norm_kw == top1_brand or norm_kw.startswith(f"{top1_brand} ") or top1_brand == norm_kw.split()[0])
    )
    kw_matches_top1_dev = bool(
        top1_dev
        and len(top1_dev) >= 4
        and (norm_kw == top1_dev or norm_kw.startswith(f"{top1_dev} "))
    )

    if not (kw_matches_top1_brand or kw_matches_top1_dev):
        return False, ""

    # Multi-word queries ending in a generic category noun (e.g. "document reader", "step tracker")
    # are usually category demand terms even when #1 app literally named itself after that category.
    norm_tokens = norm_kw.split()
    if (
        len(norm_tokens) >= 2
        and norm_tokens[-1] in GENERIC_FUNCTIONAL_NOUNS
        and not kw_matches_top1_dev
    ):
        # Only treat as brand if the leading word looks like a coined/unique brand (absent from other Top 10 titles)
        lead_tok = norm_tokens[0]
        others_with_lead = sum(
            1 for app in serp_apps[1:10] if lead_tok in normalize_keyword(app.title).split()
        )
        if others_with_lead >= 2:
            return False, ""

    # Count how many OTHER top-10 apps use the keyword's core tokens in their title
    kw_tokens = [t for t in norm_kw.split() if t not in STOPWORDS and len(t) >= 3]
    other_title_matches = 0
    for app in serp_apps[1:10]:
        t_norm = normalize_keyword(app.title)
        if kw_tokens and all(tok in t_norm for tok in kw_tokens):
            other_title_matches += 1

    real_installs = (
        top1_detail.real_installs
        if (top1_detail and top1_detail.real_installs is not None)
        else top1.min_installs
    )
    ratings = top1_detail.ratings if top1_detail else None

    is_giant = bool(
        (real_installs is not None and real_installs >= scoring_cfg.brand_giant_installs)
        or (ratings is not None and ratings >= scoring_cfg.brand_giant_ratings)
    )

    # If the keyword matches #1 app's brand/dev AND almost no other competitors use it in title -> brand query
    if other_title_matches <= 1:
        return True, f"SERP #1 独占品牌词({top1.title[:28]})"
    if is_giant and other_title_matches <= 2:
        return True, f"SERP #1 巨头品牌词({top1.title[:28]})"

    return False, ""


def compute_momentum_score(candidate: KeywordCandidate, run_mode: str) -> tuple[float, list[str]]:
    """Score 0-40 for keyword momentum / discovery heat."""
    score = 0.0
    notes: list[str] = []

    if run_mode == "diff":
        if candidate.is_new_in_suggest:
            pts = 18.0 if candidate.prefix_count >= 2 else 14.0
            score += pts
            notes.append("联想新冒头")
        elif candidate.suggest_rank_delta is not None and candidate.suggest_rank_delta >= 1.0:
            pts = min(14.0, candidate.suggest_rank_delta * 3.0)
            score += pts
            notes.append(f"联想位上升+{candidate.suggest_rank_delta:.1f}")
        elif candidate.best_suggest_rank is not None and candidate.best_suggest_rank <= 3:
            score += 6.0
            notes.append(f"联想前排#{candidate.best_suggest_rank}")
    else:
        # Baseline (Day 1 cold start): rank & multi-prefix strength
        if candidate.best_suggest_rank is not None:
            pts = max(3.0, 15.0 - candidate.best_suggest_rank * 1.5)
            score += pts
            notes.append(f"联想占位#{candidate.best_suggest_rank}")

    if candidate.prefix_count >= 2:
        pts = min(8.0, candidate.prefix_count * 2.5)
        score += pts
        notes.append(f"{candidate.prefix_count}个词根共现")

    if candidate.chart_apps:
        chart_pts = min(16.0, 8.0 + len(candidate.chart_apps) * 4.0)
        has_surge = any((a.get("rank_delta") or 0) >= 15 for a in candidate.chart_apps)
        if has_surge:
            chart_pts = min(18.0, chart_pts + 4.0)
        score += chart_pts
        notes.append(f"关联{len(candidate.chart_apps)}款榜单黑马")

    return round(min(40.0, score), 1), notes


def compute_entry_score(
    keyword: str,
    serp_apps: list[SerpAppRecord],
    details_map: dict[str, AppDetailRecord],
    scoring_cfg: ScoringConfig,
) -> tuple[float, dict[str, Any]]:
    """Score 0-45 for competition openness / entry opportunity from SERP Top 10."""
    if not serp_apps:
        return 0.0, {"serp_count": 0}

    n = len(serp_apps)
    top5 = serp_apps[:5]
    norm_kw = normalize_keyword(keyword)

    new_apps_365 = 0
    fresh_apps_180 = 0
    known_age_count = 0
    small_apps_top10 = 0
    small_apps_top5 = 0
    giants_top5 = 0
    exact_title_hits = 0

    for idx, app in enumerate(serp_apps, start=1):
        detail = details_map.get(app.app_id)
        age = detail.age_days if detail else None
        installs = (
            detail.real_installs
            if (detail and detail.real_installs is not None)
            else app.min_installs
        )
        if age is not None:
            known_age_count += 1
            if age <= scoring_cfg.new_app_age_days:
                new_apps_365 += 1
            if age <= scoring_cfg.fresh_app_age_days:
                fresh_apps_180 += 1
        if installs is not None:
            if installs < scoring_cfg.small_app_max_installs:
                small_apps_top10 += 1
                if idx <= 5:
                    small_apps_top5 += 1
            if idx <= 5 and installs >= scoring_cfg.giant_app_min_installs:
                giants_top5 += 1
        if norm_kw and norm_kw in normalize_keyword(app.title):
            exact_title_hits += 1

    # 1. New entrant share (0-16 pts)
    age_denom = max(known_age_count, 1)
    new_share = new_apps_365 / age_denom
    fresh_share = fresh_apps_180 / age_denom
    new_pts = min(16.0, new_share * 14.0 + fresh_share * 8.0) if known_age_count > 0 else 4.0

    # 2. Small/mid app presence (0-14 pts)
    small_share_10 = small_apps_top10 / n
    small_share_5 = small_apps_top5 / max(len(top5), 1)
    small_pts = min(14.0, small_share_10 * 10.0 + small_share_5 * 6.0)

    # 3. Non-monopoly headroom in Top 5 (0-8 pts)
    non_giant_ratio = 1.0 - (giants_top5 / max(len(top5), 1))
    headroom_pts = non_giant_ratio * 8.0

    # 4. ASO supply gap (0-7 pts): fewer exact-phrase title matches means easier metadata ranking
    exact_ratio = exact_title_hits / n
    gap_pts = max(0.0, (1.0 - exact_ratio) * 7.0)

    total_entry = round(min(45.0, new_pts + small_pts + headroom_pts + gap_pts), 1)
    metrics = {
        "serp_count": n,
        "new_apps_365d": new_apps_365,
        "fresh_apps_180d": fresh_apps_180,
        "small_apps_top10": small_apps_top10,
        "small_apps_top5": small_apps_top5,
        "giants_top5": giants_top5,
        "exact_title_hits": exact_title_hits,
    }
    return total_entry, metrics


def compute_trends_score(trends: TrendsRecord | None) -> tuple[float, str]:
    """Score 0-15 for Google Trends 7d vs 21d slope (neutral 5.0 when degraded/unavailable)."""
    if trends is None or trends.status == "unavailable":
        return 5.0, "unavailable"
    if trends.status == "empty" or trends.slope_pct is None:
        return 4.0, "low_volume"

    slope = trends.slope_pct
    if slope >= 60.0:
        return 15.0, f"飙升({slope:+.0f}%)"
    if slope >= 30.0:
        return 12.5, f"明显上升({slope:+.0f}%)"
    if slope >= 15.0:
        return 9.5, f"温和上升({slope:+.0f}%)"
    if slope >= -10.0:
        return 5.5, f"平稳({slope:+.0f}%)"
    return 1.5, f"回落({slope:+.0f}%)"


def score_keyword(
    candidate: KeywordCandidate,
    serp_apps: list[SerpAppRecord],
    details_map: dict[str, AppDetailRecord],
    trends: TrendsRecord | None,
    *,
    run_date: str,
    country: str,
    lang: str,
    run_mode: str,
    scoring_cfg: ScoringConfig,
) -> KeywordScoreRecord:
    blacklist = set(scoring_cfg.blacklist_terms)
    is_brand, brand_reason = detect_brand_keyword(
        candidate.keyword, serp_apps, details_map, blacklist, scoring_cfg
    )
    momentum_score, momentum_notes = compute_momentum_score(candidate, run_mode)
    entry_score, entry_metrics = compute_entry_score(
        candidate.keyword, serp_apps, details_map, scoring_cfg
    )
    trends_score, trends_label = compute_trends_score(trends)

    raw_total = momentum_score + entry_score + trends_score
    total_score = round(raw_total * 0.35 if is_brand else raw_total, 1)

    top_apps_summary: list[dict[str, Any]] = []
    for app in serp_apps[:5]:
        detail = details_map.get(app.app_id)
        top_apps_summary.append(
            {
                "rank": app.rank,
                "app_id": app.app_id,
                "title": app.title,
                "developer": app.developer,
                "installs": (detail.installs_str if detail and detail.installs_str else app.installs_str),
                "real_installs": detail.real_installs if detail else app.min_installs,
                "released": detail.released if detail else None,
                "age_days": detail.age_days if detail else None,
                "ratings": detail.ratings if detail else None,
                "score": detail.score if (detail and detail.score is not None) else app.score,
            }
        )

    signals = {
        "momentum_notes": momentum_notes,
        "best_suggest_rank": candidate.best_suggest_rank,
        "suggest_rank_delta": candidate.suggest_rank_delta,
        "is_new_in_suggest": candidate.is_new_in_suggest,
        "prefix_count": candidate.prefix_count,
        "chart_apps": candidate.chart_apps,
        "entry_metrics": entry_metrics,
        "trends_label": trends_label,
        "trends_slope_pct": trends.slope_pct if trends else None,
        "trends_avg_7d": trends.avg_7d if trends else None,
    }

    return KeywordScoreRecord(
        run_date=run_date,
        country=country,
        lang=lang,
        keyword=candidate.keyword,
        run_mode=run_mode,
        sources=candidate.sources,
        is_brand=is_brand,
        brand_reason=brand_reason,
        momentum_score=momentum_score,
        entry_score=entry_score,
        trends_score=trends_score,
        total_score=total_score,
        signals=signals,
        top_apps=top_apps_summary,
    )
