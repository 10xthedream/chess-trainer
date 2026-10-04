"""Chess.com public API client: incremental sync of a user's games into SQLite.

No auth/API key needed. Chess.com's stated rules of the road:
- Serial access (wait for each response before firing the next) is effectively
  unlimited. Parallel requests can get you 429'd.
- Set a User-Agent that identifies your app and a contact email, so they can
  reach you instead of just blocking you if something misbehaves.
- This exact use case (a tool that helps a player review their own games) is
  the intended use.

Incremental sync: we store the last-seen ETag per archive-month URL in
`archive_sync` and skip re-fetching a month whose ETag hasn't changed. The
current month's ETag changes as new games are played, so it's always
re-checked (cheaply, since an unchanged ETag means no body is re-parsed).
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .config import Config

BASE_URL = "https://api.chess.com/pub"

# chess.com's per-side "result" values, normalized to win/loss/draw from that side's POV.
_WIN = {"win"}
_DRAW = {"agreed", "repetition", "stalemate", "insufficient", "50move", "timevsinsufficient"}
# everything else (checkmated, resigned, timeout, lose, abandoned, kingofthehill,
# threecheck, bughousepartnerlose) is a loss for that side.


def _normalize_result(raw: str) -> str:
    if raw in _WIN:
        return "win"
    if raw in _DRAW:
        return "draw"
    return "loss"


@dataclass
class FetchStats:
    archives_checked: int = 0
    archives_skipped_unchanged: int = 0
    games_seen: int = 0
    games_inserted: int = 0


def _request_with_backoff(client: httpx.Client, url: str, headers: dict) -> httpx.Response:
    delay = 1.0
    for attempt in range(6):
        resp = client.get(url, headers=headers)
        if resp.status_code == 429:
            time.sleep(delay)
            delay = min(delay * 2, 30.0)
            continue
        return resp
    resp.raise_for_status()
    return resp


def sync_games(
    conn: sqlite3.Connection, config: Config, max_games: int | None = None
) -> FetchStats:
    """Syncs games for config.chesscom_username. With max_games set (used by
    the web app's "review my last N games"), walks archive months newest-first
    and stops once at least that many games of config.time_class are in the
    DB for this user - so a review session doesn't have to wait for a full
    history backfill. Without it (the CLI/weekly report path), walks every
    month oldest-first as before, unchanged."""
    stats = FetchStats()
    headers = {"User-Agent": config.user_agent}

    with httpx.Client(timeout=30.0) as client:
        archives_resp = _request_with_backoff(
            client,
            f"{BASE_URL}/player/{config.chesscom_username}/games/archives",
            headers,
        )
        archives_resp.raise_for_status()
        archive_urls = archives_resp.json()["archives"]
        if max_games is not None:
            archive_urls = list(reversed(archive_urls))

        for archive_url in archive_urls:
            if max_games is not None:
                have = conn.execute(
                    "SELECT COUNT(*) FROM games WHERE time_class = ?",
                    (config.time_class,),
                ).fetchone()[0]
                if have >= max_games:
                    break
            stats.archives_checked += 1
            row = conn.execute(
                "SELECT etag FROM archive_sync WHERE archive_url = ?", (archive_url,)
            ).fetchone()
            prev_etag = row["etag"] if row else None

            req_headers = dict(headers)
            if prev_etag:
                req_headers["If-None-Match"] = prev_etag

            resp = _request_with_backoff(client, archive_url, req_headers)

            if resp.status_code == 304:
                stats.archives_skipped_unchanged += 1
                continue
            resp.raise_for_status()

            new_etag = resp.headers.get("ETag")
            games = resp.json().get("games", [])
            for game in games:
                stats.games_seen += 1
                if _insert_game(conn, config, game):
                    stats.games_inserted += 1

            conn.execute(
                """
                INSERT INTO archive_sync (archive_url, etag, fetched_at)
                VALUES (?, ?, ?)
                ON CONFLICT(archive_url) DO UPDATE SET etag = excluded.etag,
                                                        fetched_at = excluded.fetched_at
                """,
                (archive_url, new_etag, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()

    return stats


def _insert_game(conn: sqlite3.Connection, config: Config, game: dict) -> bool:
    uuid = game.get("uuid")
    if not uuid:
        return False

    existing = conn.execute(
        "SELECT 1 FROM games WHERE chesscom_uuid = ?", (uuid,)
    ).fetchone()
    if existing:
        return False

    username_lower = config.chesscom_username.lower()
    white = game["white"]
    black = game["black"]
    if white["username"].lower() == username_lower:
        my_side, opp_side, my_colour = white, black, "white"
    elif black["username"].lower() == username_lower:
        my_side, opp_side, my_colour = black, white, "black"
    else:
        # Game belongs to someone else's archive data (shouldn't happen) - skip.
        return False

    result = _normalize_result(my_side.get("result", ""))
    played_at = datetime.fromtimestamp(game["end_time"], tz=timezone.utc).isoformat()
    eco = _eco_from_pgn(game.get("pgn", ""))

    conn.execute(
        """
        INSERT INTO games (chesscom_uuid, played_at, time_class, my_colour, result,
                            my_rating, opp_rating, eco, pgn, analysed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            uuid,
            played_at,
            game.get("time_class", "unknown"),
            my_colour,
            result,
            my_side.get("rating"),
            opp_side.get("rating"),
            eco,
            game.get("pgn", ""),
        ),
    )
    return True


def _eco_from_pgn(pgn: str) -> str | None:
    for line in pgn.splitlines():
        if line.startswith("[ECO "):
            # Line looks like: [ECO "C44"]
            start = line.find('"')
            end = line.rfind('"')
            if start != -1 and end != -1 and end > start:
                return line[start + 1 : end]
    return None
