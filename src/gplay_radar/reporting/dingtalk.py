from __future__ import annotations

import base64
import hashlib
import hmac
import time
import urllib.parse
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
        lines.append(
            f"{idx}. **{s.keyword}** — `{s.total_score:.1f}分` "
            f"(新包:{em.get('new_apps_365d', 0)} 小包:{em.get('small_apps_top10', 0)} Trends:{t_lbl})"
        )

    if dark_horses:
        lines.append("")
        lines.append("**今日榜单黑马 Top 3**")
        for dh in dark_horses[:3]:
            lines.append(
                f"- [{dh.category}] #{dh.rank} **{dh.title}** (`{dh.installs_str or '-'}`)"
            )

    return {
        "msgtype": "markdown",
        "markdown": {
            "title": f"GP热词雷达 {run_date} ({market.country.upper()})",
            "text": "\n".join(lines),
        },
    }


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
    if not dingtalk_cfg.webhook_url:
        raise ValueError("DingTalk push is enabled but webhook_url / DINGTALK_WEBHOOK_URL is empty")
    url = sign_dingtalk_webhook(dingtalk_cfg.webhook_url, dingtalk_cfg.secret)
    payload = build_dingtalk_payload(
        run_date=run_date,
        market=market,
        run_mode=run_mode,
        scores=scores,
        dark_horses=dark_horses,
        degraded_sources=degraded_sources,
        top_n=dingtalk_cfg.top_n,
    )
    resp = httpx.post(url, json=payload, timeout=15.0)
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(f"DingTalk API error: {data}")
    return data
