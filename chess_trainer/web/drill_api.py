"""Web layer for the FSRS drill loop (chess_trainer/drill.py), using the
same interactive chessground board as practice.py's curriculum screens -
not a separate CLI-only tool. Each card is a single move decision (find the
move you should have played instead of your original mistake), so unlike
curriculum lessons there's no opponent auto-reply to play out - just one
submission, one grade.

Reuses drill.py's existing review_card/due_cards rather than reimplementing
scheduling or grading.
"""
from __future__ import annotations

import sqlite3

import chess
import chess.engine

from .. import drill
from ..config import Config


def queue(
    conn: sqlite3.Connection, limit: int = 15, eco: str | None = None, tag: str | None = None,
) -> list[dict]:
    cards = drill.due_cards(conn, limit=limit, eco=eco, tag=tag)
    return [
        {"game_id": c["game_id"], "ply": c["ply"], "reps": c["reps"], "my_colour": c["my_colour"]}
        for c in cards
    ]


def _get_card_row(conn: sqlite3.Connection, game_id: int, ply: int) -> sqlite3.Row | None:
    return conn.execute(
        """SELECT c.*, p.fen_before, p.san AS original_san, g.my_colour
           FROM cards c
           JOIN positions p ON p.game_id = c.game_id AND p.ply = c.ply
           JOIN games g ON g.id = c.game_id
           WHERE c.game_id = ? AND c.ply = ?""",
        (game_id, ply),
    ).fetchone()


def card_detail(conn: sqlite3.Connection, game_id: int, ply: int) -> dict | None:
    row = _get_card_row(conn, game_id, ply)
    if row is None:
        return None
    board = chess.Board(row["fen_before"])
    return {
        "game_id": game_id,
        "ply": ply,
        "fen_before": row["fen_before"],
        "my_colour": row["my_colour"],
        "original_san": row["original_san"],
        "reps": row["reps"],
        "legal_moves": [m.uci() for m in board.legal_moves],
    }


def review(
    conn: sqlite3.Connection, config: Config, game_id: int, ply: int, uci: str, seconds_spent: float,
) -> dict:
    row = _get_card_row(conn, game_id, ply)
    if row is None:
        raise ValueError(f"No such card: game {game_id} ply {ply}")

    board = chess.Board(row["fen_before"])
    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        raise ValueError(f"'{uci}' isn't a UCI move.")
    if move not in board.legal_moves:
        raise ValueError(f"'{uci}' isn't legal in this position.")
    user_san = board.san(move)

    engine = chess.engine.SimpleEngine.popen_uci(config.stockfish_path)
    engine.configure({"Threads": config.stockfish_threads, "Hash": 256})
    limit_cfg = chess.engine.Limit(nodes=config.stockfish_nodes)
    try:
        rating, best_san = drill.review_card(conn, engine, limit_cfg, row, user_san, seconds_spent)
    finally:
        engine.quit()

    return {
        "rating": rating.name,
        "user_san": user_san,
        "best_move_san": best_san,
        "best_move_uci": row["best_move_uci"],
    }
