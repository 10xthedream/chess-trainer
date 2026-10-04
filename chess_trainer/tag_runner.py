"""Runs the 7 root-cause tags (see tags.py) across flagged positions in the DB.
Resumable - positions already tagged (including those marked 'untagged', a
sentinel meaning "processed, no tag fired") are skipped, so it's safe to stop
and re-run.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import chess
import chess.engine

from .config import Config
from .tags import analyze_for_tags, tag_position


def tag_flagged(
    conn: sqlite3.Connection,
    config: Config,
    severities: tuple[str, ...] = ("blunder",),
    game_ids: list[int] | None = None,
    nodes: int = 500_000,
    on_progress=None,
) -> int:
    """Tags positions with severity in `severities` (e.g. ('blunder', 'mistake')
    for the review app, vs. just ('blunder',) for the CLI/weekly report).
    Scoped to game_ids when given. on_progress(i, total), if given, is called
    after each position."""
    severity_placeholders = ",".join("?" for _ in severities)
    query = f"""
        SELECT p.game_id, p.ply, p.san, p.fen_before
        FROM positions p
        JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND p.severity IN ({severity_placeholders}) AND g.time_class = ?
          AND NOT EXISTS (
              SELECT 1 FROM error_tags e WHERE e.game_id = p.game_id AND e.ply = p.ply
          )
    """
    params: list = [*severities, config.time_class]
    if game_ids:
        query += f" AND p.game_id IN ({','.join('?' for _ in game_ids)})"
        params.extend(game_ids)

    rows = conn.execute(query, params).fetchall()
    if not rows:
        return 0

    engine = chess.engine.SimpleEngine.popen_uci(config.stockfish_path)
    engine.configure({"Threads": config.stockfish_threads, "Hash": 256})

    try:
        for i, row in enumerate(rows, 1):
            board = chess.Board(row["fen_before"])
            try:
                move = board.parse_san(row["san"])
            except ValueError:
                continue
            analysis = analyze_for_tags(engine, board, move, nodes=nodes)
            tags = tag_position(analysis)
            now = datetime.now(timezone.utc).isoformat()
            for tag in tags:
                conn.execute(
                    """INSERT INTO error_tags (game_id, ply, tag, created_at)
                       VALUES (?, ?, ?, ?)
                       ON CONFLICT(game_id, ply, tag) DO NOTHING""",
                    (row["game_id"], row["ply"], tag, now),
                )
            if not tags:
                # Mark as processed with no tag found, so it isn't re-queried forever.
                conn.execute(
                    """INSERT INTO error_tags (game_id, ply, tag, created_at)
                       VALUES (?, ?, 'untagged', ?)
                       ON CONFLICT(game_id, ply, tag) DO NOTHING""",
                    (row["game_id"], row["ply"], now),
                )
            if i % 25 == 0:
                conn.commit()
                print(f"  tagged {i}/{len(rows)}")
            if on_progress:
                on_progress(i, len(rows))
    finally:
        conn.commit()
        engine.quit()

    return len(rows)


def tag_all_blunders(conn: sqlite3.Connection, config: Config, nodes: int = 500_000) -> int:
    """Back-compat wrapper for the CLI/weekly report: blunders only, all games."""
    return tag_flagged(conn, config, severities=("blunder",), nodes=nodes)
