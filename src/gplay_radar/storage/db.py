from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    run_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    requests_used INTEGER DEFAULT 0,
    degraded_sources_json TEXT DEFAULT '[]',
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS suggest_snapshots (
    run_date TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    seed_query TEXT NOT NULL,
    suggestion TEXT NOT NULL,
    rank INTEGER NOT NULL,
    group_name TEXT NOT NULL,
    PRIMARY KEY (run_date, country, lang, seed_query, suggestion)
);
CREATE INDEX IF NOT EXISTS idx_suggest_lookup
    ON suggest_snapshots (country, lang, run_date, suggestion);

CREATE TABLE IF NOT EXISTS chart_snapshots (
    run_date TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    category TEXT NOT NULL,
    collection TEXT NOT NULL,
    rank INTEGER NOT NULL,
    app_id TEXT NOT NULL,
    title TEXT NOT NULL,
    developer TEXT NOT NULL,
    installs_str TEXT DEFAULT '',
    min_installs INTEGER,
    score REAL,
    description TEXT DEFAULT '',
    PRIMARY KEY (run_date, country, lang, category, collection, rank)
);
CREATE INDEX IF NOT EXISTS idx_chart_app_lookup
    ON chart_snapshots (country, lang, category, collection, run_date, app_id);

CREATE TABLE IF NOT EXISTS apps_cache (
    app_id TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    title TEXT NOT NULL,
    developer TEXT NOT NULL,
    genre_id TEXT DEFAULT '',
    released TEXT,
    age_days INTEGER,
    installs_str TEXT DEFAULT '',
    real_installs INTEGER,
    ratings INTEGER,
    score REAL,
    summary TEXT DEFAULT '',
    updated_ts INTEGER,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (app_id, country, lang)
);

CREATE TABLE IF NOT EXISTS serp_snapshots (
    run_date TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    keyword TEXT NOT NULL,
    rank INTEGER NOT NULL,
    app_id TEXT NOT NULL,
    title TEXT NOT NULL,
    developer TEXT NOT NULL,
    installs_str TEXT DEFAULT '',
    min_installs INTEGER,
    score REAL,
    PRIMARY KEY (run_date, country, lang, keyword, rank)
);
CREATE INDEX IF NOT EXISTS idx_serp_lookup
    ON serp_snapshots (country, lang, run_date, keyword);

CREATE TABLE IF NOT EXISTS trends_snapshots (
    run_date TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    keyword TEXT NOT NULL,
    avg_7d REAL,
    avg_prev21d REAL,
    slope_pct REAL,
    latest_value INTEGER,
    peak_value INTEGER,
    series_json TEXT DEFAULT '[]',
    status TEXT NOT NULL,
    PRIMARY KEY (run_date, country, lang, keyword)
);

CREATE TABLE IF NOT EXISTS keyword_scores (
    run_date TEXT NOT NULL,
    country TEXT NOT NULL,
    lang TEXT NOT NULL,
    keyword TEXT NOT NULL,
    run_mode TEXT NOT NULL,
    sources_json TEXT DEFAULT '[]',
    is_brand INTEGER NOT NULL DEFAULT 0,
    brand_reason TEXT DEFAULT '',
    momentum_score REAL NOT NULL,
    entry_score REAL NOT NULL,
    trends_score REAL NOT NULL,
    total_score REAL NOT NULL,
    signals_json TEXT DEFAULT '{}',
    top_apps_json TEXT DEFAULT '[]',
    PRIMARY KEY (run_date, country, lang, keyword)
);
CREATE INDEX IF NOT EXISTS idx_scores_date
    ON keyword_scores (country, lang, run_date, total_score DESC);
"""


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    if str(db_path) != ":memory:":
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if str(db_path) != ":memory:":
        conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()
