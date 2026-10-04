"""SQLite schema and connection helper.

MVP subset of the original schema sketch (see chess-trainer-mvp-plan.md):
games + positions for the baseline, plus weekly_report so the trend over time
is actually queryable (the original plan omitted this, and the whole point of
the success metric is a week-over-week trend, not a single snapshot).

error_tags were added once the tagging phase started (2026-10-03). cards /
reviews were added 2026-10-04 for the FSRS drill loop, once the observation
period gave enough signal to start that phase (see the Chess Trainer project
note for why that was earlier than the original 4-6 week plan).
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS archive_sync (
    archive_url TEXT PRIMARY KEY,
    etag        TEXT,
    fetched_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chesscom_uuid       TEXT UNIQUE NOT NULL,
    chesscom_username   TEXT,           -- whose game this is - added for the review app,
                                         -- where username becomes a per-session input
    played_at           TEXT NOT NULL,  -- ISO timestamp, from end_time
    time_class          TEXT NOT NULL,
    my_colour           TEXT NOT NULL,  -- 'white' | 'black'
    result              TEXT NOT NULL,  -- 'win' | 'loss' | 'draw'
    my_rating           INTEGER,
    opp_rating          INTEGER,
    eco                 TEXT,           -- from PGN [ECO] header, not the chess.com URL field
    pgn                 TEXT NOT NULL,
    analysed_at         TEXT            -- NULL until analyze step has run on this game
);

CREATE TABLE IF NOT EXISTS positions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         INTEGER NOT NULL REFERENCES games(id),
    ply             INTEGER NOT NULL,   -- 1-indexed half-move number
    is_my_move      INTEGER NOT NULL,   -- 1 if this move was played by me, else 0
    san             TEXT NOT NULL,
    fen_before      TEXT NOT NULL,
    eval_cp_before  INTEGER,            -- from the mover's perspective, NULL if mate score
    eval_cp_after   INTEGER,
    win_pct_before  REAL,
    win_pct_after   REAL,
    win_pct_drop    REAL,               -- only meaningful when is_my_move = 1
    severity        TEXT,               -- 'ok' | 'inaccuracy' | 'mistake' | 'blunder'
    phase           TEXT,               -- 'opening' | 'middlegame' | 'endgame'
    clock_seconds   REAL,               -- clock remaining after this move, from %clk
    time_spent_s    REAL,               -- seconds spent on this move (prev clock - this clock)
    UNIQUE(game_id, ply)
);

CREATE TABLE IF NOT EXISTS lessons (
    id              TEXT PRIMARY KEY,   -- e.g. 'u1-03'
    unit            INTEGER NOT NULL,
    unit_name       TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    title           TEXT NOT NULL,
    objective       TEXT NOT NULL,
    start_fen       TEXT,               -- NULL for roadmap/concept lessons with no position yet
    exercise_type   TEXT NOT NULL,      -- 'tablebase_dtz' | 'concept' | 'roadmap'
    target_result   TEXT,               -- 'win' | 'draw' | 'loss' from the side-to-move's POV,
                                         -- filled by querying the tablebase at ingestion time -
                                         -- never hand-asserted, so it's always authoritative.
    verified_at     TEXT                -- when target_result was last confirmed against the API
);

CREATE TABLE IF NOT EXISTS lesson_progress (
    lesson_id       TEXT NOT NULL REFERENCES lessons(id),
    attempted_at    TEXT NOT NULL,
    passed          INTEGER NOT NULL,   -- 1 if the achieved result matched target_result
    moves_played    TEXT                -- SAN moves, space-separated, for later review
);

CREATE TABLE IF NOT EXISTS error_tags (
    game_id         INTEGER NOT NULL REFERENCES games(id),
    ply             INTEGER NOT NULL,
    tag             TEXT NOT NULL,      -- hung_piece | missed_capture | unsound_sacrifice |
                                         -- king_safety | ignored_threat | self_obstruction | wasted_tempo
    created_at      TEXT NOT NULL,
    UNIQUE(game_id, ply, tag)
);

-- Review sessions (the web app's "review my last N games" runs) must exist
-- before hand_labels, which references review_sessions(id).
CREATE TABLE IF NOT EXISTS review_sessions (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    chesscom_username TEXT NOT NULL,
    time_class        TEXT NOT NULL,
    n_games           INTEGER NOT NULL,
    max_positions     INTEGER,
    severities        TEXT NOT NULL,      -- comma-separated, e.g. 'blunder,mistake'
    created_at        TEXT NOT NULL,
    completed_at      TEXT
);

CREATE TABLE IF NOT EXISTS review_session_games (
    session_id INTEGER NOT NULL REFERENCES review_sessions(id),
    game_id    INTEGER NOT NULL REFERENCES games(id),
    PRIMARY KEY (session_id, game_id)
);

CREATE TABLE IF NOT EXISTS hand_labels (
    game_id         INTEGER NOT NULL,
    ply             INTEGER NOT NULL,
    label           TEXT,               -- the user's own plain-language diagnosis - optional,
                                         -- since a tag pick alone is also a valid label
    confidence      TEXT CHECK (confidence IN ('sure','unsure')),
    status          TEXT NOT NULL DEFAULT 'labeled' CHECK (status IN ('labeled','skipped')),
    source          TEXT NOT NULL DEFAULT 'web' CHECK (source IN ('worksheet_import','web')),
    source_file     TEXT,               -- which raw hand-label file this came from (legacy rows only)
    session_id      INTEGER REFERENCES review_sessions(id),
    seconds_spent   REAL,               -- time spent on this position
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (game_id, ply),
    FOREIGN KEY (game_id, ply) REFERENCES positions(game_id, ply)
);

-- Structured tag picks for a hand label, mirrors error_tags exactly so
-- comparing "you vs the tagger" is a plain join, not string parsing.
CREATE TABLE IF NOT EXISTS hand_label_tags (
    game_id  INTEGER NOT NULL,
    ply      INTEGER NOT NULL,
    tag      TEXT NOT NULL CHECK (tag IN ('hung_piece','missed_capture','unsound_sacrifice',
                 'king_safety','ignored_threat','self_obstruction','wasted_tempo','other')),
    PRIMARY KEY (game_id, ply, tag),
    FOREIGN KEY (game_id, ply) REFERENCES hand_labels(game_id, ply) ON DELETE CASCADE
);

-- Own-blunder drill loop (FSRS). One card per flagged position you've seen;
-- best_move/best_cp are computed once at card-build time so review itself
-- stays fast (one more engine call, for your move specifically).
CREATE TABLE IF NOT EXISTS cards (
    game_id          INTEGER NOT NULL,
    ply              INTEGER NOT NULL,
    best_move_uci    TEXT NOT NULL,
    best_move_san    TEXT NOT NULL,
    best_cp          INTEGER,            -- engine eval of the best move, mover's POV
    fsrs_state       TEXT NOT NULL,      -- JSON: fsrs.Card.to_dict()
    due              TEXT NOT NULL,      -- denormalized from fsrs_state for queryability
    reps             INTEGER NOT NULL DEFAULT 0,
    lapses           INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    last_reviewed_at TEXT,
    PRIMARY KEY (game_id, ply),
    FOREIGN KEY (game_id, ply) REFERENCES positions(game_id, ply)
);

CREATE TABLE IF NOT EXISTS reviews (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        INTEGER NOT NULL,
    ply            INTEGER NOT NULL,
    reviewed_at    TEXT NOT NULL,
    your_move_san  TEXT NOT NULL,
    rating         TEXT NOT NULL,       -- Again | Hard | Good | Easy
    seconds_spent  REAL,
    stability      REAL,
    difficulty     REAL,
    FOREIGN KEY (game_id, ply) REFERENCES cards(game_id, ply)
);

CREATE TABLE IF NOT EXISTS weekly_report (
    week_start         TEXT PRIMARY KEY,   -- ISO date, Monday of the week
    games_count        INTEGER NOT NULL,
    my_moves_count     INTEGER NOT NULL,
    blunders           INTEGER NOT NULL,
    mistakes           INTEGER NOT NULL,
    inaccuracies       INTEGER NOT NULL,
    blunders_per_100   REAL NOT NULL,
    by_phase_json       TEXT NOT NULL,     -- {"opening": {...}, "middlegame": {...}, "endgame": {...}}
    report_markdown_path TEXT,
    generated_at        TEXT NOT NULL
);
"""


def connect(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str, default_username: str | None = None) -> list[str]:
    """Creates a fresh DB at the latest schema, or brings an existing one
    up to date via migrations.py. Returns the list of migrations actually
    applied (empty for a fresh DB or one already current)."""
    from . import migrations  # local import: migrations doesn't need db at import time

    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        applied = migrations.run_migrations(conn, default_username=default_username)
        return applied
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
