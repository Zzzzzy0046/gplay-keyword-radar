from __future__ import annotations

from collections import defaultdict
from typing import Any

from gplay_radar.config import ReportingConfig
from gplay_radar.models import DarkHorseApp, KeywordScoreRecord, MarketConfig


def _fmt_installs(val: int | None, fallback: str = "") -> str:
    if val is None:
        return fallback or "-"
    if val >= 1_000_000_000:
        return f"{val / 1_000_000_000:.1f}B"
    if val >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if val >= 1_000:
        return f"{val / 1_000:.0f}K"
    return str(val)


def _play_url(app_id: str, country: str = "us", lang: str = "en") -> str:
    return f"https://play.google.com/store/apps/details?id={app_id}&hl={lang}&gl={country}"


def _format_signal_tags(s: KeywordScoreRecord) -> str:
    parts: list[str] = []
    notes = s.signals.get("momentum_notes") or []
    parts.extend(notes[:2])
    em = s.signals.get("entry_metrics") or {}
    if em.get("fresh_apps_180d"):
        parts.append(f"半年新包:{em['fresh_apps_180d']}")
    elif em.get("new_apps_365d"):
        parts.append(f"一年新包:{em['new_apps_365d']}")
    if em.get("small_apps_top5"):
        parts.append(f"Top5中小包:{em['small_apps_top5']}/5")
    t_label = s.signals.get("trends_label")
    if t_label and t_label not in ("unavailable", "low_volume"):
        parts.append(f"Trends:{t_label}")
    return " · ".join(parts) if parts else "-"


