from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta

from gplay_radar.collectors.app_detail import AppDetailCollector
from gplay_radar.collectors.charts import ChartsCollector
from gplay_radar.collectors.http_client import RequestBudgetExceeded, ThrottledHttpClient
from gplay_radar.collectors.serp import SerpCollector
from gplay_radar.collectors.suggest import SuggestCollector, build_seed_queries
from gplay_radar.collectors.trends import TrendsCollector
from gplay_radar.config import AppConfig
from gplay_radar.models import (
    AppDetailRecord,
    ChartAppRecord,
    DarkHorseApp,
    KeywordCandidate,
    KeywordScoreRecord,
    MarketConfig,
    RunSummary,
    SuggestRecord,
    TrendsRecord,
)
from gplay_radar.pipeline.diff import build_keyword_candidates, detect_chart_dark_horses
from gplay_radar.pipeline.normalize import normalize_keyword
from gplay_radar.pipeline.scoring import score_keyword
from gplay_radar.reporting.dingtalk import (
    build_dingtalk_global_payload,
    build_dingtalk_payload,
    post_dingtalk_payload,
)
from gplay_radar.reporting.markdown import (
    render_daily_report,
    render_global_summary_report,
    render_weekly_report,
)
from gplay_radar.storage.db import connect_db, init_db
from gplay_radar.storage.repository import RadarRepository

logger = logging.getLogger(__name__)


