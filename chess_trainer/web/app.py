"""FastAPI app: 8 JSON endpoints plus the static page. Localhost only, no
auth (see chess-trainer-review-app-plan.md - adding auth is the one thing
that would need to change before this is ever exposed beyond 127.0.0.1).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

from ..config import load_config
from ..db import get_conn, init_db
from . import drill_api, jobs, practice, queries

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Chess Trainer Review")


def _config():
    return load_config()


@app.on_event("startup")
def _startup() -> None:
    config = _config()
    init_db(config.db_path, default_username=config.chesscom_username)


# --- Request/response models -------------------------------------------------

class CreateSessionRequest(BaseModel):
    username: str
    n_games: int = 10
    severities: list[Literal["blunder", "mistake", "inaccuracy"]] = ["blunder", "mistake"]
    max_positions: int | None = 30

    @field_validator("n_games")
    @classmethod
    def _n_games_range(cls, v: int) -> int:
        if not (1 <= v <= 50):
            raise ValueError("n_games must be between 1 and 50")
        return v


class DrillReviewRequest(BaseModel):
    uci: str
    seconds_spent: float


class PracticeMoveRequest(BaseModel):
    fen: str
    uci: str
    moves_so_far: list[str] = []


class PracticeResignRequest(BaseModel):
    moves_so_far: list[str] = []


class LabelRequest(BaseModel):
    text: str | None = None
    tags: list[str] = []
    confidence: Literal["sure", "unsure"] | None = None
    status: Literal["labeled", "skipped"] = "labeled"
    session_id: int | None = None
    seconds_spent: float | None = None

    @field_validator("tags")
    @classmethod
    def _valid_tags(cls, v: list[str]) -> list[str]:
        valid = set(queries.TAG_KEYS)
        bad = [t for t in v if t not in valid]
        if bad:
            raise ValueError(f"Unknown tag(s): {bad}")
        return v


# --- Routes -------------------------------------------------------------------

@app.get("/api/config")
def api_config():
    config = _config()
    return {"default_username": config.chesscom_username, "time_class": config.time_class}


@app.get("/api/tags")
def api_tags():
    return queries.tag_catalog()


@app.post("/api/sessions")
def api_create_session(req: CreateSessionRequest):
    config = _config()
    try:
        result = jobs.start_review_job(
            config, req.username, config.time_class, req.n_games, req.max_positions, req.severities
        )
    except RuntimeError:
        raise HTTPException(status_code=409, detail="A review job is already running.")
    return result


@app.get("/api/sessions")
def api_list_sessions():
    config = _config()
    with get_conn(config.db_path) as conn:
        return queries.list_unfinished_sessions(conn)


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str):
    state = jobs.get_job(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="No such job.")
    return {
        "stage": state.stage, "done": state.done, "total": state.total,
        "message": state.message, "session_id": state.session_id,
    }


@app.get("/api/sessions/{session_id}/queue")
def api_queue(session_id: int, include_labeled: bool = False):
    config = _config()
    with get_conn(config.db_path) as conn:
        queue = queries.get_queue(conn, session_id, include_labeled=include_labeled)
        pending_games = len(
            [g for g in queries.session_game_ids(conn, session_id)]
        )
    return {"queue": queue, "pending_games": pending_games}


@app.get("/api/positions/{game_id}/{ply}")
def api_position(game_id: int, ply: int):
    config = _config()
    with get_conn(config.db_path) as conn:
        detail = queries.get_position_detail(conn, game_id, ply)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such position.")
    return detail


@app.put("/api/labels/{game_id}/{ply}")
def api_save_label(game_id: int, ply: int, req: LabelRequest):
    if req.status == "labeled" and not req.text and not req.tags:
        raise HTTPException(status_code=400, detail="Provide text or at least one tag.")
    config = _config()
    with get_conn(config.db_path) as conn:
        queries.upsert_label(
            conn, game_id, ply, req.text, req.tags, req.confidence, req.status,
            req.session_id, req.seconds_spent,
        )
    return {"ok": True}


@app.get("/api/lessons")
def api_lessons():
    config = _config()
    with get_conn(config.db_path) as conn:
        return practice.list_lessons(conn)


@app.get("/api/lessons/{lesson_id}")
def api_lesson_detail(lesson_id: str):
    config = _config()
    with get_conn(config.db_path) as conn:
        detail = practice.lesson_detail(conn, lesson_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such lesson.")
    return detail


@app.post("/api/lessons/{lesson_id}/move")
def api_lesson_move(lesson_id: str, req: PracticeMoveRequest):
    config = _config()
    with get_conn(config.db_path) as conn:
        try:
            return practice.apply_move(conn, lesson_id, req.fen, req.uci, req.moves_so_far)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/lessons/{lesson_id}/resign")
def api_lesson_resign(lesson_id: str, req: PracticeResignRequest):
    config = _config()
    with get_conn(config.db_path) as conn:
        try:
            return practice.resign(conn, lesson_id, req.moves_so_far)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/lessons/{lesson_id}/hint")
def api_lesson_hint(lesson_id: str, fen: str):
    config = _config()
    with get_conn(config.db_path) as conn:
        try:
            return practice.hint(conn, lesson_id, fen)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/drill/queue")
def api_drill_queue(eco: str | None = None, tag: str | None = None, limit: int = 15):
    config = _config()
    with get_conn(config.db_path) as conn:
        return drill_api.queue(conn, limit=limit, eco=eco, tag=tag)


@app.get("/api/drill/card/{game_id}/{ply}")
def api_drill_card(game_id: int, ply: int):
    config = _config()
    with get_conn(config.db_path) as conn:
        detail = drill_api.card_detail(conn, game_id, ply)
    if detail is None:
        raise HTTPException(status_code=404, detail="No such card.")
    return detail


@app.post("/api/drill/review/{game_id}/{ply}")
def api_drill_review(game_id: int, ply: int, req: DrillReviewRequest):
    config = _config()
    with get_conn(config.db_path) as conn:
        try:
            return drill_api.review(conn, config, game_id, ply, req.uci, req.seconds_spent)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))


@app.get("/api/sessions/{session_id}/summary")
def api_summary(session_id: int):
    config = _config()
    with get_conn(config.db_path) as conn:
        summary = queries.get_summary(conn, session_id)
        queue = queries.get_queue(conn, session_id, include_labeled=True)
        if queue and all(q["done"] for q in queue):
            queries.mark_session_complete(conn, session_id)
    return summary


# --- Static page --------------------------------------------------------------
# Deliberately not mounting StaticFiles at "/" - a root mount is a catch-all
# prefix in Starlette and would risk shadowing the /api/* routes above
# depending on registration order. Explicit routes for the handful of static
# files this app actually has, plus a scoped mount for the vendor/ subtree
# (a distinct, non-root prefix, so it can't shadow anything).

app.mount("/vendor", StaticFiles(directory=STATIC_DIR / "vendor"), name="vendor")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")


@app.get("/app.css")
def app_css():
    return FileResponse(STATIC_DIR / "app.css", media_type="text/css")