def render_daily_report(
    *,
    run_date: str,
    market: MarketConfig,
    run_mode: str,
    scores: list[KeywordScoreRecord],
    dark_horses: list[DarkHorseApp],
    stats: dict[str, Any],
    degraded_sources: list[str],
    reporting_cfg: ReportingConfig,
) -> str:
    opportunities = [s for s in scores if not s.is_brand][: reporting_cfg.top_opportunities_limit]
    brand_watch = [s for s in scores if s.is_brand][: reporting_cfg.top_brand_watchlist_limit]
    top_horses = dark_horses[: reporting_cfg.top_dark_horse_apps_limit]

    mode_label = (
        "历史异动对比（Diff 模式）"
        if run_mode == "diff"
        else "冷启动基线扫描（Baseline 模式 — 连续跑满 2 天起自动开启每日异动对比）"
    )

    lines: list[str] = [
        f"# Google Play 选品热词雷达日报 — {run_date} ({market.label})",
        "",
        f"- **运行模式**：{mode_label}",
        f"- **采集概况**：联想快照 `{stats.get('suggest_count', 0)}` 条 | 榜单 App `{stats.get('chart_apps_count', 0)}` 款 | 榜单黑马 `{len(dark_horses)}` 款 | 候选词池 `{stats.get('candidates_total', 0)}` 个 | 深度验词 `{stats.get('enriched_count', 0)}` 个 | HTTP 请求 `{stats.get('requests_used', 0)}` 次",
    ]
    if degraded_sources:
        lines.append(
            f"- **降级提醒**：本次运行以下数据源降级或跳过：`{', '.join(degraded_sources)}`（不影响其余站内信号排序）"
        )
    lines.append("")

    # Section 1: Main Opportunity Table
    lines.append("## 一、Top 选品机会热词榜（非品牌 · 可切入优先）")
    lines.append("")
    if not opportunities:
        lines.append("> 今日暂无符合条件的非品牌机会词。")
        lines.append("")
    else:
        lines.append("| 排名 | 关键词 | 总分 | 热度(40) | 可切入(45) | 趋势(15) | 核心信号摘要 |")
        lines.append("|---:|---|---:|---:|---:|---:|---|")
        for idx, s in enumerate(opportunities, start=1):
            lines.append(
                f"| {idx} | **{s.keyword}** | **{s.total_score:.1f}** | "
                f"{s.momentum_score:.1f} | {s.entry_score:.1f} | {s.trends_score:.1f} | "
                f"{_format_signal_tags(s)} |"
            )
        lines.append("")

        # Deep-dive cards for Top 6 opportunities
        lines.append("### 重点机会词拆解（Top 6 竞品格局证据）")
        lines.append("")
        for idx, s in enumerate(opportunities[:6], start=1):
            em = s.signals.get("entry_metrics") or {}
            t_label = s.signals.get("trends_label", "-")
            lines.append(f"#### {idx}. `{s.keyword}` — 综合分 **{s.total_score:.1f}**")
            lines.append(
                f"- **来源**：`{', '.join(s.sources)}` | **Google Trends**：`{t_label}` | "
                f"**SERP Top 10 格局**：新包(<1年) `{em.get('new_apps_365d', 0)}` 款（<180天 `{em.get('fresh_apps_180d', 0)}` 款）、"
                f"中小包(<100万) `{em.get('small_apps_top10', 0)}` 款、Top 5 巨头(>=5000万) `{em.get('giants_top5', 0)}` 款、"
                f"标题精确含词 `{em.get('exact_title_hits', 0)}/10`"
            )
            if s.top_apps:
                lines.append("- **搜索前排代表 App**：")
                for app in s.top_apps[:5]:
                    url = _play_url(app["app_id"], market.country, market.lang)
                    inst = _fmt_installs(app.get("real_installs"), app.get("installs") or "")
                    rel = app.get("released") or "未知"
                    age_txt = f"（{app['age_days']}天前）" if app.get("age_days") is not None else ""
                    score_txt = f"{app['score']}★" if app.get("score") is not None else "-"
                    lines.append(
                        f"  - #{app['rank']} [{app['title']}]({url}) — {app['developer']} | "
                        f"安装 `{inst}` | 上线 `{rel}`{age_txt} | 评分 `{score_txt}`"
                    )
            lines.append("")

    # Section 2: Chart Dark-Horse Apps
    lines.append("## 二、品类榜单黑马 App 雷达（低安装冲前排 / 排名飙升）")
    lines.append("")
    if not top_horses:
        lines.append("> 今日榜单暂无新增黑马。")
        lines.append("")
    else:
        lines.append("| 品类 | 榜单 | 排名 | App 名称 | 安装量级 | 触发信号 | 反推需求词 |")
        lines.append("|---|---|---:|---|---|---|---|")
        for dh in top_horses:
            coll_label = "免费榜" if dh.collection == "topselling_free" else "畅销榜"
            url = _play_url(dh.app_id, market.country, market.lang)
            phrases_txt = ", ".join(f"`{p}`" for p in dh.extracted_phrases[:3]) or "-"
            reasons_txt = "；".join(dh.reasons)
            lines.append(
                f"| {dh.category} | {coll_label} | #{dh.rank} | "
                f"[{dh.title}]({url}) | `{dh.installs_str or '-'}` | {reasons_txt} | {phrases_txt} |"
            )
        lines.append("")

    # Section 3: Brand Watchlist
    lines.append("## 三、品牌词飙升观察（已自动从主榜分流）")
    lines.append("")
    if not brand_watch:
        lines.append("> 今日无显著品牌词进入候选池。")
        lines.append("")
    else:
        lines.append("| 关键词 | 识别依据 | 原始热度(40) | Top 1 对应 App |")
        lines.append("|---|---|---:|---|")
        for s in brand_watch:
            top1_txt = "-"
            if s.top_apps:
                a = s.top_apps[0]
                top1_txt = f"[{a['title']}]({_play_url(a['app_id'], market.country, market.lang)}) (`{_fmt_installs(a.get('real_installs'), a.get('installs') or '')}`)"
            lines.append(
                f"| **{s.keyword}** | {s.brand_reason} | {s.momentum_score:.1f} | {top1_txt} |"
            )
        lines.append("")

    return "\n".join(lines)


