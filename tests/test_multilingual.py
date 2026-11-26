from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from gplay_radar.collectors.suggest import build_seed_queries
from gplay_radar.config import ScoringConfig, SeedGroupConfig, SuggestConfig, load_config
from gplay_radar.models import (
    AppDetailRecord,
    DarkHorseApp,
    KeywordScoreRecord,
    MarketConfig,
    SerpAppRecord,
)
from gplay_radar.pipeline.normalize import (
    extract_phrases_from_chart_app,
    is_valid_candidate_keyword,
    normalize_keyword,
)
from gplay_radar.pipeline.scoring import detect_brand_keyword
from gplay_radar.reporting.markdown import render_global_summary_report


class TestMultilingualSupport(unittest.TestCase):
    def test_unicode_normalization_preserves_diacritics_and_arabic(self) -> None:
        self.assertEqual(normalize_keyword("Münzen Erkennen: KI-Scanner!"), "münzen erkennen ki scanner")
        self.assertEqual(normalize_keyword("Restaurar Fotos — ¡Grátis!"), "restaurar fotos grátis")
        self.assertEqual(normalize_keyword("PDF Tarayıcı & İndir"), "pdf tarayıcı indir")
        # Arabic tashkeel stripped while preserving Arabic letters
        self.assertEqual(normalize_keyword("مَاسِح PDF: تَنظِيف الهاتف"), "ماسح pdf تنظيف الهاتف")

    def test_multilingual_phrase_extraction_and_filtering(self) -> None:
        de_phrases = extract_phrases_from_chart_app("CoinScope: KI Münzen Erkennen", lang="de")
        self.assertIn("ki münzen erkennen", de_phrases)

        es_phrases = extract_phrases_from_chart_app("LimpiaPro: Limpiador de Teléfono", lang="es")
        self.assertIn("limpiador de teléfono", es_phrases)

        ar_phrases = extract_phrases_from_chart_app("تطبيق: ماسح المستندات السريع", lang="ar")
        self.assertTrue(any("ماسح" in p for p in ar_phrases))

        # Localized download noise filtered out
        self.assertFalse(is_valid_candidate_keyword("descargar pdf gratis", lang="es"))
        self.assertFalse(is_valid_candidate_keyword("تحميل تطبيق مجاني", lang="ar"))
        self.assertTrue(is_valid_candidate_keyword("ماسح pdf", lang="ar"))

    def test_arabic_skips_latin_az_expansion(self) -> None:
        cfg = SuggestConfig(
            enabled=True,
            az_letters_per_day=6,
            intent_prefixes_by_lang={"ar": ["ذكاء اصطناعي"]},
            seed_groups_by_lang={
                "ar": [SeedGroupConfig(name="core_ar", expand_az=True, seeds=["ماسح pdf"])]
            },
        )
        queries = [q for q, _ in build_seed_queries(cfg, date(2026, 11, 26), lang="ar")]
        self.assertIn("ماسح pdf", queries)
        self.assertIn("ذكاء اصطناعي ماسح pdf", queries)
        self.assertFalse(any(q.endswith(" a") or q.endswith(" b") for q in queries))

    def test_romance_and_arabic_generic_noun_not_flagged_as_brand(self) -> None:
        cfg = ScoringConfig(blacklist_terms=[])
        serp = [
            SerpAppRecord("escaner pdf", 1, "com.es1", "Escáner PDF - Documentos", "Dev1", "10,000,000+", 10_000_000, 4.6),
            SerpAppRecord("escaner pdf", 2, "com.es2", "CamScanner - PDF Escáner", "Dev2", "100,000,000+", 100_000_000, 4.7),
            SerpAppRecord("escaner pdf", 3, "com.es3", "PDF Editor y Fotos", "Dev3", "1,000,000+", 1_000_000, 4.5),
        ]
        details = {
            "com.es1": AppDetailRecord(
                "com.es1", "es", "en", "Escáner PDF", "Dev1", "TOOLS", "2020-01-01",
                2000, "10,000,000+", 12_000_000, 200_000, 4.6, "", None, "2026-11-26T00:00:00+00:00"
            )
        }
        is_brand, _ = detect_brand_keyword("escaner pdf", serp, details, set(), cfg, lang="es")
        self.assertFalse(is_brand)

    def test_config_loads_all_10_markets_and_global_summary(self) -> None:
        cfg_path = Path(__file__).resolve().parent.parent / "config.yaml"
        cfg = load_config(cfg_path)
        countries = {m.country for m in cfg.markets}
        self.assertEqual(countries, {"us", "de", "fr", "es", "br", "mx", "id", "in", "sa", "tr"})
        for m in cfg.markets:
            self.assertTrue(len(cfg.suggest.groups_for_lang(m.lang)) >= 1)

        # Render cross-market summary with a shared dark horse across US + DE + BR
        markets_map = {m.country: m for m in cfg.markets}
        scores = [
            KeywordScoreRecord("2026-11-26", "us", "en", "restore scan", "baseline", [], False, "", 25, 34, 12, 71.0, {}, []),
            KeywordScoreRecord("2026-11-26", "de", "de", "foto wiederherstellen", "baseline", [], False, "", 24, 30, 10, 64.0, {}, []),
        ]
        dh_map = {
            "us": [DarkHorseApp("com.photo.restore.scan", "Photo Restore Scan", "VIP", "TOOLS", "topselling_free", 15, None, None, "100,000+", 100_000, ["low"])],
            "de": [DarkHorseApp("com.photo.restore.scan", "Foto Wiederherstellen", "VIP", "TOOLS", "topselling_free", 9, None, None, "100,000+", 100_000, ["low"])],
            "br": [DarkHorseApp("com.photo.restore.scan", "Restaurar Fotos", "VIP", "TOOLS", "topselling_free", 12, None, None, "100,000+", 100_000, ["low"])],
        }
        md = render_global_summary_report(
            run_date="2026-11-26",
            markets_map=markets_map,
            scores_all=scores,
            dark_horses_by_market=dh_map,
        )
        self.assertIn("3国", md)
        self.assertIn("Photo Restore Scan", md)
        self.assertIn("foto wiederherstellen", md)


if __name__ == "__main__":
    unittest.main()
