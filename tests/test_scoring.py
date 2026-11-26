from __future__ import annotations

import unittest

from gplay_radar.config import ScoringConfig
from gplay_radar.models import (
    AppDetailRecord,
    KeywordCandidate,
    SerpAppRecord,
    TrendsRecord,
)
from gplay_radar.pipeline.scoring import detect_brand_keyword, score_keyword


def _make_app(rank: int, app_id: str, title: str, dev: str, installs: int, age_days: int, ratings: int = 5000):
    serp = SerpAppRecord("kw", rank, app_id, title, dev, f"{installs:,}+", installs, 4.5)
    detail = AppDetailRecord(
        app_id=app_id,
        country="us",
        lang="en",
        title=title,
        developer=dev,
        genre_id="TOOLS",
        released="2026-07-01",
        age_days=age_days,
        installs_str=f"{installs:,}+",
        real_installs=installs,
        ratings=ratings,
        score=4.5,
        summary="",
        updated_ts=None,
        fetched_at="2026-11-26T00:00:00+00:00",
    )
    return serp, detail


class TestScoring(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ScoringConfig(blacklist_terms=["tiktok", "google"])

    def test_auto_brand_detection_vs_generic_demand(self) -> None:
        # Brand keyword: "coinscope" matches #1 brand prefix and 0 other top apps use "coinscope"
        pairs = [
            _make_app(1, "com.coinscope", "CoinScope: AI Identifier", "SpeakApps", 250_000, 120, 4000),
            _make_app(2, "com.coin2", "CoinSnap - Coin Identifier", "NextVision", 5_000_000, 900, 80_000),
            _make_app(3, "com.coin3", "Rare Coin Value Scanner", "IndieLab", 120_000, 150, 1200),
        ]
        serp = [p[0] for p in pairs]
        details = {p[1].app_id: p[1] for p in pairs}

        is_brand, reason = detect_brand_keyword("coinscope", serp, details, set(self.cfg.blacklist_terms), self.cfg)
        self.assertTrue(is_brand)
        self.assertIn("SERP #1", reason)

        # Generic keyword: "coin identifier" should NOT be flagged as brand
        is_brand2, _ = detect_brand_keyword("coin identifier", serp, details, set(self.cfg.blacklist_terms), self.cfg)
        self.assertFalse(is_brand2)

        # Generic category title on #1 app ("Document Reader - PDF Editor") should NOT flag "document reader" as brand
        doc_pairs = [
            _make_app(1, "com.doc1", "Document Reader - PDF Editor", "AllDocs Lab", 75_000_000, 1500, 400_000),
            _make_app(2, "com.doc2", "All Document Viewer & Office", "ToolBox", 10_000_000, 900, 50_000),
            _make_app(3, "com.doc3", "Document Scanner & File Manager", "IndieCo", 500_000, 200, 3_000),
        ]
        is_brand3, _ = detect_brand_keyword(
            "document reader",
            [p[0] for p in doc_pairs],
            {p[1].app_id: p[1] for p in doc_pairs},
            set(self.cfg.blacklist_terms),
            self.cfg,
        )
        self.assertFalse(is_brand3)

    def test_open_opportunity_scores_much_higher_than_monopoly(self) -> None:
        # Open niche: fresh apps (<180d), small installs (<500k), rising Google Trends
        open_pairs = [
            _make_app(i, f"com.new{i}", f"Indie Tool {i}", f"Dev{i}", 180_000, 110)
            for i in range(1, 11)
        ]
        open_cand = KeywordCandidate(
            keyword="coin value scanner",
            sources=["suggest:tools", "chart:TOOLS"],
            best_suggest_rank=2,
            is_new_in_suggest=True,
            prefix_count=2,
            chart_apps=[{"app_id": "com.new1", "rank_delta": 22}],
        )
        rising_tr = TrendsRecord("coin value scanner", 70.0, 30.0, 133.3, 80, 100, status="ok")
        open_score = score_keyword(
            open_cand,
            [p[0] for p in open_pairs],
            {p[1].app_id: p[1] for p in open_pairs},
            rising_tr,
            run_date="2026-11-26",
            country="us",
            lang="en",
            run_mode="diff",
            scoring_cfg=self.cfg,
        )

        # Monopolized evergreen keyword: 100M+ 6-year-old giants all with exact title match
        giant_pairs = [
            _make_app(i, f"com.giant{i}", f"PDF Scanner Pro {i}", f"Corp{i}", 120_000_000, 2200, 900_000)
            for i in range(1, 11)
        ]
        stale_cand = KeywordCandidate(
            keyword="pdf scanner",
            sources=["suggest:tools"],
            best_suggest_rank=5,
            is_new_in_suggest=False,
            prefix_count=1,
        )
        flat_tr = TrendsRecord("pdf scanner", 50.0, 52.0, -3.8, 49, 60, status="ok")
        giant_score = score_keyword(
            stale_cand,
            [p[0] for p in giant_pairs],
            {p[1].app_id: p[1] for p in giant_pairs},
            flat_tr,
            run_date="2026-11-26",
            country="us",
            lang="en",
            run_mode="diff",
            scoring_cfg=self.cfg,
        )

        self.assertGreater(open_score.entry_score, 35.0)
        self.assertLess(giant_score.entry_score, 10.0)
        self.assertGreater(open_score.total_score, giant_score.total_score + 40.0)


if __name__ == "__main__":
    unittest.main()
