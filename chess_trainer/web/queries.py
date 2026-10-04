"""Data access for the review app. All SQL lives here, kept separate from
app.py's routing so the queries are easy to find and test independent of
FastAPI. Raw sqlite3, no ORM - matches the rest of the codebase.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import chess

from ..tags import TAG_INFO

TAG_KEYS = list(TAG_INFO.keys())  # includes 'other', unlike tags.ALL_TAGS


def tag_catalog() -> list[dict]:
    return [
        {"key": k, "label": label, "tooltip": tooltip}
        for k, (label, tooltip) in TAG_INFO.items()
    ]


# --- Sessions ---------------------------------------------------------------

def create_session(
    conn: sqlite3.Connection,
    username: str,
    time_class: str,
    n_games: int,
    max_positions: int | None,
    severities: list[str],
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO review_sessions
               (chesscom_username, time_class, n_games, max_positions, severities, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (username, time_class, n_games, max_positions, ",".join(severities), now),
    )
    conn.commit()
    return cur.lastrowid


def link_session_games(conn: sqlite3.Connection, session_id: int, game_ids: list[int]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO review_session_games (session_id, game_id) VALUES (?, ?)",
        [(session_id, gid) for gid in game_ids],
    )
    conn.commit()


def pick_recent_games(
    conn: sqlite3.Connection, username: str, time_class: str, n_games: int
) -> list[int]:
    """The N most recent games for this username/time_class already in the DB
    (call after syncing, which fetches newest-first up to n_games)."""
    rows = conn.execute(
        """SELECT id FROM games WHERE chesscom_username = ? AND time_class = ?
           ORDER BY played_at DESC LIMIT ?""",
        (username, time_class, n_games),
    ).fetchall()
    return [r["id"] for r in rows]


def session_game_ids(conn: sqlite3.Connection, session_id: int) -> list[int]:
    rows = conn.execute(
        "SELECT game_id FROM review_session_games WHERE session_id = ?", (session_id,)
    ).fetchall()
    return [r["game_id"] for r in rows]


def list_unfinished_sessions(conn: sqlite3.Connection) -> list[dict]:
    sessions = conn.execute(
        "SELECT * FROM review_sessions WHERE completed_at IS NULL ORDER BY created_at DESC"
    ).fetchall()
    out = []
    for s in sessions:
        queue = get_queue(conn, s["id"])
        labeled = sum(1 for q in queue if q["done"])
        if labeled == len(queue) and queue:
            continue  # fully done, not "unfinished" even if completed_at wasn't set yet
        out.append(
            {
                "session_id": s["id"],
                "chesscom_username": s["chesscom_username"],
                "created_at": s["created_at"],
                "labeled": labeled,
                "total": len(queue),
            }
        )
    return out


def mark_session_complete(conn: sqlite3.Connection, session_id: int) -> None:
    conn.execute(
        "UPDATE review_sessions SET completed_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), session_id),
    )
    conn.commit()


# --- Queue -------------------------------------------------------------------

def get_queue(
    conn: sqlite3.Connection, session_id: int, include_labeled: bool = False
) -> list[dict]:
    game_ids = session_game_ids(conn, session_id)
    if not game_ids:
        return []
    session = conn.execute(
        "SELECT * FROM review_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    severities = session["severities"].split(",")
    max_positions = session["max_positions"]

    placeholders = ",".join("?" for _ in game_ids)
    sev_placeholders = ",".join("?" for _ in severities)
    rows = conn.execute(
        f"""
        SELECT p.game_id, p.ply, g.played_at,
               (SELECT status FROM hand_labels h WHERE h.game_id = p.game_id AND h.ply = p.ply) as status
        FROM positions p
        JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND p.severity IN ({sev_placeholders})
          AND p.game_id IN ({placeholders})
        ORDER BY g.played_at DESC, p.ply ASC
        """,
        [*severities, *game_ids],
    ).fetchall()

    queue = []
    for r in rows:
        if r["status"] is not None and not include_labeled:
            continue
        queue.append({"game_id": r["game_id"], "ply": r["ply"], "done": r["status"] is not None})

    if max_positions:
        # Keep all already-done ones (so progress numbers stay right) plus
        # pending ones up to the cap.
        done = [q for q in queue if q["done"]]
        pending = [q for q in queue if not q["done"]]
        queue = done + pending[: max(0, max_positions - len(done))]

    return queue


# --- Position detail -----------------------------------------------------

def _move_label(ply: int, san: str) -> str:
    move_number = (ply + 1) // 2
    return f"{move_number}. {san}" if ply % 2 == 1 else f"{move_number}...{san}"


def get_position_detail(conn: sqlite3.Connection, game_id: int, ply: int) -> dict | None:
    pos = conn.execute(
        "SELECT * FROM positions WHERE game_id = ? AND ply = ?", (game_id, ply)
    ).fetchone()
    if pos is None:
        return None
    game = conn.execute("SELECT * FROM games WHERE id = ?", (game_id,)).fetchone()

    board = chess.Board(pos["fen_before"])
    move = board.parse_san(pos["san"])
    move_from, move_to = chess.square_name(move.from_square), chess.square_name(move.to_square)
    board_after = board.copy()
    board_after.push(move)
    fen_after = board_after.fen()

    prev = conn.execute(
        "SELECT san, fen_before FROM positions WHERE game_id = ? AND ply = ?", (game_id, ply - 1)
    ).fetchone()
    prev_from = prev_to = None
    if prev is not None:
        prev_board = chess.Board(prev["fen_before"])
        prev_move = prev_board.parse_san(prev["san"])
        prev_from, prev_to = chess.square_name(prev_move.from_square), chess.square_name(prev_move.to_square)

    label = conn.execute(
        "SELECT * FROM hand_labels WHERE game_id = ? AND ply = ?", (game_id, ply)
    ).fetchone()
    label_tags = []
    if label is not None:
        label_tags = [
            r["tag"]
            for r in conn.execute(
                "SELECT tag FROM hand_label_tags WHERE game_id = ? AND ply = ?", (game_id, ply)
            ).fetchall()
        ]

    return {
        "game_id": game_id,
        "ply": ply,
        "fen_before": pos["fen_before"],
        "fen_after": fen_after,
        "move_from": move_from,
        "move_to": move_to,
        "prev_from": prev_from,
        "prev_to": prev_to,
        "move_label": _move_label(ply, pos["san"]),
        "san": pos["san"],
        "severity": pos["severity"],
        "phase": pos["phase"],
        "win_pct_before": pos["win_pct_before"],
        "win_pct_after": pos["win_pct_after"],
        "win_pct_drop": pos["win_pct_drop"],
        "time_spent_s": pos["time_spent_s"],
        "clock_seconds": pos["clock_seconds"],
        "my_colour": game["my_colour"],
        "chesscom_uuid": game["chesscom_uuid"],
        "played_at": game["played_at"],
        "result": game["result"],
        "existing_label": {
            "text": label["label"] if label else None,
            "confidence": label["confidence"] if label else None,
            "status": label["status"] if label else None,
            "tags": label_tags,
        },
    }


# --- Labels ----------------------------------------------------------------

def upsert_label(
    conn: sqlite3.Connection,
    game_id: int,
    ply: int,
    text: str | None,
    tags: list[str],
    confidence: str | None,
    status: str,
    session_id: int | None,
    seconds_spent: float | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT created_at FROM hand_labels WHERE game_id = ? AND ply = ?", (game_id, ply)
    ).fetchone()
    created_at = existing["created_at"] if existing else now

    conn.execute(
        """
        INSERT INTO hand_labels
            (game_id, ply, label, confidence, status, source, session_id, seconds_spent, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 'web', ?, ?, ?, ?)
        ON CONFLICT(game_id, ply) DO UPDATE SET
            label = excluded.label, confidence = excluded.confidence, status = excluded.status,
            source = 'web', session_id = excluded.session_id, seconds_spent = excluded.seconds_spent,
            updated_at = excluded.updated_at
        """,
        (game_id, ply, text, confidence, status, session_id, seconds_spent, created_at, now),
    )
    conn.execute("DELETE FROM hand_label_tags WHERE game_id = ? AND ply = ?", (game_id, ply))
    for tag in tags:
        conn.execute(
            "INSERT INTO hand_label_tags (game_id, ply, tag) VALUES (?, ?, ?)", (game_id, ply, tag)
        )
    conn.commit()


# --- Summary -----------------------------------------------------------------

def get_summary(conn: sqlite3.Connection, session_id: int) -> dict:
    game_ids = session_game_ids(conn, session_id)
    queue = get_queue(conn, session_id, include_labeled=True)
    total = len(queue)

    labels = conn.execute(
        """
        SELECT h.* FROM hand_labels h
        WHERE h.session_id = ?
        """,
        (session_id,),
    ).fetchall()
    labeled = sum(1 for l in labels if l["status"] == "labeled")
    skipped = sum(1 for l in labels if l["status"] == "skipped")
    unsure = sum(1 for l in labels if l["confidence"] == "unsure")

    your_tag_counts: dict[str, int] = {}
    tagger_tag_counts: dict[str, int] = {}
    agree = 0
    compared = 0
    disagreements = []

    for l in labels:
        if l["status"] != "labeled":
            continue
        your_tags = {
            r["tag"]
            for r in conn.execute(
                "SELECT tag FROM hand_label_tags WHERE game_id = ? AND ply = ?", (l["game_id"], l["ply"])
            ).fetchall()
        }
        for t in your_tags:
            your_tag_counts[t] = your_tag_counts.get(t, 0) + 1

        tagger_tags = {
            r["tag"]
            for r in conn.execute(
                """SELECT tag FROM error_tags WHERE game_id = ? AND ply = ? AND tag != 'untagged'""",
                (l["game_id"], l["ply"]),
            ).fetchall()
        }
        for t in tagger_tags:
            tagger_tag_counts[t] = tagger_tag_counts.get(t, 0) + 1

        if your_tags or tagger_tags:
            compared += 1
            if your_tags & tagger_tags:
                agree += 1
            else:
                disagreements.append(
                    {
                        "game_id": l["game_id"],
                        "ply": l["ply"],
                        "your_tags": sorted(your_tags),
                        "tagger_tags": sorted(tagger_tags),
                    }
                )

    return {
        "total": total,
        "labeled": labeled,
        "skipped": skipped,
        "unsure": unsure,
        "your_tag_counts": your_tag_counts,
        "tagger_tag_counts": tagger_tag_counts,
        "agreement_pct": round(agree / compared * 100, 1) if compared else None,
        "compared": compared,
        "disagreements": disagreements,
    }
