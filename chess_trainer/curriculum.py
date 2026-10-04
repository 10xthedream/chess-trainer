"""Curriculum ingestion and interactive practice.

Ingestion validates every authored FEN with python-chess (legality) and, for
'tablebase_dtz' lessons, fetches the authoritative result from the live
Lichess tablebase API rather than trusting a hand-derived verdict (see
curriculum_data.py for why this matters). If the API can't classify a
position (out of range, or temporarily unavailable), the lesson is downgraded
to 'concept' rather than silently shipping an unverified pass/fail target.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import chess

from . import tablebase
from .curriculum_data import LESSONS, UNIT_NAMES

# Public: also used by chess_trainer/web/practice.py (the web curriculum-practice
# screen reuses this exact logic rather than reimplementing it).
RESULT_INVERSE = {"win": "loss", "loss": "win", "draw": "draw"}


def ingest_lessons(conn: sqlite3.Connection, verbose: bool = True) -> list[str]:
    """Validates and (re)loads all lessons into the DB. Returns a list of
    warning strings for anything that needed downgrading or couldn't be
    verified."""
    warnings = []

    for lesson in LESSONS:
        exercise_type = lesson["exercise_type"]
        target_result = None
        verified_at = None

        if lesson["start_fen"] is not None:
            board = chess.Board(lesson["start_fen"])
            if not board.is_valid():
                raise ValueError(
                    f"Lesson {lesson['id']} has an illegal FEN: {lesson['start_fen']} "
                    f"(status: {board.status()!r})"
                )

        if exercise_type == "tablebase_dtz":
            try:
                target_result = tablebase.result_for_side_to_move(lesson["start_fen"])
            except tablebase.TablebaseUnavailable as exc:
                warnings.append(f"{lesson['id']}: tablebase unavailable ({exc}), downgraded to concept")
                exercise_type = "concept"
            else:
                if target_result is None:
                    warnings.append(
                        f"{lesson['id']}: position outside tablebase range/undetermined, "
                        f"downgraded to concept"
                    )
                    exercise_type = "concept"
                else:
                    verified_at = datetime.now(timezone.utc).isoformat()

        conn.execute(
            """
            INSERT INTO lessons (id, unit, unit_name, seq, title, objective, start_fen,
                                  exercise_type, target_result, verified_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                unit = excluded.unit, unit_name = excluded.unit_name, seq = excluded.seq,
                title = excluded.title, objective = excluded.objective,
                start_fen = excluded.start_fen, exercise_type = excluded.exercise_type,
                target_result = excluded.target_result, verified_at = excluded.verified_at
            """,
            (
                lesson["id"], lesson["unit"], UNIT_NAMES[lesson["unit"]], lesson["seq"],
                lesson["title"], lesson["objective"], lesson["start_fen"],
                exercise_type, target_result, verified_at,
            ),
        )
        if verbose:
            status = target_result or exercise_type
            print(f"  {lesson['id']:12} {lesson['title']:55} -> {status}")

    # Prune lessons that no longer appear in LESSONS (e.g. a roadmap placeholder
    # replaced by real content under a new id) - upsert alone would leave the
    # old row behind forever. Drop any progress history for a pruned id first,
    # since lesson_progress.lesson_id has no ON DELETE CASCADE.
    current_ids = {lesson["id"] for lesson in LESSONS}
    stale_ids = [
        row["id"] for row in conn.execute("SELECT id FROM lessons").fetchall()
        if row["id"] not in current_ids
    ]
    for stale_id in stale_ids:
        conn.execute("DELETE FROM lesson_progress WHERE lesson_id = ?", (stale_id,))
        conn.execute("DELETE FROM lessons WHERE id = ?", (stale_id,))
        if verbose and stale_id:
            print(f"  {stale_id:12} (removed - no longer in curriculum_data.py)")

    conn.commit()
    return warnings


def list_lessons(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM lessons ORDER BY unit, seq").fetchall()


def _print_board(board: chess.Board) -> None:
    # Plain letters (str(board)), not board.unicode(): Windows consoles default
    # to a codepage (cp1252) that can't encode the Unicode chess piece glyphs
    # and crashes with UnicodeEncodeError. Letters are portable everywhere.
    print()
    print(board)
    print(f"FEN: {board.fen()}")
    print()


def final_result_for_user(board: chess.Board, user_is_white: bool, resigned: bool) -> str:
    if resigned:
        return "loss"
    if board.is_checkmate():
        # side to move is the one who got mated
        mated_is_white = board.turn == chess.WHITE
        user_got_mated = mated_is_white == user_is_white
        return "loss" if user_got_mated else "win"
    # stalemate, insufficient material, 75-move, 5-fold repetition, etc.
    return "draw"


def practice_lesson(conn: sqlite3.Connection, lesson_id: str) -> None:
    row = conn.execute("SELECT * FROM lessons WHERE id = ?", (lesson_id,)).fetchone()
    if row is None:
        print(f"No such lesson: {lesson_id}")
        return

    print(f"\n=== {row['title']} ({row['unit_name']}) ===")
    print(row["objective"])

    if row["exercise_type"] != "tablebase_dtz":
        print(f"\n(This is a '{row['exercise_type']}' lesson - no graded play-it-out yet.)")
        return

    board = chess.Board(row["start_fen"])
    user_is_white = board.turn == chess.WHITE
    target_result = row["target_result"]
    print(f"\nYou are playing {'White' if user_is_white else 'Black'}. "
          f"Theoretical result: {target_result} for you. "
          f"Enter moves in SAN (e.g. Kd5, e4), or 'resign'/'quit'.")
    _print_board(board)

    moves_played: list[str] = []
    resigned = False

    while not board.is_game_over():
        is_users_turn = (board.turn == chess.WHITE) == user_is_white

        if is_users_turn:
            try:
                raw = input("Your move: ").strip()
            except EOFError:
                resigned = True
                break
            if raw.lower() in ("resign", "quit"):
                resigned = True
                break
            try:
                move = board.parse_san(raw)
            except ValueError:
                print("Couldn't parse that as a legal move - try again (SAN, e.g. Rb1, exd5, O-O).")
                continue
            san = board.san(move)
            board.push(move)
            moves_played.append(san)
        else:
            uci = tablebase.best_move_uci(board.fen())
            if uci is None:
                break
            move = chess.Move.from_uci(uci)
            san = board.san(move)
            board.push(move)
            moves_played.append(san)
            print(f"Opponent plays: {san}")

        _print_board(board)

        # Live feedback: has the achievable result for the user changed? Only
        # invert when the side to move is the opponent (i.e. the user just
        # moved) - when it's the user's own turn (opponent just moved),
        # result_for_side_to_move already IS the user's result, and inverting
        # it anyway was a real bug: it falsely flagged "drifted to a loss" on
        # some obviously-still-winning positions (caught via the web UI's
        # identical logic, same bug, ported here).
        try:
            side_to_move_result = tablebase.result_for_side_to_move(board.fen())
        except tablebase.TablebaseUnavailable:
            side_to_move_result = None
        if side_to_move_result is not None:
            its_users_turn = (board.turn == chess.WHITE) == user_is_white
            current_user_result = (
                side_to_move_result if its_users_turn else RESULT_INVERSE[side_to_move_result]
            )
            if current_user_result != target_result:
                print(
                    f"  [!] The position has drifted from a theoretical '{target_result}' "
                    f"to a theoretical '{current_user_result}' for you."
                )

    final_result = final_result_for_user(board, user_is_white, resigned)
    passed = final_result == target_result
    print(f"\n{'PASS' if passed else 'FAIL'} - final result: {final_result}, target was: {target_result}")

    conn.execute(
        "INSERT INTO lesson_progress (lesson_id, attempted_at, passed, moves_played) VALUES (?, ?, ?, ?)",
        (lesson_id, datetime.now(timezone.utc).isoformat(), 1 if passed else 0, " ".join(moves_played)),
    )
    conn.commit()
