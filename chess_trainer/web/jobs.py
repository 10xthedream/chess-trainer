"""Single-worker background job runner. No Celery/RQ/asyncio task queue - one
threading.Thread, one job at a time, state in an in-memory dict the UI polls.
Each job opens its own sqlite3 connection (connections can't cross threads).

No persistence needed for job state: analyze_pending_games and tag_flagged
already commit per game/position, so if the server dies mid-job, re-running
the same session picks up where it left off (see queries.get_queue, which
only shows what's actually analyzed/tagged in the DB).
"""
from __future__ import annotations

import dataclasses
import threading
import uuid
from dataclasses import dataclass, field

from .. import chesscom, tag_runner
from ..analyze import analyze_pending_games
from ..config import Config
from ..db import connect
from . import queries


@dataclass
class JobState:
    stage: str = "pending"  # syncing | analyzing | tagging | done | error
    done: int = 0
    total: int = 0
    message: str = ""
    session_id: int | None = None


_jobs: dict[str, JobState] = {}
_lock = threading.Lock()
_current_job_id: str | None = None


def get_job(job_id: str) -> JobState | None:
    with _lock:
        return _jobs.get(job_id)


def start_review_job(
    config: Config,
    username: str,
    time_class: str,
    n_games: int,
    max_positions: int | None,
    severities: list[str],
) -> dict:
    """Creates the session, links it to the N most recent games (after
    syncing), and kicks off analyze+tag in the background. Returns
    {session_id, job_id}. Raises RuntimeError('busy') if a job is already
    running - the API layer turns that into a 409."""
    global _current_job_id
    with _lock:
        if _current_job_id is not None and _jobs[_current_job_id].stage not in ("done", "error"):
            raise RuntimeError("busy")
        job_id = str(uuid.uuid4())
        _jobs[job_id] = JobState(stage="syncing")
        _current_job_id = job_id

    conn = connect(config.db_path)
    session_config = dataclasses.replace(config, chesscom_username=username, time_class=time_class)
    session_id = queries.create_session(conn, username, time_class, n_games, max_positions, severities)
    conn.close()

    thread = threading.Thread(
        target=_run_job, args=(job_id, session_config, session_id, n_games), daemon=True
    )
    thread.start()
    return {"session_id": session_id, "job_id": job_id}


def _set(job_id: str, **kwargs) -> None:
    with _lock:
        state = _jobs[job_id]
        for k, v in kwargs.items():
            setattr(state, k, v)


def _run_job(job_id: str, config: Config, session_id: int, n_games: int) -> None:
    conn = connect(config.db_path)
    try:
        _set(job_id, stage="syncing", session_id=session_id)
        chesscom.sync_games(conn, config, max_games=n_games)

        game_ids = queries.pick_recent_games(conn, config.chesscom_username, config.time_class, n_games)
        queries.link_session_games(conn, session_id, game_ids)

        _set(job_id, stage="analyzing", total=len(game_ids), done=0)

        def on_analyze_progress(i, total, _row):
            _set(job_id, done=i, total=total)

        analyze_pending_games(conn, config, game_ids=game_ids, on_progress=on_analyze_progress)

        _set(job_id, stage="tagging", done=0, total=0)

        def on_tag_progress(i, total):
            _set(job_id, done=i, total=total)

        tag_runner.tag_flagged(
            conn, config, severities=("blunder", "mistake"), game_ids=game_ids,
            on_progress=on_tag_progress,
        )

        _set(job_id, stage="done", message="Ready to review.")
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI, not swallowed
        _set(job_id, stage="error", message=str(exc))
    finally:
        conn.close()
