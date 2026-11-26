from __future__ import annotations

import unittest

from gplay_radar.config import ChartsConfig
from gplay_radar.models import ChartAppRecord, SuggestRecord
from gplay_radar.pipeline.diff import build_keyword_candidates, detect_chart_dark_horses
from gplay_radar.pipeline.normalize import (
    extract_phrases_from_chart_app,
    is_valid_candidate_keyword,
)


class TestNormalizeAndDiff(unittest.TestCase):
    def test_extract_phrases_from_chart_app(self) -> None:
        phrases = extract_phrases_from_chart_app(
            "CoinScope: AI Identifier",
            description="CoinScope helps you identify coins, learn about them, and keep track.",
        )
        self.assertIn("ai identifier", phrases)
        self.assertIn("coin identifier", phrases)

        phrases2 = extract_phrases_from_chart_app("QR & Barcode Scanner")
        self.assertIn("barcode scanner", phrases2)
        self.assertIn("qr scanner", phrases2)

        phrases3 = extract_phrases_from_chart_app("Cleanup: Phone Storage Cleaner")
        self.assertIn("phone storage cleaner", phrases3)

        # Ensure preposition fragments like "at home" or "workout at" are stripped cleanly
        phrases4 = extract_phrases_from_chart_app("Chair Workout at Home")
        self.assertIn("chair workout at home", phrases4)
        self.assertNotIn("at home", phrases4)
        self.assertNotIn("workout at", phrases4)

    def test_valid_candidate_filter(self) -> None:
        bl = {"tiktok", "whatsapp", "google"}
        self.assertTrue(is_valid_candidate_keyword("coin identifier free", bl))
        self.assertFalse(is_valid_candidate_keyword("tiktok video downloader", bl))
        self.assertFalse(is_valid_candidate_keyword("pdf scanner apk", bl))
        self.assertFalse(is_valid_candidate_keyword("pdf scanner how to cancel", bl))
        self.assertFalse(is_valid_candidate_keyword("free app", bl))

    def test_detect_dark_horses_baseline_and_diff(self) -> None:
        cfg = ChartsConfig(dark_horse_max_installs=1_000_000, dark_horse_rank_jump=15)
        apps = [
            ChartAppRecord("TOOLS", "topselling_free", 1, "com.coin", "CoinScope: AI Identifier", "Dev", "100,000+", 100_000, 4.2, "identify coins quickly"),
            ChartAppRecord("TOOLS", "topselling_free", 2, "com.giant", "Mega VPN", "Big", "100,000,000+", 100_000_000, 4.6, ""),
            ChartAppRecord("TOOLS", "topselling_free", 12, "com.surge", "Fast Scan: Receipt Scanner", "Indie", "5,000,000+", 5_000_000, 4.5, ""),
        ]
        # Day 1 baseline: only low-install top-rank triggers
        dh_day1 = detect_chart_dark_horses(apps, {}, cfg, has_prev_snapshot=False)
        self.assertEqual([h.app_id for h in dh_day1], ["com.coin"])

        # Day 2 diff: com.surge jumped from #40 -> #12 (+28)
        prev = {
            ("TOOLS", "topselling_free", "com.coin"): 3,
            ("TOOLS", "topselling_free", "com.giant"): 2,
            ("TOOLS", "topselling_free", "com.surge"): 40,
        }
        dh_day2 = detect_chart_dark_horses(apps, prev, cfg, has_prev_snapshot=True)
        self.assertEqual({h.app_id for h in dh_day2}, {"com.coin", "com.surge"})

    def test_build_candidates_diff_ranks_new_and_rising_higher(self) -> None:
        today = [
            SuggestRecord("coin", "coin identifier value", 1, "tools"),
            SuggestRecord("coin i", "coin identifier value", 2, "tools"),
            SuggestRecord("pdf", "pdf scanner", 1, "tools"),
        ]
        history = [
            {"run_date": "2026-11-25", "seed_query": "pdf", "suggestion": "pdf scanner", "rank": 1, "group_name": "tools"},
            {"run_date": "2026-11-24", "seed_query": "pdf", "suggestion": "pdf scanner", "rank": 1, "group_name": "tools"},
        ]
        mode, cands = build_keyword_candidates(today, history, dark_horses=[], blacklist=set())
        self.assertEqual(mode, "diff")
        self.assertEqual(cands[0].keyword, "coin identifier value")
        self.assertTrue(cands[0].is_new_in_suggest)
        self.assertEqual(cands[0].prefix_count, 2)


if __name__ == "__main__":
    unittest.main()
