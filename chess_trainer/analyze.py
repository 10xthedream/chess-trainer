"""Stockfish analysis pass: evaluate every position once, classify severity and
phase for the player's own moves, and extract per-move time spent from the PGN
clock comments.

Design notes (see chess-trainer-mvp-plan.md for the "why"):
- One evaluation per position (not two-pass depth as in the original plan) at a
  fixed node budget. Simpler, deterministic, and plenty for 700-level mistakes
  (hung pieces and missed one-movers, not deep positional subtlety).
- Severity thresholds are win%-drop based, corrected to Lichess's real values:
  >=5% inaccuracy, >=10% mistake, >=15% blunder (the original draft plan had
  these at roughly double the real values).
- No "why" tagging here (hung_piece, missed_fork, etc.) - that's the next
  phase, after 20 games get hand-labeled for ground truth. This step only
  answers "how often, how bad, which phase, how fast".
"""
from __future__ import annotations

import io
import math
import re
import sqlite3
from datetime import datetime, timezone

import chess
import chess.engine
import chess.pgn

from .config import Config

# Corrected thresholds (win% drop), see plan note #2.
INACCURACY_THRESHOLD = 5.0
MISTAKE_THRESHOLD = 10.0
BLUNDER_THRESHOLD = 15.0

OPENING_PLY_LIMIT = 24
# Non-pawn material (both sides combined) at/below which we call it an endgame.
# Q=9 R=5 B=3 N=3 per side; 13/side combined == 26.
ENDGAME_MATERIAL_THRESHOLD = 26

_PIECE_VALUES = {
    chess.QUEEN: 9,
    chess.ROOK: 5,
    chess.BISHOP: 3,
    chess.KNIGHT: 3,
}

_CLK_RE = re.compile(r"\[%clk (\d+):(\d+):(\d+(?:\.\d+)?)\]")


def win_pct(cp: int) -> float:
    return 50 + 50 * (2 / (1 + math.exp(-0.00368208 * cp)) - 1)


def _clock_seconds(comment: str) -> float | None:
    m = _CLK_RE.search(comment or "")
    if not m:
        return None
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def _base_time_seconds(headers: chess.pgn.Headers) -> float | None:
    """Parse the PGN TimeControl header, e.g. '600' or '600+5'. Returns None
    for correspondence/daily controls (format 'days/seconds') where this
    tracking doesn't apply."""
    tc = headers.get("TimeControl", "")
    if not tc or "/" in tc:
        return None
    base = tc.split("+")[0]
    try:
        return float(base)
    except ValueError:
        return None


def _non_pawn_material(board: chess.Board) -> int:
    total = 0
    for piece_type, value in _PIECE_VALUES.items():
        total += value * len(board.pieces(piece_type, chess.WHITE))
        total += value * len(board.pieces(piece_type, chess.BLACK))
    return total


def _phase(ply: int, board: chess.Board) -> str:
    if ply <= OPENING_PLY_LIMIT:
        return "opening"
    if _non_pawn_material(board) <= ENDGAME_MATERIAL_THRESHOLD:
        return "endgame"
    return "middlegame"


def analyze_pending_games(
    conn: sqlite3.Connection,
    config: Config,
    limit: int | None = None,
    game_ids: list[int] | None = None,
    on_progress=None,
) -> int:
    """Analyzes games with analysed_at IS NULL, filtered to config.time_class.
    If game_ids is given, scopes to just those games (used by the web app's
    per-session jobs instead of "every pending game"). on_progress(i, total,
    game_row), if given, is called after each game - the CLI passes a print,
    the web job updates its progress record. Returns the number analyzed."""
    query = "SELECT * FROM games WHERE analysed_at IS NULL AND time_class = ?"
    params: list = [config.time_class]
    if game_ids:
        placeholders = ",".join("?" for _ in game_ids)
        query += f" AND id IN ({placeholders})"
        params.extend(game_ids)
    query += " ORDER BY played_at"
    rows = conn.execute(query, params).fetchall()
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        return 0

    engine = chess.engine.SimpleEngine.popen_uci(config.stockfish_path)
    engine.configure({"Threads": config.stockfish_threads, "Hash": 256})
    limit_cfg = chess.engine.Limit(nodes=config.stockfish_nodes)

    try:
        for i, row in enumerate(rows, 1):
            _analyze_one_game(conn, engine, limit_cfg, row)
            conn.execute(
                "UPDATE games SET analysed_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), row["id"]),
            )
            conn.commit()
            if on_progress:
                on_progress(i, len(rows), row)
    finally:
        engine.quit()

    return len(rows)


def _analyze_one_game(
    conn: sqlite3.Connection,
    engine: chess.engine.SimpleEngine,
    limit_cfg: chess.engine.Limit,
    game_row: sqlite3.Row,
) -> None:
    game = chess.pgn.read_game(io.StringIO(game_row["pgn"]))
    if game is None:
        return

    my_colour_white = game_row["my_colour"] == "white"
    base_time = _base_time_seconds(game.headers)

    board = game.board()

    def eval_white_cp(b: chess.Board) -> int:
        info = engine.analyse(b, limit_cfg)
        score = info["score"].white()
        return score.score(mate_score=100000)

    evals = [eval_white_cp(board)]
    clocks = [base_time, base_time]  # [white_prev_clock, black_prev_clock]

    ply = 0
    rows_to_insert = []

    for node in game.mainline():
        move = node.move
        ply += 1
        mover_is_white = board.turn == chess.WHITE
        san = board.san(move)
        fen_before = board.fen()

        board.push(move)
        evals.append(eval_white_cp(board))

        clk = _clock_seconds(node.comment)

        time_spent = None
        if clk is not None:
            prev_idx = 0 if mover_is_white else 1
            if clocks[prev_idx] is not None:
                time_spent = clocks[prev_idx] - clk
            clocks[prev_idx] = clk

        win_before_white = win_pct(evals[ply - 1])
        win_after_white = win_pct(evals[ply])
        win_before_mover = win_before_white if mover_is_white else 100 - win_before_white
        win_after_mover = win_after_white if mover_is_white else 100 - win_after_white
        drop = win_before_mover - win_after_mover

        is_my_move = mover_is_white == my_colour_white

        severity = None
        if is_my_move:
            if drop >= BLUNDER_THRESHOLD:
                severity = "blunder"
            elif drop >= MISTAKE_THRESHOLD:
                severity = "mistake"
            elif drop >= INACCURACY_THRESHOLD:
                severity = "inaccuracy"
            else:
                severity = "ok"

        rows_to_insert.append(
            (
                game_row["id"],
                ply,
                1 if is_my_move else 0,
                san,
                fen_before,
                evals[ply - 1],
                evals[ply],
                win_before_mover,
                win_after_mover,
                drop if is_my_move else None,
                severity,
                _phase(ply, board),
                clk,
                time_spent,
            )
        )

    conn.executemany(
        """
        INSERT OR IGNORE INTO positions
            (game_id, ply, is_my_move, san, fen_before, eval_cp_before, eval_cp_after,
             win_pct_before, win_pct_after, win_pct_drop, severity, phase,
             clock_seconds, time_spent_s)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_to_insert,
    )
