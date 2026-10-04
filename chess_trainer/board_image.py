"""Renders a FEN to a PNG board image for visual walkthroughs.

Uses the free web-boardimage service (same author as python-chess; no API key,
no local rendering deps like cairosvg needed). Not part of the weekly report
pipeline - this is a conversational/debugging aid for looking at specific
positions (e.g. "show me this blunder").
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

API_URL = "https://backscattering.de/web-boardimage/board.png"


def save_board_png(
    fen: str,
    out_path: str | Path,
    size: int = 480,
    last_move_uci: str | None = None,
    arrows: list[str] | None = None,
) -> Path:
    """arrows: list of strings like 'Ge2e4' (Green), 'Re2e4' (Red), 'Ye2e4' (Yellow),
    'Bc3c5' (Blue) - color letter + from-square + to-square, no separators.

    This is a small free service with no API key - retries with backoff on 429
    rather than hammering it, and callers generating many images in a loop
    should add their own small delay between calls (see hand_label.py)."""
    params = {"fen": fen, "size": str(size)}
    if last_move_uci:
        params["lastMove"] = last_move_uci
    if arrows:
        params["arrows"] = ",".join(arrows)

    delay = 2.0
    resp = None
    for attempt in range(6):
        resp = httpx.get(API_URL, params=params, timeout=20.0)
        if resp.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        break
    resp.raise_for_status()

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(resp.content)
    return out_path
