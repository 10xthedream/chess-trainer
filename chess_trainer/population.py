"""Population-level analysis: instead of just your own games, pulls your
most recent opponents' own game histories too. Chess.com's matchmaking
already pairs you with similarly-rated players, so this approximates "what
do ~900-level players typically do" without needing a rating-band search
API, which chess.com's public API doesn't offer (only per-username
archives - see chesscom.py). Lichess's open bulk database would be the
alternative if this ever needs to scale past "your actual opponent pool."

Reuses the existing single-user pipeline verbatim per sampled username
(chesscom.sync_games / analyze_pending_games / tag_runner.tag_flagged),
looped with dataclasses.replace(config, chesscom_username=...) for each one
- the games table already supports multiple usernames side by side (added
for the review app). No new schema, no new analysis logic - this file is
just orchestration + aggregation/reporting.
"""
from __future__ import annotations

import dataclasses
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from . import chesscom, tag_runner
from .analyze import analyze_pending_games
from .config import Config

_WHITE_RE = re.compile(r'\[White "(.+?)"\]')
_BLACK_RE = re.compile(r'\[Black "(.+?)"\]')


def discover_opponents(conn: sqlite3.Connection, primary_username: str, limit: int = 50) -> list[str]:
    """Unique opponent usernames from the primary user's N most recent games,
    lowercased - chesscom.py always stores/compares usernames lowercase
    (chess.com's own API is case-insensitive), so every downstream query in
    this module needs the same casing or it silently matches nothing."""
    primary_lower = primary_username.lower()
    rows = conn.execute(
        "SELECT pgn FROM games WHERE chesscom_username = ? ORDER BY played_at DESC LIMIT ?",
        (primary_lower, limit),
    ).fetchall()
    opponents: set[str] = set()
    for r in rows:
        w = _WHITE_RE.search(r["pgn"])
        b = _BLACK_RE.search(r["pgn"])
        for m in (w, b):
            if m and m.group(1).lower() != primary_lower:
                opponents.add(m.group(1).lower())
    return sorted(opponents)


def build_sample(
    conn: sqlite3.Connection,
    config: Config,
    opponents: list[str],
    games_per_opponent: int = 15,
    analysis_nodes: int = 200_000,
    tag_nodes: int = 150_000,
    on_progress=None,
) -> dict:
    """Syncs + analyzes + tags up to `games_per_opponent` recent games for
    each opponent username, at a reduced node budget - this is a population
    sample, not your own careful review, so full 1M-node precision isn't
    needed. Resumable/idempotent like the rest of the pipeline: re-running
    only picks up what's missing."""
    light_config = dataclasses.replace(config, stockfish_nodes=analysis_nodes)
    stats = {"opponents_done": 0, "games_synced": 0, "games_analyzed": 0, "errors": []}

    for i, username in enumerate(opponents, 1):
        user_config = dataclasses.replace(light_config, chesscom_username=username)
        try:
            fetch_stats = chesscom.sync_games(conn, user_config, max_games=games_per_opponent)
            stats["games_synced"] += fetch_stats.games_inserted
            n_analyzed = analyze_pending_games(conn, user_config)
            stats["games_analyzed"] += n_analyzed
            # Scoped to just this opponent's own games - without game_ids,
            # tag_flagged rescans the *entire* untagged backlog in the DB on
            # every call (harmless but wasteful across dozens of opponents;
            # caught during testing when it kept re-tagging the primary
            # user's historical mistake backlog on every single iteration).
            game_ids = [
                r["id"] for r in conn.execute(
                    "SELECT id FROM games WHERE chesscom_username = ?", (username,)
                ).fetchall()
            ]
            tag_runner.tag_flagged(
                conn, user_config, severities=("blunder", "mistake"), nodes=tag_nodes, game_ids=game_ids,
            )
        except Exception as exc:  # noqa: BLE001 - one bad username shouldn't kill a 45-min run
            stats["errors"].append(f"{username}: {exc}")
        stats["opponents_done"] += 1
        if on_progress:
            on_progress(i, len(opponents), username)

    return stats


