"""Migrations for pre-existing DBs. See chess-trainer-review-app-plan.md §4.

db.py's SCHEMA already creates every table (including the new ones this adds)
in its final shape via CREATE TABLE IF NOT EXISTS, so a fresh DB needs nothing
here. The one thing SCHEMA can't fix on an existing DB is `hand_labels`
already existing in its old shape (label NOT NULL, no status/confidence/etc.)
and `games` already existing without `chesscom_username` - SQLite can't ALTER
a NOT NULL constraint away or do it in SCHEMA's idempotent style, so that
rebuild lives here, gated on detecting the old shape is still present.
"""
from __future__ import annotations

import sqlite3


def _hand_labels_needs_rebuild(conn: sqlite3.Connection) -> bool:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(hand_labels)")}
    return "status" not in cols  # 'status' only exists in the new shape


def _rebuild_hand_labels(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(
        """
        CREATE TABLE hand_labels_new (
            game_id        INTEGER NOT NULL,
            ply            INTEGER NOT NULL,
            label          TEXT,
            confidence     TEXT CHECK (confidence IN ('sure','unsure')),
            status         TEXT NOT NULL DEFAULT 'labeled' CHECK (status IN ('labeled','skipped')),
            source         TEXT NOT NULL DEFAULT 'web' CHECK (source IN ('worksheet_import','web')),
            source_file    TEXT,
            session_id     INTEGER REFERENCES review_sessions(id),
            seconds_spent  REAL,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (game_id, ply),
            FOREIGN KEY (game_id, ply) REFERENCES positions(game_id, ply)
        );
        """
    )
    conn.execute(
        """
        INSERT INTO hand_labels_new (game_id, ply, label, status, source, source_file, created_at, updated_at)
            SELECT game_id, ply, label, 'labeled', 'worksheet_import', source_file, created_at, created_at
            FROM hand_labels
        """
    )
    conn.execute("DROP TABLE hand_labels")
    conn.execute("ALTER TABLE hand_labels_new RENAME TO hand_labels")
    conn.execute("PRAGMA foreign_keys = ON")


def _ensure_games_username_column(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    if "chesscom_username" not in cols:
        conn.execute("ALTER TABLE games ADD COLUMN chesscom_username TEXT")


def run_migrations(conn: sqlite3.Connection, default_username: str | None = None) -> list[str]:
    """Idempotent: safe to call every time init_db() runs. Returns a list of
    migration names actually applied (empty if the DB was already current)."""
    applied = []

    if _hand_labels_needs_rebuild(conn):
        _rebuild_hand_labels(conn)
        applied.append("rebuild_hand_labels")

    before_cols = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
    _ensure_games_username_column(conn)
    if "chesscom_username" not in before_cols:
        applied.append("add_games_chesscom_username")
        if default_username:
            conn.execute(
                "UPDATE games SET chesscom_username = ? WHERE chesscom_username IS NULL",
                (default_username,),
            )

    conn.commit()
    return applied
