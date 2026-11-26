from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MarketConfig:
    country: str = "us"
    lang: str = "en"
    label: str = "US (English)"
    region: str = "na"
    tier: str = "deep"  # deep | radar
    expand_az: bool | None = None
    az_letters_per_day: int | None = None
    max_chart_phrases_to_suggest: int | None = None
    max_serp_candidates: int | None = None
    max_trends_candidates: int | None = None


@dataclass
class SuggestRecord:
    seed_query: str
    suggestion: str
    rank: int
    group_name: str


@dataclass
class ChartAppRecord:
    category: str
    collection: str
    rank: int
    app_id: str
    title: str
    developer: str
    installs_str: str
    min_installs: int | None
    score: float | None
    description: str = ""


@dataclass
class DarkHorseApp:
    app_id: str
    title: str
    developer: str
    category: str
    collection: str
    rank: int
    prev_rank: int | None
    rank_delta: int | None
    installs_str: str
    min_installs: int | None
    reasons: list[str] = field(default_factory=list)
    extracted_phrases: list[str] = field(default_factory=list)


@dataclass
class SerpAppRecord:
    keyword: str
    rank: int
    app_id: str
    title: str
    developer: str
    installs_str: str
    min_installs: int | None
    score: float | None


@dataclass
class AppDetailRecord:
    app_id: str
    country: str
    lang: str
    title: str
    developer: str
    genre_id: str
    released: str | None
    age_days: int | None
    installs_str: str
    real_installs: int | None
    ratings: int | None
    score: float | None
    summary: str
    updated_ts: int | None
    fetched_at: str


@dataclass
class TrendsRecord:
    keyword: str
    avg_7d: float | None
    avg_prev21d: float | None
    slope_pct: float | None
    latest_value: int | None
    peak_value: int | None
    series: list[dict[str, Any]] = field(default_factory=list)
    status: str = "ok"  # ok | empty | unavailable


@dataclass
class KeywordCandidate:
    keyword: str
    sources: list[str] = field(default_factory=list)
    best_suggest_rank: int | None = None
    prev_median_suggest_rank: float | None = None
    suggest_rank_delta: float | None = None
    is_new_in_suggest: bool = False
    prefix_count: int = 0
    seen_days_7d: int = 0
    chart_apps: list[dict[str, Any]] = field(default_factory=list)
    pre_score: float = 0.0


@dataclass
class KeywordScoreRecord:
    run_date: str
    country: str
    lang: str
    keyword: str
    run_mode: str  # baseline | diff
    sources: list[str]
    is_brand: bool
    brand_reason: str
    momentum_score: float
    entry_score: float
    trends_score: float
    total_score: float
    signals: dict[str, Any]
    top_apps: list[dict[str, Any]]


@dataclass
class RunSummary:
    run_id: str
    run_date: str
    mode: str
    country: str
    lang: str
    status: str
    requests_used: int
    degraded_sources: list[str]
    suggest_count: int
    chart_apps_count: int
    dark_horse_count: int
    enriched_keywords_count: int
    scored_opportunities_count: int
    report_path: str
    global_summary_path: str | None = None
