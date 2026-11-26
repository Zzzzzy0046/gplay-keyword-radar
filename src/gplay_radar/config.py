from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gplay_radar.models import MarketConfig


@dataclass
class StorageConfig:
    db_path: Path
    reports_dir: Path
    app_cache_ttl_days: int = 7


@dataclass
class NetworkConfig:
    proxy: str = ""
    timeout_seconds: float = 20.0
    min_delay_seconds: float = 0.6
    max_delay_seconds: float = 1.5
    max_retries: int = 3
    daily_request_budget: int = 600


@dataclass
class ChartsConfig:
    enabled: bool = True
    collections: list[str] = field(default_factory=lambda: ["topselling_free", "topgrossing"])
    top_n_free: int = 100
    top_n_grossing: int = 50
    dark_horse_max_installs: int = 1_000_000
    dark_horse_rank_jump: int = 15
    categories: list[str] = field(default_factory=lambda: ["TOOLS", "PRODUCTIVITY"])


@dataclass
class SeedGroupConfig:
    name: str
    expand_az: bool
    seeds: list[str]


@dataclass
class SuggestConfig:
    enabled: bool = True
    az_letters_per_day: int = 6
    intent_prefixes: list[str] = field(default_factory=lambda: ["ai", "free"])
    seed_groups: list[SeedGroupConfig] = field(default_factory=list)


@dataclass
class FunnelConfig:
    max_chart_phrases_to_suggest: int = 25
    max_serp_candidates: int = 35
    serp_top_n: int = 10
    max_trends_candidates: int = 20
    trends_enabled: bool = True
    trends_timeframe: str = "today 1-m"


@dataclass
class ScoringConfig:
    new_app_age_days: int = 365
    fresh_app_age_days: int = 180
    small_app_max_installs: int = 1_000_000
    giant_app_min_installs: int = 50_000_000
    brand_giant_installs: int = 10_000_000
    brand_giant_ratings: int = 100_000
    blacklist_terms: list[str] = field(default_factory=list)


@dataclass
class DingTalkConfig:
    enabled: bool = False
    webhook_url: str = ""
    secret: str = ""
    top_n: int = 8


@dataclass
class ReportingConfig:
    top_opportunities_limit: int = 20
    top_brand_watchlist_limit: int = 8
    top_dark_horse_apps_limit: int = 15
    dingtalk: DingTalkConfig = field(default_factory=DingTalkConfig)


@dataclass
class AppConfig:
    base_dir: Path
    storage: StorageConfig
    network: NetworkConfig
    markets: list[MarketConfig]
    charts: ChartsConfig
    suggest: SuggestConfig
    funnel: FunnelConfig
    scoring: ScoringConfig
    reporting: ReportingConfig

    def resolved_proxy(self) -> str | None:
        if self.network.proxy.strip():
            return self.network.proxy.strip()
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            val = os.environ.get(key, "").strip()
            if val:
                return val
        return None


