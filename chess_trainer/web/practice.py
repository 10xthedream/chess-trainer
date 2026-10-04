"""Data/game logic for the web curriculum-practice screen (the interactive
chessground version of `curriculum.practice_lesson`'s CLI loop).

Stateless on the server by design: the client holds the current FEN and its
own move-SAN history, and every request re-validates fully from python-chess
plus the live tablebase - exactly like the CLI does. This is a UI layer over
curriculum.py's existing, working game logic (final_result_for_user,
RESULT_INVERSE), not a reimplementation of it - see chess-trainer-mvp-plan.md's
"Next planned: web UI for curriculum-practice" note for why.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import chess

from .. import tablebase
from ..curriculum import final_result_for_user


def get_lesson(conn: sqlite3.Connection, lesson_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()


def list_lessons(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM lessons ORDER BY unit, seq").fetchall()
    return [dict(r) for r in rows]


def _legal_moves_uci(board: chess.Board) -> list[str]:
    return [m.uci() for m in board.legal_moves]


def lesson_detail(conn: sqlite3.Connection, lesson_id: str) -> dict | None:
    lesson = get_lesson(conn, lesson_id)
    if lesson is None:
        return None
    out = dict(lesson)
    if lesson["exercise_type"] == "tablebase_dtz":
        board = chess.Board(lesson["start_fen"])
        out["user_is_white"] = board.turn == chess.WHITE
        out["legal_moves"] = _legal_moves_uci(board)
    else:
        out["user_is_white"] = None
        out["legal_moves"] = []
    return out


def _log_attempt(conn: sqlite3.Connection, lesson_id: str, passed: bool, moves: list[str]) -> None:
    conn.execute(
        "INSERT INTO lesson_progress (lesson_id, attempted_at, passed, moves_played) VALUES (?, ?, ?, ?)",
        (lesson_id, datetime.now(timezone.utc).isoformat(), 1 if passed else 0, " ".join(moves)),
    )
    conn.commit()


def _drift(board: chess.Board, target_result: str) -> tuple[bool, str]:
    """Mirrors the CLI's live '[!] position has drifted' feedback: does the
    theoretical result for the user still match the lesson's target? Only
    called when it's not game_over, i.e. right after the opponent's auto-reply
    - so `board`'s side to move is always the user here, and
    result_for_side_to_move already IS the user's own result (no inversion -
    see curriculum.py's practice_lesson for the same bug, fixed alongside this)."""
    try:
        user_result = tablebase.result_for_side_to_move(board.fen())
    except tablebase.TablebaseUnavailable:
        return False, target_result
    if user_result is None:
        return False, target_result
    return user_result != target_result, user_result


def apply_move(
    conn: sqlite3.Connection, lesson_id: str, fen: str, uci: str, moves_so_far: list[str]
) -> dict:
    """Validates and applies the user's move (given as UCI, e.g. 'e7e8q'),
    then - if the lesson isn't over - plays the tablebase's reply for the
    opponent immediately, same as the CLI's single-threaded turn loop.
    Raises ValueError for anything invalid (unknown lesson, wrong exercise
    type, not the user's turn, illegal move)."""
    lesson = get_lesson(conn, lesson_id)
    if lesson is None:
        raise ValueError(f"No such lesson: {lesson_id}")
    if lesson["exercise_type"] != "tablebase_dtz":
        raise ValueError("This lesson isn't a graded play-it-out exercise.")

    user_is_white = chess.Board(lesson["start_fen"]).turn == chess.WHITE
    board = chess.Board(fen)
    expected_turn = chess.WHITE if user_is_white else chess.BLACK
    if board.turn != expected_turn:
        raise ValueError("It isn't your move in this position.")

    try:
        move = chess.Move.from_uci(uci)
    except ValueError:
        raise ValueError(f"'{uci}' isn't a UCI move.")
    if move not in board.legal_moves:
        raise ValueError(f"'{uci}' isn't legal in this position.")

    user_san = board.san(move)
    board.push(move)
    moves = [*moves_so_far, user_san]

    opponent_san = opponent_uci = None
    if not board.is_game_over():
        opp_uci = tablebase.best_move_uci(board.fen())
        if opp_uci is not None:
            opp_move = chess.Move.from_uci(opp_uci)
            opponent_san = board.san(opp_move)
            opponent_uci = opp_uci
            board.push(opp_move)
            moves.append(opponent_san)

    game_over = board.is_game_over()
    passed = final_result = None
    if game_over:
        final_result = final_result_for_user(board, user_is_white, resigned=False)
        passed = final_result == lesson["target_result"]
        _log_attempt(conn, lesson_id, passed, moves)

    drifted, current_target = (False, lesson["target_result"])
    if not game_over:
        drifted, current_target = _drift(board, lesson["target_result"])

    return {
        "fen": board.fen(),
        "user_san": user_san,
        "user_uci": uci,
        "opponent_san": opponent_san,
        "opponent_uci": opponent_uci,
        "game_over": game_over,
        "passed": passed,
        "final_result": final_result,
        "target_result": lesson["target_result"],
        "drifted": drifted,
        "current_target": current_target,
        "legal_moves": [] if game_over else _legal_moves_uci(board),
        "moves": moves,
    }


def resign(conn: sqlite3.Connection, lesson_id: str, moves_so_far: list[str]) -> dict:
    lesson = get_lesson(conn, lesson_id)
    if lesson is None:
        raise ValueError(f"No such lesson: {lesson_id}")
    _log_attempt(conn, lesson_id, False, moves_so_far)
    return {"final_result": "loss", "target_result": lesson["target_result"], "passed": False}
