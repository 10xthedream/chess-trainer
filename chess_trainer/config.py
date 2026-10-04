"""Loads configuration from .env (or real environment variables)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required config: {name}. Copy .env.example to .env and fill it in."
        )
    return value


@dataclass(frozen=True)
class Config:
    chesscom_username: str
    chesscom_contact_email: str
    stockfish_path: str
    time_class: str
    stockfish_nodes: int
    stockfish_threads: int
    db_path: str
    obsidian_vault_path: str

    @property
    def user_agent(self) -> str:
        return (
            f"chess-trainer/0.1 (personal use; contact: {self.chesscom_contact_email})"
        )


def load_config() -> Config:
    return Config(
        chesscom_username=_require("CHESSCOM_USERNAME"),
        chesscom_contact_email=_require("CHESSCOM_CONTACT_EMAIL"),
        stockfish_path=_require("STOCKFISH_PATH"),
        time_class=os.environ.get("TIME_CLASS", "rapid").strip().lower(),
        stockfish_nodes=int(os.environ.get("STOCKFISH_NODES", "1000000")),
        stockfish_threads=int(os.environ.get("STOCKFISH_THREADS", "1")),
        db_path=os.environ.get("DB_PATH", "chess_trainer.db").strip(),
        obsidian_vault_path=_require("OBSIDIAN_VAULT_PATH"),
    )