def write_population_report_markdown(
    conn: sqlite3.Connection, config: Config, opponents: list[str], time_class: str,
) -> Path:
    all_usernames = [config.chesscom_username, *opponents]
    placeholders = ",".join("?" for _ in all_usernames)

    game_count = conn.execute(
        f"SELECT COUNT(*) FROM games WHERE chesscom_username IN ({placeholders}) AND time_class = ?",
        [*all_usernames, time_class],
    ).fetchone()[0]

    eco_rows = conn.execute(
        f"""
        SELECT eco, COUNT(*) n FROM games
        WHERE chesscom_username IN ({placeholders}) AND time_class = ? AND eco IS NOT NULL
        GROUP BY eco ORDER BY n DESC LIMIT 20
        """,
        [*all_usernames, time_class],
    ).fetchall()

    tag_rows = conn.execute(
        f"""
        SELECT e.tag, COUNT(*) n FROM error_tags e
        JOIN games g ON g.id = e.game_id
        WHERE g.chesscom_username IN ({placeholders}) AND g.time_class = ? AND e.tag != 'untagged'
        GROUP BY e.tag ORDER BY n DESC
        """,
        [*all_usernames, time_class],
    ).fetchall()

    phase_rows = conn.execute(
        f"""
        SELECT p.phase,
               COUNT(*) as moves,
               SUM(CASE WHEN p.severity = 'blunder' THEN 1 ELSE 0 END) as blunders
        FROM positions p JOIN games g ON g.id = p.game_id
        WHERE g.chesscom_username IN ({placeholders}) AND g.time_class = ? AND p.is_my_move = 1
        GROUP BY p.phase
        """,
        [*all_usernames, time_class],
    ).fetchall()

    total_my_moves = sum(r["moves"] for r in phase_rows)
    total_blunders = sum(r["blunders"] for r in phase_rows)

    lines = []
    lines.append(f"# Population Report — {time_class}, your opponent pool")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append(
        f"**{game_count} games across you + {len(opponents)} opponents from your last "
        f"50 games.** Not a random ~900-rated sample - this is specifically the pool "
        f"chess.com's matchmaking actually puts you against, which is arguably more "
        f"useful: it's what you'll really face next."
    )
    lines.append("")
    lines.append("## Common openings in this pool")
    lines.append("")
    lines.append("| ECO | Games | Share |")
    lines.append("|---|---|---|")
    for r in eco_rows:
        pct = round(r["n"] / game_count * 100, 1) if game_count else 0
        lines.append(f"| {r['eco']} | {r['n']} | {pct}% |")
    lines.append("")
    lines.append("## Most common mistake types (pooled, blunders + mistakes)")
    lines.append("")
    lines.append("| Tag | Count |")
    lines.append("|---|---|")
    for r in tag_rows:
        lines.append(f"| {r['tag']} | {r['n']} |")
    lines.append("")
    lines.append("## Blunder rate by phase (pooled)")
    lines.append("")
    lines.append("| Phase | Moves | Blunders | Per 100 moves |")
    lines.append("|---|---|---|---|")
    for r in phase_rows:
        rate = round(r["blunders"] / r["moves"] * 100, 2) if r["moves"] else 0
        lines.append(f"| {r['phase']} | {r['moves']} | {r['blunders']} | {rate} |")
    lines.append("")
    lines.append(
        f"**Pool-wide: {total_blunders} blunders / {total_my_moves} moves "
        f"({round(total_blunders / total_my_moves * 100, 2) if total_my_moves else 0} per 100).** "
        f"Compare this to your own weekly report's blunder rate to see whether you're "
        f"actually ahead of or behind the pool you're playing against."
    )
    lines.append("")

    vault = Path(config.obsidian_vault_path)
    content = "\n".join(lines)
    out_path = vault / "chess-trainer-population-report.md"
    out_path.write_text(content, encoding="utf-8")
    return out_path
