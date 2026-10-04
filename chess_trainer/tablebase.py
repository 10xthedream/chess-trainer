"""Thin client for the free Lichess tablebase API.

No local Syzygy download needed for the curriculum: this API serves perfect
play for up to 7-piece endgames, which covers every lesson in
curriculum_data.py. Used both to (a) get the authoritative result category for
a position, and (b) get a perfect move for the "opponent" side during a
practice session.

Rate-limit friendly by construction: curriculum practice is a human playing
one move at a time, nowhere near the volume of the chess.com bulk import.
"""
from __future__ import annotations

import httpx

API_URL = "https://tablebase.lichess.ovh/standard"

# Lichess's category values, in the order from best to worst for the side to move.
_CATEGORY_TO_RESULT = {
    "win": "win",
    "cursed-win": "draw",   # win in theory, drawn under the 50-move rule - treat as draw for grading
    "draw": "draw",
    "blessed-loss": "draw",  # loss in theory, drawn under the 50-move rule
    "loss": "loss",
    "maybe-win": "win",
    "maybe-loss": "loss",
    "unknown": None,
}


class TablebaseUnavailable(RuntimeError):
    pass


def probe(fen: str, timeout: float = 15.0) -> dict:
    """Returns the raw Lichess tablebase API response for a FEN."""
    try:
        resp = httpx.get(API_URL, params={"fen": fen}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as exc:
        raise TablebaseUnavailable(f"Tablebase API request failed: {exc}") from exc


def result_for_side_to_move(fen: str) -> str | None:
    """Returns 'win' | 'draw' | 'loss' for the side to move in this position,
    or None if the position is outside tablebase range / undetermined."""
    data = probe(fen)
    category = data.get("category")
    return _CATEGORY_TO_RESULT.get(category)


def best_move_uci(fen: str) -> str | None:
    """Returns the tablebase's best move (by DTZ, falling back to the first
    listed move) in UCI format, or None if no moves are available (e.g.
    checkmate/stalemate, or position outside tablebase range)."""
    data = probe(fen)
    moves = data.get("moves") or []
    if not moves:
        return None
    return moves[0].get("uci")