def render_weekly_report(
    *,
    start_date: str,
    end_date: str,
    market: MarketConfig,
    scores: list[KeywordScoreRecord],
    reporting_cfg: ReportingConfig,
) -> str:
    """Aggregate 7 days of daily scores to surface persistent, non-flapping opportunities."""
    by_kw: dict[str, list[KeywordScoreRecord]] = defaultdict(list)
    dates_seen: set[str] = set()
    for s in scores:
        dates_seen.add(s.run_date)
        if not s.is_brand:
            by_kw[s.keyword].append(s)

    rolled: list[dict[str, Any]] = []
    for kw, items in by_kw.items():
        items.sort(key=lambda x: x.run_date)
        days = len(items)
        avg_total = round(sum(i.total_score for i in items) / days, 1)
        max_total = round(max(i.total_score for i in items), 1)
        latest = items[-1]
        # Persistence bonus rewards keywords that stayed hot across multiple days
        weekly_rank_score = round(avg_total + min(12.0, (days - 1) * 3.0), 1)
        rolled.append(
            {
                "keyword": kw,
                "days": days,
                "avg_total": avg_total,
                "max_total": max_total,
                "weekly_score": weekly_rank_score,
                "latest": latest,
            }
        )
    rolled.sort(key=lambda r: (-r["weekly_score"], -r["days"], -r["max_total"]))

    lines = [
        f"# Google Play 选品热词周报 — {start_date} ~ {end_date} ({market.label})",
        "",
        f"- **统计窗口**：覆盖 `{len(dates_seen)}` 个有效采集日，累计追踪 `{len(by_kw)}` 个非品牌机会词",
        "- **周榜说明**：优先突出**连续多天出现且机会分稳定偏高**的词，自动过滤单日偶发抖动。",
        "",
        "## 持续机会热词周榜（Top 25）",
        "",
    ]
    if not rolled:
        lines.append("> 本周期内暂无评分记录，请先连续运行每日采集任务。")
        return "\n".join(lines)

    lines.append("| 排名 | 关键词 | 周综合分 | 上榜天数 | 日均分 | 峰值分 | 最新核心信号 |")
    lines.append("|---:|---|---:|---:|---:|---:|---|")
    for idx, row in enumerate(rolled[: max(25, reporting_cfg.top_opportunities_limit)], start=1):
        lines.append(
            f"| {idx} | **{row['keyword']}** | **{row['weekly_score']:.1f}** | "
            f"{row['days']}/{max(len(dates_seen), 1)} | {row['avg_total']:.1f} | "
            f"{row['max_total']:.1f} | {_format_signal_tags(row['latest'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def render_global_summary_report(
    *,
    run_date: str,
    markets_map: dict[str, MarketConfig],
    scores_all: list[KeywordScoreRecord],
    dark_horses_by_market: dict[str, list[DarkHorseApp]],
) -> str:
    """Render cross-market daily executive summary highlighting multi-country breakout apps."""
    scores_by_country: dict[str, list[KeywordScoreRecord]] = defaultdict(list)
    for s in scores_all:
        scores_by_country[s.country].append(s)

    countries = sorted(set(scores_by_country.keys()) | set(dark_horses_by_market.keys()))

    # Aggregate dark-horse apps across markets by app_id
    app_markets: dict[str, dict[str, Any]] = {}
    for country, horses in dark_horses_by_market.items():
        for dh in horses:
            entry = app_markets.setdefault(
                dh.app_id,
                {
                    "app_id": dh.app_id,
                    "title": dh.title,
                    "developer": dh.developer,
                    "installs_str": dh.installs_str,
                    "min_installs": dh.min_installs,
                    "placements": {},
                    "categories": set(),
                },
            )
            entry["categories"].add(dh.category)
            # Keep best rank per country
            prev = entry["placements"].get(country)
            if prev is None or dh.rank < prev["rank"]:
                entry["placements"][country] = {
                    "rank": dh.rank,
                    "collection": "免费" if dh.collection == "topselling_free" else "畅销",
                    "category": dh.category,
                }
            # Prefer English or cleaner title if US/IN present
            if country in ("us", "in") and dh.title:
                entry["title"] = dh.title

    cross_country_horses = [
        info for info in app_markets.values() if len(info["placements"]) >= 2
    ]
    cross_country_horses.sort(
        key=lambda x: (
            -len(x["placements"]),
            x["min_installs"] if x["min_installs"] is not None else 10_000_000_000,
            min(p["rank"] for p in x["placements"].values()),
        )
    )

    lines: list[str] = [
        f"# Google Play 多国跨市场热词总览 — {run_date}",
        "",
        f"- **覆盖市场数**：`{len(countries)}` 个国家（`{', '.join(c.upper() for c in countries)}`）",
        f"- **跨国共现黑马 App**：`{len(cross_country_horses)}` 款（同时在 `>=2` 个国家冲榜的低安装/飙升 App）",
        "",
        "## 一、各国 Top 3 机会热词速览",
        "",
        "| 国家 / 市场 | 区域 | 语种 | 黑马数 | Top 3 选品机会词（非品牌） | 单国日报 |",
        "|---|---|---|---:|---|---|",
    ]

    for c in countries:
        m = markets_map.get(c, MarketConfig(country=c, lang="en", label=c.upper(), region="-"))
        opps = [s for s in scores_by_country.get(c, []) if not s.is_brand]
        opps.sort(key=lambda x: (-x.total_score, x.keyword))
        top3 = " / ".join(f"**{o.keyword}** ({o.total_score:.1f})" for o in opps[:3]) or "-"
        dh_count = len(dark_horses_by_market.get(c, []))
        rel_link = f"[{run_date}_{c}.md]({run_date}_{c}.md)"
        lines.append(
            f"| {m.label} (`{c.upper()}`) | `{m.region}` | `{m.lang}` | {dh_count} | {top3} | {rel_link} |"
        )

    lines.append("")
    lines.append("## 二、跨国同步起量黑马 App 雷达（多国共现信号）")
    lines.append("")
    if not cross_country_horses:
        lines.append("> 今日暂无同时在 2 个及以上国家榜单出现的低安装/飙升黑马 App。")
        lines.append("")
    else:
        lines.append("| 覆盖国数 | App 名称 | 安装量级 | 品类 | 各国榜单占位 |")
        lines.append("|---:|---|---|---|---|")
        for info in cross_country_horses[:25]:
            url = _play_url(info["app_id"], "us", "en")
            cats = ", ".join(sorted(info["categories"]))
            places = " · ".join(
                f"`{ctry.upper()}` {p['collection']}#{p['rank']}"
                for ctry, p in sorted(info["placements"].items(), key=lambda kv: kv[1]["rank"])
            )
            lines.append(
                f"| **{len(info['placements'])}国** | [{info['title']}]({url}) | "
                f"`{info['installs_str'] or '-'}` | {cats} | {places} |"
            )
        lines.append("")

    return "\n".join(lines)


