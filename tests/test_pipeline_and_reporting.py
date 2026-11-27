from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from gplay_radar.collectors.http_client import ThrottledHttpClient
from gplay_radar.config import (
    AppConfig,
    ChartsConfig,
    DingTalkConfig,
    FunnelConfig,
    NetworkConfig,
    ReportingConfig,
    ScoringConfig,
    SeedGroupConfig,
    StorageConfig,
    SuggestConfig,
)
from gplay_radar.models import (
    AppDetailRecord,
    ChartAppRecord,
    MarketConfig,
    SerpAppRecord,
    TrendsRecord,
)
from gplay_radar.pipeline.runner import RadarPipeline
from gplay_radar.reporting.dingtalk import (
    build_dingtalk_global_payload,
    build_dingtalk_payload,
    sign_dingtalk_webhook,
)
from gplay_radar.storage.db import connect_db, init_db
from gplay_radar.storage.repository import RadarRepository


class _FakeSuggest:
    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self.mapping = mapping

    def fetch(self, term: str, country: str = "us", lang: str = "en") -> list[str]:
        return self.mapping.get(term, [f"{term} free"])


class _FakeCharts:
    def __init__(self, apps: list[ChartAppRecord]) -> None:
        self.apps = apps

    def fetch_chart(self, category: str, collection: str, count: int, country: str, lang: str):
        return [a for a in self.apps if a.category == category and a.collection == collection]


class _FakeSerp:
    def search(self, keyword: str, country: str = "us", lang: str = "en", top_n: int = 10):
        return [
            SerpAppRecord(keyword, 1, "com.indie.coin", "Coin Finder Pro", "IndieDev", "100,000+", 100_000, 4.6),
            SerpAppRecord(keyword, 2, "com.indie.coin2", "Coin Value Check", "SmallLab", "50,000+", 50_000, 4.4),
        ]


class _FakeDetail:
    def fetch(self, app_id: str, country: str = "us", lang: str = "en", ref_date=None):
        return AppDetailRecord(
            app_id=app_id,
            country=country,
            lang=lang,
            title="Coin Finder Pro",
            developer="IndieDev",
            genre_id="TOOLS",
            released="2026-08-01",
            age_days=117,
            installs_str="100,000+",
            real_installs=140_000,
            ratings=1200,
            score=4.6,
            summary="Identify rare coins",
            updated_ts=None,
            fetched_at="2026-11-26T00:00:00+00:00",
        )


class _DegradedTrends:
    degraded = True

    def fetch_keyword(self, keyword: str, country: str = "us", lang: str = "en"):
        return TrendsRecord(keyword, None, None, None, None, None, status="unavailable")

    def close(self) -> None:
        pass


