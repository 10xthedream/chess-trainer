"""Opponent-pattern report for one opening (by ECO code): what do opponents
actually play against you, how have you scored against each reply, and
which of your own losses in that opening trace back to specific flagged
blunders/mistakes (with their existing root-cause tags).

Deliberately NOT a generic opening-repertoire trainer (see chess-trainer-mvp-plan.md:
"opening repertoire trainer - use Chessdriller/Listudy if needed later" was an
explicit non-goal, reaffirmed twice). This is the opposite of that: it answers
"what will MY actual opponents throw at me" from this project's own game data,
not "here is opening theory to memorize" - the same own-data ethos as everything
else in this app.
"""
from __future__ import annotations

import io
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import chess
import chess.pgn

from .config import Config

# How many of the opponent's own plies to use as the "line signature" for
# grouping - 3 of their moves is usually enough to distinguish the real
# branches (e.g. 3...exd4 vs 3...Nf6 vs 3...Bc5) without over-fragmenting
# into near-duplicate lines that just transposed differently.
OPPONENT_PLY_SIGNATURE = 3


def _opening_moves(pgn: str, max_plies: int = 16) -> list[str]:
    game = chess.pgn.read_game(io.StringIO(pgn))
    if game is None:
        return []
    board = game.board()
    sans = []
    for ply, node in enumerate(game.mainline(), start=1):
        if ply > max_plies:
            break
        move = node.move
        sans.append(board.san(move))
        board.push(move)
    return sans


def _opponent_signature(sans: list[str], my_colour: str) -> str:
    """The opponent's first OPPONENT_PLY_SIGNATURE moves, formatted as a
    short SAN string, e.g. 'exd4 Bc5 Qf6' - used to bucket games by what the
    opponent actually chose to play."""
    opp_indices = range(1, len(sans), 2) if my_colour == "white" else range(0, len(sans), 2)
    opp_moves = [sans[i] for i in opp_indices][:OPPONENT_PLY_SIGNATURE]
    return " ".join(opp_moves) if opp_moves else "(game ended before this)"


def opponent_lines(conn: sqlite3.Connection, eco: str, my_colour: str) -> dict[str, dict]:
    """For every game with this ECO/colour, buckets by the opponent's early
    reply and tallies win/draw/loss per bucket."""
    games = conn.execute(
        "SELECT id, pgn, result FROM games WHERE eco = ? AND my_colour = ?",
        (eco, my_colour),
    ).fetchall()

    buckets: dict[str, dict] = {}
    for g in games:
        sans = _opening_moves(g["pgn"])
        sig = _opponent_signature(sans, my_colour)
        bucket = buckets.setdefault(sig, {"win": 0, "draw": 0, "loss": 0, "game_ids": []})
        bucket[g["result"]] += 1
        bucket["game_ids"].append(g["id"])

    return buckets


def losses_with_blunders(conn: sqlite3.Connection, eco: str, my_colour: str) -> list[dict]:
    """Every flagged blunder/mistake YOU made in a LOST game of this opening,
    with its existing root-cause tag(s) - connects 'what opponent played' to
    'what specifically went wrong for you'."""
    rows = conn.execute(
        """
        SELECT g.id as game_id, g.played_at, g.chesscom_uuid, p.ply, p.san,
               p.severity, p.win_pct_drop, p.phase
        FROM games g
        JOIN positions p ON p.game_id = g.id
        WHERE g.eco = ? AND g.my_colour = ? AND g.result = 'loss'
          AND p.is_my_move = 1 AND p.severity IN ('blunder', 'mistake')
        ORDER BY g.played_at, p.ply
        """,
        (eco, my_colour),
    ).fetchall()

    out = []
    for r in rows:
        tags = [
            t["tag"] for t in conn.execute(
                "SELECT tag FROM error_tags WHERE game_id = ? AND ply = ? AND tag != 'untagged'",
                (r["game_id"], r["ply"]),
            ).fetchall()
        ]
        out.append({**dict(r), "tags": tags})
    return out


def write_opening_report_markdown(conn: sqlite3.Connection, config: Config, eco: str, my_colour: str = "white") -> Path:
    buckets = opponent_lines(conn, eco, my_colour)
    blunders = losses_with_blunders(conn, eco, my_colour)

    total = sum(b["win"] + b["draw"] + b["loss"] for b in buckets.values())
    wins = sum(b["win"] for b in buckets.values())
    losses = sum(b["loss"] for b in buckets.values())
    draws = sum(b["draw"] for b in buckets.values())

    lines = []
    lines.append(f"# Opening Report — {eco} as {my_colour}")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append(
        f"**{total} games as {my_colour} in {eco}.** {wins}W {draws}D {losses}L "
        f"({round(wins / total * 100, 1) if total else 0}% score)."
    )
    lines.append("")
    lines.append("## What opponents actually play against you")
    lines.append("")
    lines.append("Grouped by their first few moves - this is the real answer to "
                  "\"what lines will my opponents throw at me,\" built from your own "
                  "games rather than generic theory.")
    lines.append("")
    lines.append("| Opponent's reply | Games | W-D-L | Your score |")
    lines.append("|---|---|---|---|")
    for sig, b in sorted(buckets.items(), key=lambda kv: -(kv[1]["win"] + kv[1]["draw"] + kv[1]["loss"])):
        n = b["win"] + b["draw"] + b["loss"]
        score = round(b["win"] / n * 100, 0) if n else 0
        lines.append(f"| {sig} | {n} | {b['win']}-{b['draw']}-{b['loss']} | {score}% |")
    lines.append("")
    lines.append("## Your blunders in games you lost")
    lines.append("")
    if not blunders:
        lines.append("None flagged yet, or no losses in this opening - nothing to show.")
    else:
        lines.append("| Game | Move | Severity | Phase | Tags | |")
        lines.append("|---|---|---|---|---|---|")
        for r in blunders:
            tags = ", ".join(r["tags"]) if r["tags"] else "-"
            move_no = (r["ply"] + 1) // 2
            move_label = f"{move_no}.{'..' if r['ply'] % 2 == 0 else ''} {r['san']}"
            link = f"[chess.com ↗](https://www.chess.com/game/live/{r['chesscom_uuid']})"
            lines.append(
                f"| {r['played_at'][:10]} | {move_label} | {r['severity']} | "
                f"{r['phase']} | {tags} | {link} |"
            )
    lines.append("")
    lines.append(
        f"Run `python -m chess_trainer.cli drill-build {eco}` to turn these into "
        f"FSRS drill cards scoped to just this opening."
    )
    lines.append("")

    vault = Path(config.obsidian_vault_path)
    content = "\n".join(lines)
    out_path = vault / f"chess-trainer-opening-report-{eco}.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path
