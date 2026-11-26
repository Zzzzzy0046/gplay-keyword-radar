from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from gplay_radar.models import (
    AppDetailRecord,
    ChartAppRecord,
    KeywordScoreRecord,
    SerpAppRecord,
    SuggestRecord,
    TrendsRecord,
)


class RadarRepository:
    """Data access layer for daily snapshots, historical windows, and app cache."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    # ---- Runs ----

    def start_run(self, run_id: str, run_date: str, mode: str, country: str, lang: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO runs
            (run_id, run_date, mode, country, lang, started_at, status)
            VALUES (?, ?, ?, ?, ?, ?, 'running')
            """,
            (run_id, run_date, mode, country, lang, datetime.now(timezone.utc).isoformat()),
        )
        self.conn.commit()

    def finish_run(
        self,
        run_id: str,
        status: str,
        requests_used: int,
        degraded_sources: list[str],
        notes: str = "",
    ) -> None:
        self.conn.execute(
            """
            UPDATE runs
            SET finished_at = ?, status = ?, requests_used = ?,
                degraded_sources_json = ?, notes = ?
            WHERE run_id = ?
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                status,
                requests_used,
                json.dumps(degraded_sources, ensure_ascii=False),
                notes,
                run_id,
            ),
        )
        self.conn.commit()

    # ---- Suggest ----

    def get_completed_suggest_seeds(self, run_date: str, country: str, lang: str) -> set[str]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT seed_query FROM suggest_snapshots
            WHERE run_date = ? AND country = ? AND lang = ?
            """,
            (run_date, country, lang),
        ).fetchall()
        return {r["seed_query"] for r in rows}

    def save_suggest_batch(
        self, run_date: str, country: str, lang: str, records: list[SuggestRecord]
    ) -> None:
        if not records:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO suggest_snapshots
            (run_date, country, lang, seed_query, suggestion, rank, group_name)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (run_date, country, lang, r.seed_query, r.suggestion, r.rank, r.group_name)
                for r in records
            ],
        )
        self.conn.commit()

    def get_suggest_for_date(self, run_date: str, country: str, lang: str) -> list[SuggestRecord]:
        rows = self.conn.execute(
            """
            SELECT seed_query, suggestion, rank, group_name
            FROM suggest_snapshots
            WHERE run_date = ? AND country = ? AND lang = ?
            """,
            (run_date, country, lang),
        ).fetchall()
        return [
            SuggestRecord(
                seed_query=r["seed_query"],
                suggestion=r["suggestion"],
                rank=r["rank"],
                group_name=r["group_name"],
            )
            for r in rows
        ]

    def get_suggest_history(
        self, country: str, lang: str, before_date: str, lookback_days: int = 7
    ) -> list[dict[str, Any]]:
        cutoff = (date.fromisoformat(before_date) - timedelta(days=lookback_days)).isoformat()
        rows = self.conn.execute(
            """
            SELECT run_date, seed_query, suggestion, rank, group_name
            FROM suggest_snapshots
            WHERE country = ? AND lang = ?
              AND run_date < ? AND run_date >= ?
            """,
            (country, lang, before_date, cutoff),
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- Charts ----

    def get_completed_charts(self, run_date: str, country: str, lang: str) -> set[tuple[str, str]]:
        rows = self.conn.execute(
            """
            SELECT DISTINCT category, collection FROM chart_snapshots
            WHERE run_date = ? AND country = ? AND lang = ?
            """,
            (run_date, country, lang),
        ).fetchall()
        return {(r["category"], r["collection"]) for r in rows}

    def save_chart_batch(
        self, run_date: str, country: str, lang: str, records: list[ChartAppRecord]
    ) -> None:
        if not records:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO chart_snapshots
            (run_date, country, lang, category, collection, rank, app_id, title,
             developer, installs_str, min_installs, score, description)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_date,
                    country,
                    lang,
                    r.category,
                    r.collection,
                    r.rank,
                    r.app_id,
                    r.title,
                    r.developer,
                    r.installs_str,
                    r.min_installs,
                    r.score,
                    r.description,
                )
                for r in records
            ],
        )
        self.conn.commit()

    def get_charts_for_date(self, run_date: str, country: str, lang: str) -> list[ChartAppRecord]:
        rows = self.conn.execute(
            """
            SELECT category, collection, rank, app_id, title, developer,
                   installs_str, min_installs, score, description
            FROM chart_snapshots
            WHERE run_date = ? AND country = ? AND lang = ?
            ORDER BY category, collection, rank
            """,
            (run_date, country, lang),
        ).fetchall()
        return [
            ChartAppRecord(
                category=r["category"],
                collection=r["collection"],
                rank=r["rank"],
                app_id=r["app_id"],
                title=r["title"],
                developer=r["developer"],
                installs_str=r["installs_str"],
                min_installs=r["min_installs"],
                score=r["score"],
                description=r["description"],
            )
            for r in rows
        ]

    def get_charts_for_date_all_markets(
        self, run_date: str
    ) -> dict[tuple[str, str], list[ChartAppRecord]]:
        rows = self.conn.execute(
            """
            SELECT country, lang, category, collection, rank, app_id, title, developer,
                   installs_str, min_installs, score, description
            FROM chart_snapshots
            WHERE run_date = ?
            ORDER BY country, category, collection, rank
            """,
            (run_date,),
        ).fetchall()
        grouped: dict[tuple[str, str], list[ChartAppRecord]] = {}
        for r in rows:
            key = (r["country"], r["lang"])
            grouped.setdefault(key, []).append(
                ChartAppRecord(
                    category=r["category"],
                    collection=r["collection"],
                    rank=r["rank"],
                    app_id=r["app_id"],
                    title=r["title"],
                    developer=r["developer"],
                    installs_str=r["installs_str"],
                    min_installs=r["min_installs"],
                    score=r["score"],
                    description=r["description"],
                )
            )
        return grouped

    def get_latest_previous_chart_ranks(
        self, before_date: str, country: str, lang: str
    ) -> tuple[str | None, dict[tuple[str, str, str], int]]:
        """Return (prev_date, {(category, collection, app_id): rank}) for most recent prior chart run."""
        row = self.conn.execute(
            """
            SELECT MAX(run_date) AS prev_date
            FROM chart_snapshots
            WHERE country = ? AND lang = ? AND run_date < ?
            """,
            (country, lang, before_date),
        ).fetchone()
        prev_date = row["prev_date"] if row and row["prev_date"] else None
        if not prev_date:
            return None, {}
        rows = self.conn.execute(
            """
            SELECT category, collection, app_id, rank
            FROM chart_snapshots
            WHERE run_date = ? AND country = ? AND lang = ?
            """,
            (prev_date, country, lang),
        ).fetchall()
        return prev_date, {(r["category"], r["collection"], r["app_id"]): r["rank"] for r in rows}

    # ---- App Cache ----

    def get_cached_app(
        self, app_id: str, country: str, lang: str, ttl_days: int = 7
    ) -> AppDetailRecord | None:
        row = self.conn.execute(
            """
            SELECT * FROM apps_cache
            WHERE app_id = ? AND country = ? AND lang = ?
            """,
            (app_id, country, lang),
        ).fetchone()
        if not row and lang == "en":
            # Reuse fresh English global metadata across countries to save HTTP requests
            row = self.conn.execute(
                """
                SELECT * FROM apps_cache
                WHERE app_id = ? AND lang = 'en'
                ORDER BY fetched_at DESC
                LIMIT 1
                """,
                (app_id,),
            ).fetchone()
        if not row:
            return None
        try:
            fetched_dt = datetime.fromisoformat(row["fetched_at"])
            if fetched_dt.tzinfo is None:
                fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - fetched_dt > timedelta(days=ttl_days):
                return None
        except ValueError:
            return None
        return AppDetailRecord(
            app_id=row["app_id"],
            country=row["country"],
            lang=row["lang"],
            title=row["title"],
            developer=row["developer"],
            genre_id=row["genre_id"],
            released=row["released"],
            age_days=row["age_days"],
            installs_str=row["installs_str"],
            real_installs=row["real_installs"],
            ratings=row["ratings"],
            score=row["score"],
            summary=row["summary"],
            updated_ts=row["updated_ts"],
            fetched_at=row["fetched_at"],
        )

    def save_app_detail(self, detail: AppDetailRecord) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO apps_cache
            (app_id, country, lang, title, developer, genre_id, released, age_days,
             installs_str, real_installs, ratings, score, summary, updated_ts, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                detail.app_id,
                detail.country,
                detail.lang,
                detail.title,
                detail.developer,
                detail.genre_id,
                detail.released,
                detail.age_days,
                detail.installs_str,
                detail.real_installs,
                detail.ratings,
                detail.score,
                detail.summary,
                detail.updated_ts,
                detail.fetched_at,
            ),
        )
        self.conn.commit()

    # ---- SERP ----

    def get_serp_for_keyword(
        self, run_date: str, country: str, lang: str, keyword: str
    ) -> list[SerpAppRecord]:
        rows = self.conn.execute(
            """
            SELECT keyword, rank, app_id, title, developer, installs_str, min_installs, score
            FROM serp_snapshots
            WHERE run_date = ? AND country = ? AND lang = ? AND keyword = ?
            ORDER BY rank
            """,
            (run_date, country, lang, keyword),
        ).fetchall()
        return [
            SerpAppRecord(
                keyword=r["keyword"],
                rank=r["rank"],
                app_id=r["app_id"],
                title=r["title"],
                developer=r["developer"],
                installs_str=r["installs_str"],
                min_installs=r["min_installs"],
                score=r["score"],
            )
            for r in rows
        ]

    def save_serp_batch(
        self, run_date: str, country: str, lang: str, records: list[SerpAppRecord]
    ) -> None:
        if not records:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO serp_snapshots
            (run_date, country, lang, keyword, rank, app_id, title, developer,
             installs_str, min_installs, score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    run_date,
                    country,
                    lang,
                    r.keyword,
                    r.rank,
                    r.app_id,
                    r.title,
                    r.developer,
                    r.installs_str,
                    r.min_installs,
                    r.score,
                )
                for r in records
            ],
        )
        self.conn.commit()

    # ---- Trends ----

    def get_trends_for_keyword(
        self, run_date: str, country: str, lang: str, keyword: str
    ) -> TrendsRecord | None:
        row = self.conn.execute(
            """
            SELECT * FROM trends_snapshots
            WHERE run_date = ? AND country = ? AND lang = ? AND keyword = ?
            """,
            (run_date, country, lang, keyword),
        ).fetchone()
        if not row:
            return None
        return TrendsRecord(
            keyword=row["keyword"],
            avg_7d=row["avg_7d"],
            avg_prev21d=row["avg_prev21d"],
            slope_pct=row["slope_pct"],
            latest_value=row["latest_value"],
            peak_value=row["peak_value"],
            series=json.loads(row["series_json"] or "[]"),
            status=row["status"],
        )

    def save_trends(self, run_date: str, country: str, lang: str, record: TrendsRecord) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO trends_snapshots
            (run_date, country, lang, keyword, avg_7d, avg_prev21d, slope_pct,
             latest_value, peak_value, series_json, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_date,
                country,
                lang,
                record.keyword,
                record.avg_7d,
                record.avg_prev21d,
                record.slope_pct,
                record.latest_value,
                record.peak_value,
                json.dumps(record.series, ensure_ascii=False),
                record.status,
            ),
        )
        self.conn.commit()

    # ---- Keyword Scores & Multi-Market Queries ----

    def save_keyword_scores(self, scores: list[KeywordScoreRecord]) -> None:
        if not scores:
            return
        self.conn.executemany(
            """
            INSERT OR REPLACE INTO keyword_scores
            (run_date, country, lang, keyword, run_mode, sources_json, is_brand,
             brand_reason, momentum_score, entry_score, trends_score, total_score,
             signals_json, top_apps_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    s.run_date,
                    s.country,
                    s.lang,
                    s.keyword,
                    s.run_mode,
                    json.dumps(s.sources, ensure_ascii=False),
                    1 if s.is_brand else 0,
                    s.brand_reason,
                    s.momentum_score,
                    s.entry_score,
                    s.trends_score,
                    s.total_score,
                    json.dumps(s.signals, ensure_ascii=False),
                    json.dumps(s.top_apps, ensure_ascii=False),
                )
                for s in scores
            ],
        )
        self.conn.commit()

    def _row_to_score(self, r: sqlite3.Row) -> KeywordScoreRecord:
        return KeywordScoreRecord(
            run_date=r["run_date"],
            country=r["country"],
            lang=r["lang"],
            keyword=r["keyword"],
            run_mode=r["run_mode"],
            sources=json.loads(r["sources_json"] or "[]"),
            is_brand=bool(r["is_brand"]),
            brand_reason=r["brand_reason"],
            momentum_score=r["momentum_score"],
            entry_score=r["entry_score"],
            trends_score=r["trends_score"],
            total_score=r["total_score"],
            signals=json.loads(r["signals_json"] or "{}"),
            top_apps=json.loads(r["top_apps_json"] or "[]"),
        )

    def get_scores_in_range(
        self, country: str, lang: str, start_date: str, end_date: str
    ) -> list[KeywordScoreRecord]:
        rows = self.conn.execute(
            """
            SELECT * FROM keyword_scores
            WHERE country = ? AND lang = ?
              AND run_date >= ? AND run_date <= ?
            ORDER BY run_date DESC, total_score DESC
            """,
            (country, lang, start_date, end_date),
        ).fetchall()
        return [self._row_to_score(r) for r in rows]

    def get_scores_for_date_all_markets(self, run_date: str) -> list[KeywordScoreRecord]:
        rows = self.conn.execute(
            """
            SELECT * FROM keyword_scores
            WHERE run_date = ?
            ORDER BY country, total_score DESC
            """,
            (run_date,),
        ).fetchall()
        return [self._row_to_score(r) for r in rows]
