from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
import urllib.parse
from collections import defaultdict
from typing import Any

import httpx

from gplay_radar.config import DingTalkConfig
from gplay_radar.models import DarkHorseApp, KeywordScoreRecord, MarketConfig


def sign_dingtalk_webhook(webhook_url: str, secret: str, timestamp_ms: int | None = None) -> str:
    """Append DingTalk HMAC-SHA256 timestamp and sign parameters when secret is configured."""
    if not secret:
        return webhook_url
    ts = str(timestamp_ms if timestamp_ms is not None else int(time.time() * 1000))
    string_to_sign = f"{ts}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), string_to_sign, digestmod=hashlib.sha256).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    sep = "&" if "?" in webhook_url else "?"
    return f"{webhook_url}{sep}timestamp={ts}&sign={sign}"


def _github_report_link(rel_path: str) -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if not repo:
        return None
    return f"https://github.com/{repo}/blob/data/{rel_path}"


def build_dingtalk_payload(
    *,
    run_date: str,
    market: MarketConfig,
    run_mode: str,
    scores: list[KeywordScoreRecord],
    dark_horses: list[DarkHorseApp],
    degraded_sources: list[str],
    top_n: int = 8,
) -> dict[str, Any]:
    opportunities = [s for s in scores if not s.is_brand][:top_n]
    mode_txt = "异动对比" if run_mode == "diff" else "基线扫描"
    lines = [
        f"### Google Play 热词雷达 ({run_date} · {market.country.upper()} · {mode_txt})",
        "",
    ]
    if degraded_sources:
        lines.append(f"> 降级源：{', '.join(degraded_sources)}")
        lines.append("")

    lines.append("**Top 机会热词（非品牌）**")
    for idx, s in enumerate(opportunities, start=1):
        em = s.signals.get("entry_metrics") or {}
        t_lbl = s.signals.get("trends_label", "-")
        m_notes = "·".join((s.signals.get("momentum_notes") or [])[:1])
        tag_str = f" {m_notes}" if m_notes else ""
        lines.append(
            f"{idx}. **{s.keyword}** — `{s.total_score:.1f}分`{tag_str} "
            f"(新包:{em.get('new_apps_365d', 0)} 小包:{em.get('small_apps_top10', 0)} Trends:{t_lbl})"
        )

    if dark_horses:
        lines.append("")
        lines.append("**今日榜单黑马 Top 3**")
        for dh in dark_horses[:3]:
            reason = dh.reasons[0] if dh.reasons else f"#{dh.rank}"
            lines.append(f"- [{dh.category}] **{dh.title}** ({reason})")

    gh_url = _github_report_link(f"data/reports/daily/{run_date}_{market.country}.md")
    if gh_url:
        lines.append("")
        lines.append(f"[查看完整 Markdown 日报]({gh_url})")

    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"GP热词雷达 {run_date} ({market.country.upper()})",
            "text": "\n".join(lines),
        },
    }


def build_dingtalk_global_payload(
    *,
    run_date: str,
    markets_map: dict[str, MarketConfig],
    scores_all: list[KeywordScoreRecord],
    dark_horses_by_market: dict[str, list[DarkHorseApp]],
    degraded_by_country: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Build ONE consolidated multi-market DingTalk card (no multi-message spam)."""
    scores_by_country: dict[str, list[KeywordScoreRecord]] = defaultdict(list)
    modes_seen: set[str] = set()
    for s in scores_all:
        scores_by_country[s.country].append(s)
        modes_seen.add(s.run_mode)

    countries = sorted(set(scores_by_country.keys()) | set(dark_horses_by_market.keys()))
    mode_txt = "异动对比" if "diff" in modes_seen else "基线扫描"

    lines = [
        f"### Google Play 全球热词雷达 ({run_date} · {len(countries)}国 · {mode_txt})",
        "",
        "**各国 Top 2 选品机会词（非品牌）**",
    ]
    for c in countries:
        opps = [s for s in scores_by_country.get(c, []) if not s.is_brand]
        opps.sort(key=lambda x: (-x.total_score, x.keyword))
        if not opps:
            continue
        items_txt = " | ".join(
            f"**{o.keyword}** `{o.total_score:.0f}`"
            + (" 🆕" if o.signals.get("is_new_in_suggest") else "")
            for o in opps[:2]
        )
        lines.append(f"- **`{c.upper()}`**：{items_txt}")

    # Cross-country dark-horse apps (>=2 countries)
    app_places: dict[str, dict[str, Any]] = {}
    for c, horses in dark_horses_by_market.items():
        for dh in horses:
            entry = app_places.setdefault(
                dh.app_id,
                {"title": dh.title, "installs": dh.installs_str, "min_installs": dh.min_installs, "places": {}},
            )
            if c in ("us", "in") and dh.title:
                entry["title"] = dh.title
            prev = entry["places"].get(c)
            if prev is None or dh.rank < prev:
                entry["places"][c] = dh.rank

    shared = [v for v in app_places.values() if len(v["places"]) >= 2]
    shared.sort(
        key=lambda x: (
            -len(x["places"]),
            x["min_installs"] if x["min_installs"] is not None else 10_000_000_000,
            min(x["places"].values()),
        )
    )
    if shared:
        lines.append("")
        lines.append("**多国同步起量黑马 App Top 5**")
        for info in shared[:5]:
            p_txt = " ".join(
                f"{ctry.upper()}#{rk}"
                for ctry, rk in sorted(info["places"].items(), key=lambda kv: kv[1])
            )
            lines.append(f"- **{len(info['places'])}国** {info['title']} (`{info['installs'] or '-'}`) · {p_txt}")

    if degraded_by_country:
        notes = [f"{c.upper()}({','.join(v)})" for c, v in degraded_by_country.items() if v]
        if notes:
            lines.append("")
            lines.append(f"> 降级提示：{'; '.join(notes[:4])}")

    gh_url = _github_report_link(f"data/reports/daily/{run_date}_global_summary.md")
    if gh_url:
        lines.append("")
        lines.append(f"[查看完整多国总览与单国日报]({gh_url})")

    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"GP全球热词雷达 {run_date} ({len(countries)}国)",
            "text": "\n".join(lines),
        },
    }


def post_dingtalk_payload(dingtalk_cfg: DingTalkConfig, payload: dict[str, Any]) -> dict[str, Any]:
    if not dingtalk_cfg.webhook_url:
        raise ValueError("DingTalk push is enabled but webhook_url / DINGTALK_WEBHOOK_URL is empty")
    url = sign_dingtalk_webhook(dingtalk_cfg.webhook_url, dingtalk_cfg.secret)
    resp = httpx.post(url, json=payload, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"DingTalk API error: {data}")
    return data


def send_dingtalk_daily_summary(
    *,
    dingtalk_cfg: DingTalkConfig,
    run_date: str,
    market: MarketConfig,
    run_mode: str,
    scores: list[KeywordScoreRecord],
    dark_horses: list[DarkHorseApp],
    degraded_sources: list[str],
) -> dict[str, Any]:
    payload = build_dingtalk_payload(
        run_date=run_date,
        market=market,
        run_mode=run_mode,
        scores=scores,
        dark_horses=dark_horses,
        degraded_sources=degraded_sources,
        top_n=dingtalk_cfg.top_n,
    )
    return post_dingtalk_payload(dingtalk_cfg, payload)