class RadarPipeline:
    """End-to-end funnel orchestrator for daily runs, weekly rollups, and single-keyword inspection."""

    def __init__(
        self,
        config: AppConfig,
        *,
        repo: RadarRepository | None = None,
        http_client: ThrottledHttpClient | None = None,
        suggest_collector: SuggestCollector | None = None,
        charts_collector: ChartsCollector | None = None,
        serp_collector: SerpCollector | None = None,
        app_detail_collector: AppDetailCollector | None = None,
        trends_collector: TrendsCollector | None = None,
    ) -> None:
        self.cfg = config
        self._owns_conn = repo is None
        if repo is None:
            self._conn = connect_db(config.storage.db_path)
            init_db(self._conn)
            self.repo = RadarRepository(self._conn)
        else:
            self._conn = repo.conn
            self.repo = repo

        self._owns_http = http_client is None
        self.http = http_client or ThrottledHttpClient(config.network, proxy=config.resolved_proxy())
        self.suggest = suggest_collector or SuggestCollector(self.http)
        self.charts = charts_collector or ChartsCollector(self.http)
        self.serp = serp_collector or SerpCollector(self.http)
        self.app_detail = app_detail_collector or AppDetailCollector(self.http)
        self.trends = trends_collector or TrendsCollector(
            self.http, timeframe=config.funnel.trends_timeframe
        )

    def close(self) -> None:
        if hasattr(self.trends, "close"):
            self.trends.close()
        if self._owns_http:
            self.http.close()
        if self._owns_conn:
            self._conn.close()

    def _select_markets(
        self,
        market_filter: str | None = None,
        region_filter: str | None = None,
    ) -> list[MarketConfig]:
        selected = list(self.cfg.markets)
        if region_filter:
            regions = {r.strip().lower() for r in region_filter.split(",") if r.strip()}
            selected = [m for m in selected if m.region in regions]
            if not selected:
                raise ValueError(f"Region {region_filter!r} not found in config.yaml")
        if market_filter:
            targets = {t.strip().lower() for t in market_filter.split(",") if t.strip()}
            selected = [m for m in selected if m.country in targets]
            if not selected:
                raise ValueError(f"Market {market_filter!r} not found in config.yaml")
        return selected

    def run_daily(
        self,
        *,
        run_date: date | None = None,
        market_filter: str | None = None,
        region_filter: str | None = None,
        skip_trends: bool = False,
        skip_push: bool = False,
    ) -> list[RunSummary]:
        ref_date = run_date or date.today()
        run_date_str = ref_date.isoformat()
        selected_markets = self._select_markets(market_filter, region_filter)
        summaries: list[RunSummary] = []

        for market in selected_markets:
            summary = self._run_daily_for_market(
                market=market,
                ref_date=ref_date,
                run_date_str=run_date_str,
                skip_trends=skip_trends,
                skip_push=True,  # Consolidated push handled once after all markets finish
            )
            summaries.append(summary)

        global_path = self.generate_global_summary(run_date=ref_date)
        if global_path and summaries:
            summaries[-1].global_summary_path = global_path

        # Build DingTalk card preview (consolidated global card when >=2 markets, single-market card otherwise)
        payload = self._build_daily_dingtalk_payload(
            run_date_str=run_date_str,
            summaries=summaries,
        )
        if payload:
            preview_dir = self.cfg.storage.reports_dir / "daily"
            preview_dir.mkdir(parents=True, exist_ok=True)
            preview_path = preview_dir / f"{run_date_str}_dingtalk_preview.md"
            preview_path.write_text(payload["markdown"]["text"], encoding="utf-8")

            if self.cfg.reporting.dingtalk.enabled and self.cfg.reporting.dingtalk.webhook_url and not skip_push:
                try:
                    post_dingtalk_payload(self.cfg.reporting.dingtalk, payload)
                    logger.info("DingTalk summary pushed successfully.")
                except Exception as exc:
                    logger.warning("DingTalk push failed: %s", exc)
                    if summaries:
                        summaries[-1].degraded_sources.append("dingtalk_push_failed")

        return summaries

    def _build_daily_dingtalk_payload(
        self,
        *,
        run_date_str: str,
        summaries: list[RunSummary],
    ) -> dict | None:
        if not summaries:
            return None
        scores_all = self.repo.get_scores_for_date_all_markets(run_date_str)
        charts_all = self.repo.get_charts_for_date_all_markets(run_date_str)
        markets_map = {m.country: m for m in self.cfg.markets}
        blacklist = set(self.cfg.scoring.blacklist_terms)

        dark_horses_by_market: dict[str, list[DarkHorseApp]] = {}
        for (country, lang), chart_apps in charts_all.items():
            prev_date, prev_ranks = self.repo.get_latest_previous_chart_ranks(
                run_date_str, country, lang
            )
            dark_horses_by_market[country] = detect_chart_dark_horses(
                today_charts=chart_apps,
                prev_ranks=prev_ranks,
                charts_cfg=self.cfg.charts,
                has_prev_snapshot=prev_date is not None,
                blacklist=blacklist,
                lang=lang,
            )

        countries_present = {s.country for s in scores_all} | set(dark_horses_by_market.keys())
        degraded_map = {s.country: s.degraded_sources for s in summaries if s.degraded_sources}
        if len(countries_present) >= 2:
            return build_dingtalk_global_payload(
                run_date=run_date_str,
                markets_map=markets_map,
                scores_all=scores_all,
                dark_horses_by_market=dark_horses_by_market,
                degraded_by_country=degraded_map,
            )

        only = summaries[0]
        market = markets_map.get(only.country, MarketConfig(country=only.country, lang=only.lang))
        market_scores = [s for s in scores_all if s.country == only.country]
        return build_dingtalk_payload(
            run_date=run_date_str,
            market=market,
            run_mode=only.mode,
            scores=market_scores,
            dark_horses=dark_horses_by_market.get(only.country, []),
            degraded_sources=only.degraded_sources,
            top_n=self.cfg.reporting.dingtalk.top_n,
        )

    def generate_global_summary(self, *, run_date: date | None = None) -> str | None:
        """Build cross-market summary report if >=2 markets have data for run_date."""
        ref_date = run_date or date.today()
        run_date_str = ref_date.isoformat()
        scores_all = self.repo.get_scores_for_date_all_markets(run_date_str)
        charts_all = self.repo.get_charts_for_date_all_markets(run_date_str)
        countries_present = {s.country for s in scores_all} | {c for c, _ in charts_all.keys()}
        if len(countries_present) < 2:
            return None

        blacklist = set(self.cfg.scoring.blacklist_terms)
        dark_horses_by_market: dict[str, list[DarkHorseApp]] = {}
        for (country, lang), chart_apps in charts_all.items():
            prev_date, prev_ranks = self.repo.get_latest_previous_chart_ranks(
                run_date_str, country, lang
            )
            dark_horses_by_market[country] = detect_chart_dark_horses(
                today_charts=chart_apps,
                prev_ranks=prev_ranks,
                charts_cfg=self.cfg.charts,
                has_prev_snapshot=prev_date is not None,
                blacklist=blacklist,
                lang=lang,
            )

        markets_map = {m.country: m for m in self.cfg.markets}
        md = render_global_summary_report(
            run_date=run_date_str,
            markets_map=markets_map,
            scores_all=scores_all,
            dark_horses_by_market=dark_horses_by_market,
        )
        out_dir = self.cfg.storage.reports_dir / "daily"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{run_date_str}_global_summary.md"
        out_path.write_text(md, encoding="utf-8")
        return str(out_path)

    def _run_daily_for_market(
        self,
        *,
        market: MarketConfig,
        ref_date: date,
        run_date_str: str,
        skip_trends: bool,
        skip_push: bool,
    ) -> RunSummary:
        run_id = f"{run_date_str}_{market.country}_{uuid.uuid4().hex[:6]}"
        self.repo.start_run(run_id, run_date_str, "daily", market.country, market.lang)
        degraded: list[str] = []
        blacklist = set(self.cfg.scoring.blacklist_terms)
        start_reqs = self.http.requests_used

        try:
            # --- Stage 1A: Category Charts ---
            logger.info("[%s] Stage 1A: Fetching category charts (%d categories)...", market.country.upper(), len(self.cfg.charts.categories))
            if self.cfg.charts.enabled:
                completed_charts = self.repo.get_completed_charts(
                    run_date_str, market.country, market.lang
                )
                for cat in self.cfg.charts.categories:
                    for coll in self.cfg.charts.collections:
                        if (cat, coll) in completed_charts:
                            continue
                        count = (
                            self.cfg.charts.top_n_free
                            if coll == "topselling_free"
                            else self.cfg.charts.top_n_grossing
                        )
                        try:
                            records = self.charts.fetch_chart(
                                category=cat,
                                collection=coll,
                                count=count,
                                country=market.country,
                                lang=market.lang,
                            )
                            self.repo.save_chart_batch(run_date_str, market.country, market.lang, records)
                        except RequestBudgetExceeded:
                            degraded.append("budget_exhausted_in_charts")
                            break
                        except Exception as exc:
                            logger.warning("Chart fetch failed for %s/%s: %s", coll, cat, exc)
                            degraded.append(f"charts:{cat}:{coll}")

            today_charts: list[ChartAppRecord] = self.repo.get_charts_for_date(
                run_date_str, market.country, market.lang
            )
            prev_chart_date, prev_chart_ranks = self.repo.get_latest_previous_chart_ranks(
                run_date_str, market.country, market.lang
            )
            dark_horses: list[DarkHorseApp] = detect_chart_dark_horses(
                today_charts=today_charts,
                prev_ranks=prev_chart_ranks,
                charts_cfg=self.cfg.charts,
                has_prev_snapshot=prev_chart_date is not None,
                blacklist=blacklist,
                lang=market.lang,
            )

            max_chart_phrases = (
                market.max_chart_phrases_to_suggest
                if market.max_chart_phrases_to_suggest is not None
                else self.cfg.funnel.max_chart_phrases_to_suggest
            )
            max_serp_candidates = (
                market.max_serp_candidates
                if market.max_serp_candidates is not None
                else self.cfg.funnel.max_serp_candidates
            )
            max_trends_candidates = (
                market.max_trends_candidates
                if market.max_trends_candidates is not None
                else self.cfg.funnel.max_trends_candidates
            )

            # --- Stage 1B: Seed Suggest + Dark-Horse Phrase Two-Hop Suggest ---
            logger.info("[%s] Found %d chart apps (%d dark horses). Stage 1B: Fetching Suggest...", market.country.upper(), len(today_charts), len(dark_horses))
            if self.cfg.suggest.enabled:
                completed_seeds = self.repo.get_completed_suggest_seeds(
                    run_date_str, market.country, market.lang
                )
                seed_queries = build_seed_queries(
                    self.cfg.suggest,
                    ref_date,
                    lang=market.lang,
                    expand_az_override=market.expand_az,
                    az_letters_override=market.az_letters_per_day,
                )

                # Feed top chart dark-horse phrases back into Suggest to discover real search demand!
                seen_q = {q for q, _ in seed_queries}
                added_dh = 0
                for dh in dark_horses:
                    if added_dh >= max_chart_phrases:
                        break
                    for phrase in dh.extracted_phrases:
                        if phrase not in seen_q:
                            seen_q.add(phrase)
                            seed_queries.append((phrase, f"darkhorse_{dh.category.lower()}"))
                            added_dh += 1
                            if added_dh >= max_chart_phrases:
                                break

                suggest_failures = 0
                for seed_q, grp in seed_queries:
                    if seed_q in completed_seeds:
                        continue
                    try:
                        suggestions = self.suggest.fetch(seed_q, country=market.country, lang=market.lang)
                        batch = [
                            SuggestRecord(
                                seed_query=seed_q,
                                suggestion=s,
                                rank=idx,
                                group_name=grp,
                            )
                            for idx, s in enumerate(suggestions, start=1)
                        ]
                        self.repo.save_suggest_batch(run_date_str, market.country, market.lang, batch)
                    except RequestBudgetExceeded:
                        degraded.append("budget_exhausted_in_suggest")
                        break
                    except Exception as exc:
                        suggest_failures += 1
                        logger.warning("Suggest fetch failed for %r: %s", seed_q, exc)
                        if suggest_failures >= 8:
                            degraded.append("suggest_high_error_rate")
                            break

            today_suggests = self.repo.get_suggest_for_date(run_date_str, market.country, market.lang)
            history_suggests = self.repo.get_suggest_history(
                market.country, market.lang, before_date=run_date_str, lookback_days=7
            )

            # --- Stage 2: Filter, Diff & Pre-Rank Candidates ---
            run_mode, candidates = build_keyword_candidates(
                today_suggests=today_suggests,
                history_suggests=history_suggests,
                dark_horses=dark_horses,
                blacklist=blacklist,
                lang=market.lang,
            )

            top_candidates = candidates[:max_serp_candidates]
            logger.info(
                "[%s] Stage 2 complete: mode=%s, %d total candidates -> enriching Top %d via SERP & App Details...",
                market.country.upper(),
                run_mode,
                len(candidates),
                len(top_candidates),
            )

            # --- Stage 3A: SERP Top N + Cached App Details ---
            serp_by_kw: dict[str, list] = {}
            details_map: dict[str, AppDetailRecord] = {}
            serp_failures = 0

            for cand in top_candidates:
                existing_serp = self.repo.get_serp_for_keyword(
                    run_date_str, market.country, market.lang, cand.keyword
                )
                if existing_serp:
                    serp_apps = existing_serp
                else:
                    try:
                        serp_apps = self.serp.search(
                            cand.keyword,
                            country=market.country,
                            lang=market.lang,
                            top_n=self.cfg.funnel.serp_top_n,
                        )
                        self.repo.save_serp_batch(run_date_str, market.country, market.lang, serp_apps)
                    except RequestBudgetExceeded:
                        degraded.append("budget_exhausted_in_serp")
                        break
                    except Exception as exc:
                        serp_failures += 1
                        logger.warning("SERP failed for %r: %s", cand.keyword, exc)
                        if serp_failures >= 5:
                            degraded.append("serp_degraded")
                            break
                        continue

                serp_by_kw[cand.keyword] = serp_apps

                # Enrich top apps using 7-day SQLite English-metadata cache (consistent released dates + cross-market reuse)
                for app in serp_apps[: self.cfg.funnel.serp_top_n]:
                    if app.app_id in details_map:
                        continue
                    cached = self.repo.get_cached_app(
                        app.app_id,
                        market.country,
                        "en",
                        ttl_days=self.cfg.storage.app_cache_ttl_days,
                    )
                    if cached:
                        details_map[app.app_id] = cached
                        continue
                    try:
                        detail = self.app_detail.fetch(
                            app.app_id,
                            country=market.country,
                            lang="en",
                            ref_date=ref_date,
                        )
                        if detail:
                            self.repo.save_app_detail(detail)
                            details_map[app.app_id] = detail
                    except RequestBudgetExceeded:
                        degraded.append("budget_exhausted_in_app_detail")
                        break

            # Preliminary scoring before Trends to select the highest-potential non-brand terms for Trends
            prelim_scores: list[KeywordScoreRecord] = []
            for cand in top_candidates:
                if cand.keyword not in serp_by_kw:
                    continue
                prelim_scores.append(
                    score_keyword(
                        cand,
                        serp_by_kw[cand.keyword],
                        details_map,
                        trends=None,
                        run_date=run_date_str,
                        country=market.country,
                        lang=market.lang,
                        run_mode=run_mode,
                        scoring_cfg=self.cfg.scoring,
                    )
                )
            prelim_scores.sort(key=lambda s: (-s.total_score, s.keyword))

            # --- Stage 3B: Google Trends Cross-Validation (Top non-brand candidates only) ---
            logger.info("[%s] Stage 3B: Validating top non-brand keywords via Google Trends...", market.country.upper())
            trends_by_kw: dict[str, TrendsRecord] = {}
            trends_enabled = self.cfg.funnel.trends_enabled and not skip_trends
            if trends_enabled:
                trends_targets = [s.keyword for s in prelim_scores if not s.is_brand][
                    :max_trends_candidates
                ]
                for kw in trends_targets:
                    cached_tr = self.repo.get_trends_for_keyword(
                        run_date_str, market.country, market.lang, kw
                    )
                    if cached_tr:
                        trends_by_kw[kw] = cached_tr
                        continue
                    try:
                        tr = self.trends.fetch_keyword(kw, country=market.country, lang=market.lang)
                        self.repo.save_trends(run_date_str, market.country, market.lang, tr)
                        trends_by_kw[kw] = tr
                    except RequestBudgetExceeded:
                        degraded.append("budget_exhausted_in_trends")
                        break
                if getattr(self.trends, "degraded", False):
                    degraded.append("google_trends_429")
            elif skip_trends:
                degraded.append("google_trends_skipped")

            # --- Stage 4: Final Scoring & Persistence ---
            cand_by_kw = {c.keyword: c for c in top_candidates}
            final_scores: list[KeywordScoreRecord] = []
            for kw, serp_apps in serp_by_kw.items():
                final_scores.append(
                    score_keyword(
                        cand_by_kw[kw],
                        serp_apps,
                        details_map,
                        trends=trends_by_kw.get(kw),
                        run_date=run_date_str,
                        country=market.country,
                        lang=market.lang,
                        run_mode=run_mode,
                        scoring_cfg=self.cfg.scoring,
                    )
                )
            final_scores.sort(key=lambda s: (-s.total_score, s.keyword))
            self.repo.save_keyword_scores(final_scores)

            # --- Stage 5: Render Markdown Report & Optional DingTalk Push ---
            degraded_unique = list(dict.fromkeys(degraded))
            reqs_delta = self.http.requests_used - start_reqs
            report_md = render_daily_report(
                run_date=run_date_str,
                market=market,
                run_mode=run_mode,
                scores=final_scores,
                dark_horses=dark_horses,
                stats={
                    "requests_used": reqs_delta,
                    "suggest_count": len(today_suggests),
                    "chart_apps_count": len(today_charts),
                    "candidates_total": len(candidates),
                    "enriched_count": len(final_scores),
                },
                degraded_sources=degraded_unique,
                reporting_cfg=self.cfg.reporting,
            )

            report_dir = self.cfg.storage.reports_dir / "daily"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_path = report_dir / f"{run_date_str}_{market.country}.md"
            report_path.write_text(report_md, encoding="utf-8")

            if self.cfg.reporting.dingtalk.enabled and not skip_push:
                try:
                    single_payload = build_dingtalk_payload(
                        run_date=run_date_str,
                        market=market,
                        run_mode=run_mode,
                        scores=final_scores,
                        dark_horses=dark_horses,
                        degraded_sources=degraded_unique,
                        top_n=self.cfg.reporting.dingtalk.top_n,
                    )
                    post_dingtalk_payload(self.cfg.reporting.dingtalk, single_payload)
                except Exception as exc:
                    logger.warning("DingTalk push failed: %s", exc)
                    degraded_unique.append("dingtalk_push_failed")

            non_brand_count = sum(1 for s in final_scores if not s.is_brand)
            status = "degraded" if degraded_unique else "success"
            self.repo.finish_run(
                run_id=run_id,
                status=status,
                requests_used=reqs_delta,
                degraded_sources=degraded_unique,
                notes=f"report={report_path}",
            )
            return RunSummary(
                run_id=run_id,
                run_date=run_date_str,
                mode=run_mode,
                country=market.country,
                lang=market.lang,
                status=status,
                requests_used=reqs_delta,
                degraded_sources=degraded_unique,
                suggest_count=len(today_suggests),
                chart_apps_count=len(today_charts),
                dark_horse_count=len(dark_horses),
                enriched_keywords_count=len(final_scores),
                scored_opportunities_count=non_brand_count,
                report_path=str(report_path),
            )
        except Exception as exc:
            self.repo.finish_run(
                run_id=run_id,
                status="failed",
                requests_used=self.http.requests_used - start_reqs,
                degraded_sources=degraded,
                notes=str(exc),
            )
            raise

    def run_weekly(
        self,
        *,
        end_date: date | None = None,
        market_filter: str | None = None,
        region_filter: str | None = None,
    ) -> list[str]:
        ref_end = end_date or date.today()
        ref_start = ref_end - timedelta(days=6)
        report_paths: list[str] = []

        for market in self._select_markets(market_filter, region_filter):
            scores = self.repo.get_scores_in_range(
                market.country, market.lang, ref_start.isoformat(), ref_end.isoformat()
            )
            md = render_weekly_report(
                start_date=ref_start.isoformat(),
                end_date=ref_end.isoformat(),
                market=market,
                scores=scores,
                reporting_cfg=self.cfg.reporting,
            )
            out_dir = self.cfg.storage.reports_dir / "weekly"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{ref_end.isoformat()}_7d_{market.country}.md"
            out_path.write_text(md, encoding="utf-8")
            report_paths.append(str(out_path))
        return report_paths

    def inspect_keyword(
        self,
        keyword: str,
        *,
        country: str = "us",
        lang: str = "en",
        skip_trends: bool = False,
    ) -> tuple[KeywordScoreRecord, list[str]]:
        """Ad-hoc deep inspection of a single keyword right now."""
        norm_kw = normalize_keyword(keyword)
        ref_date = date.today()
        suggestions = self.suggest.fetch(norm_kw, country=country, lang=lang)
        serp_apps = self.serp.search(
            norm_kw, country=country, lang=lang, top_n=self.cfg.funnel.serp_top_n
        )
        details_map: dict[str, AppDetailRecord] = {}
        for app in serp_apps:
            cached = self.repo.get_cached_app(
                app.app_id, country, "en", ttl_days=self.cfg.storage.app_cache_ttl_days
            )
            if cached:
                details_map[app.app_id] = cached
            else:
                detail = self.app_detail.fetch(app.app_id, country=country, lang="en", ref_date=ref_date)
                if detail:
                    self.repo.save_app_detail(detail)
                    details_map[app.app_id] = detail

        trends_rec = None
        if not skip_trends:
            trends_rec = self.trends.fetch_keyword(norm_kw, country=country, lang=lang)

        rank_in_suggest = (suggestions.index(norm_kw) + 1) if norm_kw in suggestions else None
        cand = KeywordCandidate(
            keyword=norm_kw,
            sources=["manual_inspect"],
            best_suggest_rank=rank_in_suggest,
            prefix_count=1 if suggestions else 0,
        )
        scored = score_keyword(
            cand,
            serp_apps,
            details_map,
            trends_rec,
            run_date=ref_date.isoformat(),
            country=country,
            lang=lang,
            run_mode="baseline",
            scoring_cfg=self.cfg.scoring,
        )
        return scored, suggestions
