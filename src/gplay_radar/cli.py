from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from gplay_radar.collectors.app_detail import AppDetailCollector
from gplay_radar.collectors.charts import ChartsCollector
from gplay_radar.collectors.http_client import ThrottledHttpClient
from gplay_radar.collectors.serp import SerpCollector
from gplay_radar.collectors.suggest import SuggestCollector
from gplay_radar.collectors.trends import TrendsCollector
from gplay_radar.config import load_config
from gplay_radar.pipeline.runner import RadarPipeline
from gplay_radar.reporting.markdown import render_inspect_card
from gplay_radar.storage.db import connect_db, init_db


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _cmd_init_db(config_path: Path) -> int:
    cfg = load_config(config_path)
    conn = connect_db(cfg.storage.db_path)
    init_db(conn)
    conn.close()
    print(f"Initialized SQLite database at: {cfg.storage.db_path}")
    return 0


def _cmd_check_health(config_path: Path) -> int:
    cfg = load_config(config_path)
    proxy = cfg.resolved_proxy()
    print(f"Proxy: {proxy or '(none / direct)'}")
    http = ThrottledHttpClient(cfg.network, proxy=proxy)
    trends = TrendsCollector(http, timeframe=cfg.funnel.trends_timeframe)
    ok = True
    try:
        sug = SuggestCollector(http).fetch("pdf scanner", country="us", lang="en")
        print(f"[OK] Suggest (IJ4APc): {len(sug)} items -> {sug[:3]}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] Suggest (IJ4APc): {exc}")

    try:
        charts = ChartsCollector(http).fetch_chart("TOOLS", "topselling_free", count=10, country="us", lang="en")
        print(f"[OK] Charts (vyAe2 TOOLS): {len(charts)} items -> #1 {charts[0].title} ({charts[0].installs_str})")
    except Exception as exc:
        ok = False
        print(f"[FAIL] Charts (vyAe2): {exc}")

    try:
        serp = SerpCollector(http).search("pdf scanner", country="us", lang="en", top_n=3)
        print(f"[OK] SERP Search: {len(serp)} items -> #1 {serp[0].title}")
        det = AppDetailCollector(http).fetch(serp[0].app_id, country="us", lang="en")
        print(f"[OK] App Detail: {det.app_id} released={det.released} installs={det.real_installs}")
    except Exception as exc:
        ok = False
        print(f"[FAIL] SERP/Detail: {exc}")

    try:
        tr = trends.fetch_keyword("pdf scanner", country="us", lang="en")
        print(f"[{tr.status.upper()}] Google Trends: avg7d={tr.avg_7d} slope={tr.slope_pct}%")
    except Exception as exc:
        print(f"[WARN] Google Trends: {exc}")
    finally:
        trends.close()
        http.close()

    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gplay-radar",
        description="Google Play Keyword Monitoring & Opportunity Discovery Radar",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to config.yaml (default: ./config.yaml)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logs")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="Initialize SQLite database tables")
    sub.add_parser("check-health", help="Verify live connectivity to all 4 public data sources")

    run_parser = sub.add_parser("run", help="Run daily or weekly monitoring pipeline")
    run_sub = run_parser.add_subparsers(dest="run_type", required=True)

    daily_p = run_sub.add_parser("daily", help="Run daily discovery, enrichment, scoring & report")
    daily_p.add_argument("--date", help="Override run date (YYYY-MM-DD)")
    daily_p.add_argument("--market", help="Filter to a single country code, e.g. us")
    daily_p.add_argument("--no-trends", action="store_true", help="Skip Google Trends step")
    daily_p.add_argument("--no-push", action="store_true", help="Skip DingTalk push")

    weekly_p = run_sub.add_parser("weekly", help="Generate 7-day rolling opportunity weekly report")
    weekly_p.add_argument("--date", help="End date of 7-day window (YYYY-MM-DD)")
    weekly_p.add_argument("--market", help="Filter to a single country code, e.g. us")

    insp_p = sub.add_parser("inspect", help="Live-inspect opportunity score for a single keyword")
    insp_p.add_argument("keyword", help="Keyword to inspect, e.g. 'coin identifier'")
    insp_p.add_argument("--country", default="us", help="Store country code (default: us)")
    insp_p.add_argument("--lang", default="en", help="Store language code (default: en)")
    insp_p.add_argument("--no-trends", action="store_true", help="Skip Google Trends")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)
    config_path = Path(args.config)

    if args.command == "init-db":
        return _cmd_init_db(config_path)
    if args.command == "check-health":
        return _cmd_check_health(config_path)

    cfg = load_config(config_path)
    pipeline = RadarPipeline(cfg)
    try:
        if args.command == "run" and args.run_type == "daily":
            ref_date = date.fromisoformat(args.date) if args.date else None
            summaries = pipeline.run_daily(
                run_date=ref_date,
                market_filter=args.market,
                skip_trends=args.no_trends,
                skip_push=args.no_push,
            )
            for s in summaries:
                print(
                    f"[{s.status.upper()}] {s.run_date} ({s.country.upper()}) | mode={s.mode} | "
                    f"suggests={s.suggest_count} | charts={s.chart_apps_count} | dark_horses={s.dark_horse_count} | "
                    f"opportunities={s.scored_opportunities_count} | requests={s.requests_used}"
                )
                print(f"Report saved to: {s.report_path}")
            return 0

        if args.command == "run" and args.run_type == "weekly":
            ref_date = date.fromisoformat(args.date) if args.date else None
            paths = pipeline.run_weekly(end_date=ref_date, market_filter=args.market)
            for p in paths:
                print(f"Weekly report saved to: {p}")
            return 0

        if args.command == "inspect":
            scored, suggestions = pipeline.inspect_keyword(
                args.keyword,
                country=args.country,
                lang=args.lang,
                skip_trends=args.no_trends,
            )
            print(render_inspect_card(scored, suggestions, country=args.country, lang=args.lang))
            return 0
    finally:
        pipeline.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