def _resolve_path(base_dir: Path, raw_path: str) -> Path:
    p = Path(raw_path)
    return p if p.is_absolute() else (base_dir / p)


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    base_dir = path.parent

    storage_raw = raw.get("storage", {})
    storage = StorageConfig(
        db_path=_resolve_path(base_dir, storage_raw.get("db_path", "data/radar.db")),
        reports_dir=_resolve_path(base_dir, storage_raw.get("reports_dir", "data/reports")),
        app_cache_ttl_days=int(storage_raw.get("app_cache_ttl_days", 7)),
    )

    net_raw = raw.get("network", {})
    network = NetworkConfig(
        proxy=str(net_raw.get("proxy", "") or ""),
        timeout_seconds=float(net_raw.get("timeout_seconds", 20.0)),
        min_delay_seconds=float(net_raw.get("min_delay_seconds", 0.6)),
        max_delay_seconds=float(net_raw.get("max_delay_seconds", 1.5)),
        max_retries=int(net_raw.get("max_retries", 3)),
        daily_request_budget=int(net_raw.get("daily_request_budget", 600)),
    )

    markets_raw = raw.get("markets") or [{"country": "us", "lang": "en", "label": "US (English)"}]
    markets = [
        MarketConfig(
            country=str(m.get("country", "us")).lower(),
            lang=str(m.get("lang", "en")).lower(),
            label=str(m.get("label") or f"{m.get('country', 'us').upper()} ({m.get('lang', 'en')})"),
        )
        for m in markets_raw
    ]

    charts_raw = raw.get("charts", {})
    charts = ChartsConfig(
        enabled=bool(charts_raw.get("enabled", True)),
        collections=list(charts_raw.get("collections", ["topselling_free", "topgrossing"])),
        top_n_free=int(charts_raw.get("top_n_free", 100)),
        top_n_grossing=int(charts_raw.get("top_n_grossing", 50)),
        dark_horse_max_installs=int(charts_raw.get("dark_horse_max_installs", 1_000_000)),
        dark_horse_rank_jump=int(charts_raw.get("dark_horse_rank_jump", 15)),
        categories=list(charts_raw.get("categories", ["TOOLS", "PRODUCTIVITY"])),
    )

    suggest_raw = raw.get("suggest", {})
    groups: list[SeedGroupConfig] = []
    for gname, gdata in (suggest_raw.get("seed_groups") or {}).items():
        if isinstance(gdata, dict):
            groups.append(
                SeedGroupConfig(
                    name=str(gname),
                    expand_az=bool(gdata.get("expand_az", False)),
                    seeds=[str(s).strip().lower() for s in (gdata.get("seeds") or []) if str(s).strip()],
                )
            )
    suggest = SuggestConfig(
        enabled=bool(suggest_raw.get("enabled", True)),
        az_letters_per_day=int(suggest_raw.get("az_letters_per_day", 6)),
        intent_prefixes=[str(p).strip().lower() for p in (suggest_raw.get("intent_prefixes") or []) if str(p).strip()],
        seed_groups=groups,
    )

    funnel_raw = raw.get("funnel", {})
    funnel = FunnelConfig(
        max_chart_phrases_to_suggest=int(funnel_raw.get("max_chart_phrases_to_suggest", 25)),
        max_serp_candidates=int(funnel_raw.get("max_serp_candidates", 35)),
        serp_top_n=int(funnel_raw.get("serp_top_n", 10)),
        max_trends_candidates=int(funnel_raw.get("max_trends_candidates", 20)),
        trends_enabled=bool(funnel_raw.get("trends_enabled", True)),
        trends_timeframe=str(funnel_raw.get("trends_timeframe", "today 1-m")),
    )

    scoring_raw = raw.get("scoring", {})
    scoring = ScoringConfig(
        new_app_age_days=int(scoring_raw.get("new_app_age_days", 365)),
        fresh_app_age_days=int(scoring_raw.get("fresh_app_age_days", 180)),
        small_app_max_installs=int(scoring_raw.get("small_app_max_installs", 1_000_000)),
        giant_app_min_installs=int(scoring_raw.get("giant_app_min_installs", 50_000_000)),
        brand_giant_installs=int(scoring_raw.get("brand_giant_installs", 10_000_000)),
        brand_giant_ratings=int(scoring_raw.get("brand_giant_ratings", 100_000)),
        blacklist_terms=[str(t).strip().lower() for t in (scoring_raw.get("blacklist_terms") or []) if str(t).strip()],
    )

    rep_raw = raw.get("reporting", {})
    dt_raw = rep_raw.get("dingtalk", {})
    dingtalk = DingTalkConfig(
        enabled=bool(dt_raw.get("enabled", False)),
        webhook_url=os.environ.get("DINGTALK_WEBHOOK_URL", "").strip() or str(dt_raw.get("webhook_url", "") or "").strip(),
        secret=os.environ.get("DINGTALK_SECRET", "").strip() or str(dt_raw.get("secret", "") or "").strip(),
        top_n=int(dt_raw.get("top_n", 8)),
    )
    reporting = ReportingConfig(
        top_opportunities_limit=int(rep_raw.get("top_opportunities_limit", 20)),
        top_brand_watchlist_limit=int(rep_raw.get("top_brand_watchlist_limit", 8)),
        top_dark_horse_apps_limit=int(rep_raw.get("top_dark_horse_apps_limit", 15)),
        dingtalk=dingtalk,
    )

    return AppConfig(
        base_dir=base_dir,
        storage=storage,
        network=network,
        markets=markets,
        charts=charts,
        suggest=suggest,
        funnel=funnel,
        scoring=scoring,
        reporting=reporting,
    )
