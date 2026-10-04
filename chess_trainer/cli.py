"""CLI entry point.

Usage:
    python -m chess_trainer.cli fetch
    python -m chess_trainer.cli analyze
    python -m chess_trainer.cli report
    python -m chess_trainer.cli run        # fetch + analyze + tag + report
    python -m chess_trainer.cli drill-build  # turn new flagged positions into FSRS cards
    python -m chess_trainer.cli drill        # practice due cards
"""
from __future__ import annotations

import sys

from . import chesscom, curriculum, drill, report, tag_runner
from .analyze import analyze_pending_games
from .config import load_config
from .db import get_conn, init_db


def cmd_fetch() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        stats = chesscom.sync_games(conn, config)
    print(
        f"Checked {stats.archives_checked} archive months "
        f"({stats.archives_skipped_unchanged} unchanged, skipped). "
        f"Saw {stats.games_seen} games, inserted {stats.games_inserted} new."
    )


def cmd_analyze() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        n = analyze_pending_games(conn, config)
    print(f"Analyzed {n} games (time_class={config.time_class}).")
    if n == 0:
        print(
            "Nothing to analyze - either no new games, or none match TIME_CLASS "
            f"in .env ('{config.time_class}')."
        )


def cmd_report() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        weeks = report.compute_and_store_weekly_reports(conn, config)
        out_path = report.write_report_markdown(conn, config)
    print(f"Updated {len(weeks)} week(s) of stats. Report written to:\n  {out_path}")


def cmd_run() -> None:
    cmd_fetch()
    cmd_analyze()
    cmd_tag()
    cmd_report()


def cmd_curriculum_ingest() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        warnings = curriculum.ingest_lessons(conn)
    if warnings:
        print("\nWarnings:")
        for w in warnings:
            print(f"  - {w}")


def cmd_curriculum_list() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        rows = curriculum.list_lessons(conn)
    if not rows:
        print("No lessons loaded yet - run 'curriculum-ingest' first.")
        return
    current_unit = None
    for r in rows:
        if r["unit"] != current_unit:
            current_unit = r["unit"]
            print(f"\nUnit {r['unit']}: {r['unit_name']}")
        status = r["target_result"] or r["exercise_type"]
        print(f"  {r['id']:12} {r['title']:55} [{status}]")


def cmd_curriculum_practice() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m chess_trainer.cli curriculum-practice <lesson-id>")
        sys.exit(1)
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        curriculum.practice_lesson(conn, sys.argv[2])


def cmd_tag() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        n = tag_runner.tag_all_blunders(conn, config)
    print(f"Tagged {n} blunder position(s).")


def cmd_drill_build() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        n = drill.build_cards(conn, config, on_progress=lambda i, t: print(f"  {i}/{t}") if i % 10 == 0 else None)
    print(f"Built {n} new drill card(s) from flagged positions.")


def cmd_drill() -> None:
    config = load_config()
    init_db(config.db_path, default_username=config.chesscom_username)
    with get_conn(config.db_path) as conn:
        drill.drill_session(conn, config)


COMMANDS = {
    "fetch": cmd_fetch,
    "analyze": cmd_analyze,
    "report": cmd_report,
    "run": cmd_run,
    "curriculum-ingest": cmd_curriculum_ingest,
    "curriculum-list": cmd_curriculum_list,
    "curriculum-practice": cmd_curriculum_practice,
    "tag": cmd_tag,
    "drill-build": cmd_drill_build,
    "drill": cmd_drill,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"Usage: python -m chess_trainer.cli {{{'|'.join(COMMANDS)}}}")
        print("       python -m chess_trainer.cli curriculum-practice <lesson-id>")
        sys.exit(1)
    COMMANDS[sys.argv[1]]()


if __name__ == "__main__":
    main()
