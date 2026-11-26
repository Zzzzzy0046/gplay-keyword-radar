from __future__ import annotations

import json
import unittest
from datetime import date

from gplay_radar.collectors.charts import parse_charts_response
from gplay_radar.collectors.http_client import (
    SchemaChangedError,
    parse_installs,
    parse_released_date,
)
from gplay_radar.collectors.suggest import (
    build_seed_queries,
    parse_suggest_response,
    select_daily_az_letters,
)
from gplay_radar.collectors.trends import parse_trends_multiline
from gplay_radar.config import SeedGroupConfig, SuggestConfig


class TestParsers(unittest.TestCase):
    def test_parse_installs_and_released(self) -> None:
        self.assertEqual(parse_installs("100,000+"), 100_000)
        self.assertEqual(parse_installs("100,000,000+"), 100_000_000)
        self.assertIsNone(parse_installs(""))
        self.assertIsNone(parse_installs(None))

        iso, age = parse_released_date("May 31, 2026", ref_date=date(2026, 11, 26))
        self.assertEqual(iso, "2026-05-31")
        self.assertEqual(age, 179)

    def test_parse_suggest_response_valid_and_empty(self) -> None:
        inner = json.dumps([[[["pdf scanner"], ["pdf scanner free"], ["pdf scanner app"]]]])
        payload = ")]}'\n\n" + json.dumps([["wrb.fr", "IJ4APc", inner, None, None, None, "generic"]])
        self.assertEqual(
            parse_suggest_response(payload),
            ["pdf scanner", "pdf scanner free", "pdf scanner app"],
        )

        empty_payload = ")]}'\n\n" + json.dumps([["wrb.fr", "IJ4APc", "null", None, None, None, "generic"]])
        self.assertEqual(parse_suggest_response(empty_payload), [])

        with self.assertRaises(SchemaChangedError):
            parse_suggest_response("<html>unexpected error</html>")

    def test_parse_charts_response(self) -> None:
        item = [None] * 16
        item[0] = ["com.identify.track.coinscope", 7]
        item[3] = "CoinScope: AI Identifier"
        item[4] = ["4.2", 4.16]
        item[13] = [None, "CoinScope helps you identify coins.<br><br>Fast scan."]
        item[14] = "speakapps.net"
        item[15] = "100,000+"

        inner = [[None, [[None] * 28 + [[[ [item] ]]]]]]
        # Build exact path inner[0][1][0][28][0] -> [[item]]
        slot = [None] * 29
        slot[28] = [[[item]]]
        inner = [[None, [slot]]]
        payload = ")]}'\n\n" + json.dumps([["wrb.fr", "vyAe2", json.dumps(inner), None, None, None, "generic"]])

        rows = parse_charts_response(payload, category="TOOLS", collection="topselling_free")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].app_id, "com.identify.track.coinscope")
        self.assertEqual(rows[0].title, "CoinScope: AI Identifier")
        self.assertEqual(rows[0].min_installs, 100_000)
        self.assertEqual(rows[0].score, 4.16)
        self.assertEqual(rows[0].description, "CoinScope helps you identify coins. Fast scan.")

    def test_parse_trends_multiline_rising_slope(self) -> None:
        # 21 days at ~25, then 7 days at ~60, plus 1 trailing partial day that should be ignored
        timeline = [{"formattedAxisTime": f"D{i}", "value": [25], "isPartial": False} for i in range(21)]
        timeline += [{"formattedAxisTime": f"R{i}", "value": [60], "isPartial": False} for i in range(7)]
        timeline.append({"formattedAxisTime": "Today", "value": [10], "isPartial": True})
        body = ")]}',\n" + json.dumps({"default": {"timelineData": timeline}})

        rec = parse_trends_multiline(body, keyword="coin identifier")
        self.assertEqual(rec.status, "ok")
        self.assertEqual(rec.avg_7d, 60.0)
        self.assertEqual(rec.avg_prev21d, 25.0)
        self.assertEqual(rec.slope_pct, 140.0)
        self.assertEqual(rec.peak_value, 60)

    def test_az_rotation_and_seed_queries(self) -> None:
        day1 = select_daily_az_letters(date(2026, 11, 26), 6)
        day2 = select_daily_az_letters(date(2026, 11, 27), 6)
        self.assertEqual(len(day1), 6)
        self.assertNotEqual(day1, day2)

        cfg = SuggestConfig(
            enabled=True,
            az_letters_per_day=2,
            intent_prefixes=["ai"],
            seed_groups=[
                SeedGroupConfig(name="tools", expand_az=True, seeds=["pdf scanner", "ai translator"])
            ],
        )
        queries = build_seed_queries(cfg, date(2026, 11, 26))
        q_strs = [q for q, _ in queries]
        self.assertIn("pdf scanner", q_strs)
        self.assertIn("ai pdf scanner", q_strs)
        # "ai translator" already starts with "ai ", so should not generate "ai ai translator"
        self.assertNotIn("ai ai translator", q_strs)


if __name__ == "__main__":
    unittest.main()