def render_inspect_card(
    scored: KeywordScoreRecord,
    suggestions: list[str],
    *,
    country: str,
    lang: str,
) -> str:
    em = scored.signals.get("entry_metrics") or {}
    lines = [
        f"=== Keyword Inspect: {scored.keyword} ({country.upper()}/{lang}) ===",
        f"Total Score: {scored.total_score:.1f}  (Momentum={scored.momentum_score:.1f}/40, Entry={scored.entry_score:.1f}/45, Trends={scored.trends_score:.1f}/15)",
        f"Brand Check: {'YES - ' + scored.brand_reason if scored.is_brand else 'NO (Generic demand keyword)'}",
        f"Google Trends: {scored.signals.get('trends_label', '-')}",
        f"SERP Top 10: new(<1y)={em.get('new_apps_365d', 0)}, fresh(<180d)={em.get('fresh_apps_180d', 0)}, small(<1M)={em.get('small_apps_top10', 0)}, giants_top5={em.get('giants_top5', 0)}, exact_title_hits={em.get('exact_title_hits', 0)}/10",
        f"Autocomplete Suggestions ({len(suggestions)}): {', '.join(suggestions[:8]) or 'none'}",
        "Top 5 SERP Apps:",
    ]
    for app in scored.top_apps:
        inst = _fmt_installs(app.get("real_installs"), app.get("installs") or "")
        rel = app.get("released") or "unknown"
        lines.append(
            f"  #{app['rank']} {app['title']} ({app['app_id']}) | installs={inst} | released={rel} | score={app.get('score')}"
        )
    return "\n".join(lines)
