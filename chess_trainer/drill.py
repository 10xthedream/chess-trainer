"""Own-blunder drill loop: FSRS-scheduled spaced repetition over positions
where you blundered or made a mistake in a real game.

A card asks "what should you have played here instead" - graded by comparing
your move's resulting win% to the engine's best move, not a binary
right/wrong, since "found an equally good alternative", "better than the
original mistake but not best", and "made the same category of mistake
again" are genuinely different outcomes and should be scheduled differently
(Good / Hard / Again).

Uses the reference `fsrs` package (open-spaced-repetition/py-fsrs) rather
than hand-rolling the algorithm - see chess-trainer-mvp-plan.md, which named
FSRS specifically as the next phase after the weekly report/curriculum ran
long enough to show the current approach helps.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import chess
import chess.engine
import fsrs

from .analyze import win_pct
from .config import Config

# Same win%-drop vocabulary as severity classification in analyze.py, reused
# here to grade a drill attempt instead of a real game move.
GOOD_DROP_THRESHOLD = 5.0    # drop vs the engine's best -> found an equivalent move
HARD_DROP_THRESHOLD = 15.0   # below blunder-tier -> better than the original mistake, not best
# >= HARD_DROP_THRESHOLD (blunder-tier again) -> Again

FAST_SOLVE_SECONDS = 5.0     # solved correctly this fast on a card's first rep -> Easy

_SCHEDULER = fsrs.Scheduler()


def build_cards(conn: sqlite3.Connection, config: Config, on_progress=None, eco: str | None = None) -> int:
    """Turns newly flagged blunders/mistakes into drill cards. Computes the
    engine's best move once per card at build time so review itself stays
    fast. Resumable: positions that already have a card are skipped.

    `eco`, if given, scopes this to just games with that ECO code (e.g. 'C45'
    for the Scotch) - for building a focused deck around one opening instead
    of the whole historical backlog at once."""
    query = """
        SELECT p.game_id, p.ply, p.fen_before
        FROM positions p
        JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND p.severity IN ('blunder', 'mistake')
          AND NOT EXISTS (SELECT 1 FROM cards c WHERE c.game_id = p.game_id AND c.ply = p.ply)
    """
    params: list = []
    if eco:
        query += " AND g.eco = ?"
        params.append(eco)
    query += " ORDER BY p.game_id, p.ply"
    rows = conn.execute(query, params).fetchall()
    if not rows:
        return 0

    engine = chess.engine.SimpleEngine.popen_uci(config.stockfish_path)
    engine.configure({"Threads": config.stockfish_threads, "Hash": 256})
    limit = chess.engine.Limit(nodes=config.stockfish_nodes)

    try:
        for i, row in enumerate(rows, 1):
            board = chess.Board(row["fen_before"])
            info = engine.analyse(board, limit)
            best_move = info["pv"][0]
            best_san = board.san(best_move)
            best_cp = info["score"].pov(board.turn).score(mate_score=100000)

            card = fsrs.Card()
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """INSERT INTO cards (game_id, ply, best_move_uci, best_move_san,
                       best_cp, fsrs_state, due, reps, lapses, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)""",
                (
                    row["game_id"], row["ply"], best_move.uci(), best_san, best_cp,
                    json.dumps(card.to_dict(), default=str), card.due.isoformat(), now,
                ),
            )
            conn.commit()
            if on_progress:
                on_progress(i, len(rows))
    finally:
        engine.quit()

    return len(rows)


def due_cards(conn: sqlite3.Connection, limit: int | None = None, eco: str | None = None) -> list[sqlite3.Row]:
    """Cards due now or earlier, soonest-due first. `eco`, if given, scopes
    to cards from games with that ECO code only (e.g. 'C45')."""
    now = datetime.now(timezone.utc).isoformat()
    query = """
        SELECT c.*, p.fen_before, p.san AS original_san, g.my_colour
        FROM cards c
        JOIN positions p ON p.game_id = c.game_id AND p.ply = c.ply
        JOIN games g ON g.id = c.game_id
        WHERE c.due <= ?
    """
    params: list = [now]
    if eco:
        query += " AND g.eco = ?"
        params.append(eco)
    query += " ORDER BY c.due"
    if limit:
        query += " LIMIT ?"
        params.append(limit)
    return conn.execute(query, params).fetchall()


def _grade(user_cp_for_mover: int | None, best_cp: int | None, solved_fast: bool) -> "fsrs.Rating":
    if user_cp_for_mover is None or best_cp is None:
        return fsrs.Rating.Hard  # mate-adjacent edge case: treat as "not quite"
    drop = win_pct(best_cp) - win_pct(user_cp_for_mover)
    if drop < GOOD_DROP_THRESHOLD:
        return fsrs.Rating.Easy if solved_fast else fsrs.Rating.Good
    if drop < HARD_DROP_THRESHOLD:
        return fsrs.Rating.Hard
    return fsrs.Rating.Again


def review_card(
    conn: sqlite3.Connection,
    engine: chess.engine.SimpleEngine,
    limit_cfg: chess.engine.Limit,
    card_row: sqlite3.Row,
    user_san: str,
    seconds_spent: float,
) -> tuple["fsrs.Rating", str]:
    """Scores the user's move against the card's stored best move, advances
    the FSRS schedule, and logs the review. Returns (rating, best_move_san)
    for the caller to show feedback. Raises ValueError if user_san isn't a
    legal move in this position."""
    board = chess.Board(card_row["fen_before"])
    user_move = board.parse_san(user_san)  # raises ValueError if illegal
    board.push(user_move)

    info = engine.analyse(board, limit_cfg)
    # board.turn has flipped to the opponent after our push; `not board.turn`
    # is our own color, so this is already our move's eval from our POV.
    user_cp_for_mover = info["score"].pov(not board.turn).score(mate_score=100000)

    solved_fast = seconds_spent <= FAST_SOLVE_SECONDS and card_row["reps"] == 0
    rating = _grade(user_cp_for_mover, card_row["best_cp"], solved_fast)

    card = fsrs.Card.from_dict(json.loads(card_row["fsrs_state"]))
    new_card, _review_log = _SCHEDULER.review_card(card, rating)

    now = datetime.now(timezone.utc)
    conn.execute(
        """UPDATE cards SET fsrs_state = ?, due = ?, reps = reps + 1,
               lapses = lapses + ?, last_reviewed_at = ?
           WHERE game_id = ? AND ply = ?""",
        (
            json.dumps(new_card.to_dict(), default=str), new_card.due.isoformat(),
            1 if rating == fsrs.Rating.Again else 0, now.isoformat(),
            card_row["game_id"], card_row["ply"],
        ),
    )
    conn.execute(
        """INSERT INTO reviews (game_id, ply, reviewed_at, your_move_san, rating,
               seconds_spent, stability, difficulty)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            card_row["game_id"], card_row["ply"], now.isoformat(), user_san, rating.name,
            seconds_spent, new_card.stability, new_card.difficulty,
        ),
    )
    conn.commit()
    return rating, card_row["best_move_san"]


