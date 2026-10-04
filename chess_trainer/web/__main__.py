"""python -m chess_trainer.web - starts the review app on localhost and opens
the browser. Closing this terminal stops the server; nothing stays resident
(see README.md's Automation section for the separate weekly-pipeline task,
which is unrelated to this)."""
from __future__ import annotations

import threading
import webbrowser

import uvicorn

HOST = "127.0.0.1"
PORT = 8765


def _open_browser() -> None:
    webbrowser.open(f"http://{HOST}:{PORT}")


def main() -> None:
    threading.Timer(1.0, _open_browser).start()
    uvicorn.run("chess_trainer.web.app:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