class TestPipelineAndReporting(unittest.TestCase):
    def test_end_to_end_day1_day2_weekly_and_dingtalk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = AppConfig(
                base_dir=tmp_path,
                storage=StorageConfig(db_path=tmp_path / "radar.db", reports_dir=tmp_path / "reports"),
                network=NetworkConfig(min_delay_seconds=0.0, max_delay_seconds=0.0, daily_request_budget=100),
                markets=[MarketConfig(country="us", lang="en", label="US (English)")],
                charts=ChartsConfig(
                    enabled=True,
                    collections=["topselling_free"],
                    categories=["TOOLS"],
                    dark_horse_max_installs=1_000_000,
                ),
                suggest=SuggestConfig(
                    enabled=True,
                    az_letters_per_day=0,
                    intent_prefixes=[],
                    seed_groups=[SeedGroupConfig(name="tools", expand_az=False, seeds=["coin identifier"])],
                ),
                funnel=FunnelConfig(max_chart_phrases_to_suggest=5, max_serp_candidates=10, max_trends_candidates=5),
                scoring=ScoringConfig(blacklist_terms=["tiktok"]),
                reporting=ReportingConfig(dingtalk=DingTalkConfig(enabled=False)),
            )

            conn = connect_db(":memory:")
            init_db(conn)
            repo = RadarRepository(conn)
            http = ThrottledHttpClient(cfg.network)

            day1_charts = [
                ChartAppRecord("TOOLS", "topselling_free", 1, "com.coinscope", "CoinScope: AI Identifier", "Speak", "100,000+", 100_000, 4.2, "identify coins easily"),
            ]
            pipe1 = RadarPipeline(
                cfg,
                repo=repo,
                http_client=http,
                suggest_collector=_FakeSuggest({"coin identifier": ["coin identifier", "coin identifier free"]}),
                charts_collector=_FakeCharts(day1_charts),
                serp_collector=_FakeSerp(),
                app_detail_collector=_FakeDetail(),
                trends_collector=_DegradedTrends(),
            )
            res1 = pipe1.run_daily(run_date=date(2026, 11, 25))
            self.assertEqual(len(res1), 1)
            self.assertEqual(res1[0].mode, "baseline")
            self.assertEqual(res1[0].status, "degraded")  # Trends degraded cleanly without crashing
            self.assertIn("google_trends_429", res1[0].degraded_sources)
            self.assertTrue(Path(res1[0].report_path).exists())
            md1 = Path(res1[0].report_path).read_text(encoding="utf-8")
            self.assertIn("CoinScope: AI Identifier", md1)
            self.assertIn("coin identifier", md1)

            # Day 2 run: now switches automatically to diff mode!
            pipe2 = RadarPipeline(
                cfg,
                repo=repo,
                http_client=http,
                suggest_collector=_FakeSuggest(
                    {"coin identifier": ["coin identifier value scanner", "coin identifier"]}
                ),
                charts_collector=_FakeCharts(day1_charts),
                serp_collector=_FakeSerp(),
                app_detail_collector=_FakeDetail(),
                trends_collector=_DegradedTrends(),
            )
            res2 = pipe2.run_daily(run_date=date(2026, 11, 26))
            self.assertEqual(res2[0].mode, "diff")
            md2 = Path(res2[0].report_path).read_text(encoding="utf-8")
            self.assertIn("历史异动对比", md2)
            self.assertIn("coin identifier value scanner", md2)

            # Weekly rollup
            weekly_paths = pipe2.run_weekly(end_date=date(2026, 11, 26))
            self.assertEqual(len(weekly_paths), 1)
            weekly_md = Path(weekly_paths[0]).read_text(encoding="utf-8")
            self.assertIn("持续机会热词周榜", weekly_md)

            # DingTalk signing & payload
            signed = sign_dingtalk_webhook("https://oapi.dingtalk.com/robot/send?access_token=abc", "SECtest", timestamp_ms=1700000000000)
            self.assertIn("timestamp=1700000000000&sign=", signed)
            scores = repo.get_scores_in_range("us", "en", "2026-11-26", "2026-11-26")
            payload = build_dingtalk_payload(
                run_date="2026-11-26",
                market=cfg.markets[0],
                run_mode="diff",
                scores=scores,
                dark_horses=[],
                degraded_sources=[],
            )
            self.assertEqual(payload["msgtype"], "markdown")
            self.assertIn("GP热词雷达", payload["markdown"]["title"])

            # Verify DingTalk preview file was saved automatically
            self.assertTrue((tmp_path / "reports" / "daily" / "2026-11-26_dingtalk_preview.md").exists())

            # Verify consolidated global DingTalk card format
            g_payload = build_dingtalk_global_payload(
                run_date="2026-11-26",
                markets_map={"us": cfg.markets[0]},
                scores_all=scores,
                dark_horses_by_market={"us": []},
            )
            self.assertEqual(g_payload["msgtype"], "markdown")
            self.assertIn("全球热词雷达", g_payload["markdown"]["title"])

            http.close()
            conn.close()


if __name__ == "__main__":
    unittest.main()