def drill_session(conn: sqlite3.Connection, config: Config, max_cards: int = 15, eco: str | None = None) -> None:
    """Interactive CLI drill loop over due cards, mirroring
    curriculum.practice_lesson's text-based style. `eco`, if given, scopes
    the session to one opening (e.g. 'C45' for the Scotch)."""
    import time

    cards = due_cards(conn, limit=max_cards, eco=eco)
    if not cards:
        scope = f" for {eco}" if eco else ""
        print(f"No cards due right now{scope}. Run 'drill-build{(' ' + eco) if eco else ''}' "
              f"first if you haven't, or come back later - FSRS spaces these out on purpose.")
        return

    print(f"\n{len(cards)} card(s) due. For each, find the move YOU should have "
          f"played instead of the original mistake. Enter SAN (e.g. Nf3, Qxd5), "
          f"or 'skip'/'quit'.\n")

    engine = chess.engine.SimpleEngine.popen_uci(config.stockfish_path)
    engine.configure({"Threads": config.stockfish_threads, "Hash": 256})
    limit_cfg = chess.engine.Limit(nodes=config.stockfish_nodes)

    counts = {"Again": 0, "Hard": 0, "Good": 0, "Easy": 0}
    try:
        for i, card_row in enumerate(cards, 1):
            board = chess.Board(card_row["fen_before"])
            print(f"\n--- Card {i}/{len(cards)} (game {card_row['game_id']}, "
                  f"ply {card_row['ply']}, rep #{card_row['reps'] + 1}) ---")
            print(f"You are {card_row['my_colour']}. Originally played: "
                  f"{card_row['original_san']} (a mistake/blunder).")
            print()
            print(board)  # always White-at-bottom orientation, same as curriculum.py's _print_board
            print()

            start = time.monotonic()
            try:
                raw = input("Your move: ").strip()
            except EOFError:
                raw = "quit"
            elapsed = time.monotonic() - start

            if raw.lower() == "quit":
                break
            if raw.lower() == "skip":
                continue

            try:
                board.parse_san(raw)
            except ValueError:
                print(f"Couldn't parse '{raw}' as a legal move - skipping this card.")
                continue

            rating, best_san = review_card(conn, engine, limit_cfg, card_row, raw, elapsed)
            counts[rating.name] += 1
            if rating.name in ("Good", "Easy"):
                print(f"{rating.name}! ({raw} matches or nearly matches the engine's "
                      f"top choice, {best_san})")
            else:
                print(f"{rating.name}. Best was {best_san}; your move ({raw}) wasn't "
                      f"close enough.")
    finally:
        engine.quit()

    print(f"\nSession done: {sum(counts.values())} reviewed - "
          f"{counts['Easy']} easy, {counts['Good']} good, "
          f"{counts['Hard']} hard, {counts['Again']} again.")
