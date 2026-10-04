"""Turns analyzed positions into the weekly markdown report dropped into Obsidian.

MVP version: no "why" tags exist yet, so the report can only speak in terms of
severity, phase, and time spent per move - not root causes. That's intentional
(see chess-trainer-mvp-plan.md): this report is what gets run for 4-6 weeks to
test whether "tell me my blunder rate and where it happens" moves the needle at
all, before any tagging/drilling code gets written.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import Config

PHASES = ("opening", "middlegame", "endgame")


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())  # Monday


def _severity_counts(rows) -> dict:
    counts = {"blunder": 0, "mistake": 0, "inaccuracy": 0, "ok": 0}
    for r in rows:
        if r["severity"] in counts:
            counts[r["severity"]] += 1
    return counts


def _rate_per_100(count: int, total_moves: int) -> float:
    return round((count / total_moves) * 100, 2) if total_moves else 0.0


def compute_and_store_weekly_reports(conn: sqlite3.Connection, config: Config) -> list[str]:
    """Recomputes weekly_report rows for every week that has analyzed, my-move
    positions in the target time class. Returns the list of week_start dates
    (ISO strings) touched."""
    my_moves = conn.execute(
        """
        SELECT p.*, g.played_at FROM positions p
        JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND g.time_class = ?
        """,
        (config.time_class,),
    ).fetchall()

    by_week: dict[str, list] = {}
    for row in my_moves:
        played = datetime.fromisoformat(row["played_at"]).date()
        week_key = _week_start(played).isoformat()
        by_week.setdefault(week_key, []).append(row)

    games_by_week: dict[str, set] = {}
    for row in my_moves:
        played = datetime.fromisoformat(row["played_at"]).date()
        week_key = _week_start(played).isoformat()
        games_by_week.setdefault(week_key, set()).add(row["game_id"])

    touched = []
    for week_key, rows in sorted(by_week.items()):
        counts = _severity_counts(rows)
        total_moves = len(rows)

        by_phase = {}
        for phase in PHASES:
            phase_rows = [r for r in rows if r["phase"] == phase]
            phase_counts = _severity_counts(phase_rows)
            by_phase[phase] = {
                "moves": len(phase_rows),
                "blunders": phase_counts["blunder"],
                "mistakes": phase_counts["mistake"],
                "inaccuracies": phase_counts["inaccuracy"],
                "blunders_per_100": _rate_per_100(phase_counts["blunder"], len(phase_rows)),
            }

        conn.execute(
            """
            INSERT INTO weekly_report
                (week_start, games_count, my_moves_count, blunders, mistakes,
                 inaccuracies, blunders_per_100, by_phase_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                games_count = excluded.games_count,
                my_moves_count = excluded.my_moves_count,
                blunders = excluded.blunders,
                mistakes = excluded.mistakes,
                inaccuracies = excluded.inaccuracies,
                blunders_per_100 = excluded.blunders_per_100,
                by_phase_json = excluded.by_phase_json,
                generated_at = excluded.generated_at
            """,
            (
                week_key,
                len(games_by_week[week_key]),
                total_moves,
                counts["blunder"],
                counts["mistake"],
                counts["inaccuracy"],
                _rate_per_100(counts["blunder"], total_moves),
                json.dumps(by_phase),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        touched.append(week_key)

    conn.commit()
    return touched


def _rushed_signal(conn: sqlite3.Connection, config: Config) -> str | None:
    """Rough preview of the 'rushed' tag (see plan note #8) before real tagging
    exists: compares median time spent on blunder moves vs ok moves, when
    clock data is available at all for this time class."""
    rows = conn.execute(
        """
        SELECT p.severity, p.time_spent_s FROM positions p
        JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND g.time_class = ? AND p.time_spent_s IS NOT NULL
        """,
        (config.time_class,),
    ).fetchall()
    if len(rows) < 20:
        return None

    blunder_times = sorted(r["time_spent_s"] for r in rows if r["severity"] == "blunder")
    ok_times = sorted(r["time_spent_s"] for r in rows if r["severity"] == "ok")
    if not blunder_times or not ok_times:
        return None

    def median(xs):
        n = len(xs)
        return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2

    med_blunder, med_ok = median(blunder_times), median(ok_times)
    if med_blunder < med_ok * 0.5:
        return (
            f"Your blunders are played noticeably faster than your normal moves "
            f"(median {med_blunder:.1f}s vs {med_ok:.1f}s) - a preview of what will "
            f"become the `rushed` tag once tagging is built. Worth slowing down on "
            f"moves that matter."
        )
    return None


def write_report_markdown(conn: sqlite3.Connection, config: Config) -> Path:
    all_weeks = conn.execute(
        "SELECT * FROM weekly_report ORDER BY week_start"
    ).fetchall()

    totals = conn.execute(
        """
        SELECT COUNT(*) as moves,
               SUM(CASE WHEN severity='blunder' THEN 1 ELSE 0 END) as blunders,
               SUM(CASE WHEN severity='mistake' THEN 1 ELSE 0 END) as mistakes,
               SUM(CASE WHEN severity='inaccuracy' THEN 1 ELSE 0 END) as inaccuracies
        FROM positions p JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND g.time_class = ?
        """,
        (config.time_class,),
    ).fetchone()

    games_analyzed = conn.execute(
        "SELECT COUNT(*) as n FROM games WHERE analysed_at IS NOT NULL AND time_class = ?",
        (config.time_class,),
    ).fetchone()["n"]

    by_phase_overall = {}
    for phase in PHASES:
        r = conn.execute(
            """
            SELECT COUNT(*) as moves,
                   SUM(CASE WHEN severity='blunder' THEN 1 ELSE 0 END) as blunders
            FROM positions p JOIN games g ON g.id = p.game_id
            WHERE p.is_my_move = 1 AND g.time_class = ? AND p.phase = ?
            """,
            (config.time_class, phase),
        ).fetchone()
        by_phase_overall[phase] = {
            "moves": r["moves"] or 0,
            "blunders": r["blunders"] or 0,
            "rate": _rate_per_100(r["blunders"] or 0, r["moves"] or 0),
        }

    worst_phase = max(by_phase_overall, key=lambda p: by_phase_overall[p]["rate"])

    tag_counts = conn.execute(
        """
        SELECT e.tag, COUNT(*) as n
        FROM error_tags e
        JOIN games g ON g.id = e.game_id
        WHERE g.time_class = ? AND e.tag != 'untagged'
        GROUP BY e.tag ORDER BY n DESC
        """,
        (config.time_class,),
    ).fetchall()
    total_tagged_positions = conn.execute(
        """
        SELECT COUNT(DISTINCT e.game_id || '-' || e.ply) FROM error_tags e
        JOIN games g ON g.id = e.game_id WHERE g.time_class = ?
        """,
        (config.time_class,),
    ).fetchone()[0]

    lines = []
    lines.append(f"# Chess Trainer — Baseline Report ({config.time_class})")
    lines.append("")
    lines.append(f"_Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    lines.append(
        f"**{games_analyzed} games analyzed, {totals['moves']} of my own moves.** "
        f"{totals['blunders']} blunders, {totals['mistakes']} mistakes, "
        f"{totals['inaccuracies']} inaccuracies "
        f"({_rate_per_100(totals['blunders'], totals['moves'])} blunders per 100 moves overall)."
    )
    lines.append("")
    lines.append("## By phase")
    lines.append("")
    lines.append("| Phase | My moves | Blunders | Blunders / 100 moves |")
    lines.append("|---|---|---|---|")
    for phase in PHASES:
        d = by_phase_overall[phase]
        lines.append(f"| {phase} | {d['moves']} | {d['blunders']} | {d['rate']} |")
    lines.append("")

    # Lichess puzzle themes matched to each tag, for the "go drill this" link.
    TAG_PUZZLE_THEME = {
        "hung_piece": "hangingPiece",
        "missed_capture": "advantage",
        "unsound_sacrifice": "sacrifice",
        "king_safety": "exposedKing",
        "ignored_threat": "defensiveMove",
        "self_obstruction": "middlegame",
        "wasted_tempo": "opening",
    }
    TAG_LABEL = {
        "hung_piece": "Hung pieces",
        "missed_capture": "Missed free captures",
        "unsound_sacrifice": "Unsound sacrifices",
        "king_safety": "Self-exposed king",
        "ignored_threat": "Ignored threats",
        "self_obstruction": "Blocked your own pieces",
        "wasted_tempo": "Wasted tempo (no material lost)",
    }

    lines.append(
        f"## Root-cause tags ({total_tagged_positions} blunders tagged, {config.time_class})"
    )
    lines.append("")
    lines.append(
        "7 tags, validated against 29 hand-labeled positions at ~79% precision "
        "(see chess-trainer-mvp-plan.md) - treat these as a strong signal, not gospel."
    )
    lines.append("")
    lines.append("| Tag | Count | % of blunders |")
    lines.append("|---|---|---|")
    for row in tag_counts:
        pct = round(row["n"] / total_tagged_positions * 100, 1) if total_tagged_positions else 0
        lines.append(f"| {TAG_LABEL.get(row['tag'], row['tag'])} | {row['n']} | {pct}% |")
    lines.append("")

    lines.append("## This period's top 3 actions")
    lines.append("")
    actions = []
    top_tags = [r for r in tag_counts if r["tag"] in TAG_PUZZLE_THEME][:2]
    for i, row in enumerate(top_tags, 1):
        theme = TAG_PUZZLE_THEME[row["tag"]]
        actions.append(
            f"{i}. **{TAG_LABEL.get(row['tag'], row['tag'])}** is your #{i} most common "
            f"blunder cause ({row['n']} occurrences). Drill "
            f"[lichess.org/training/{theme}](https://lichess.org/training/{theme}) "
            f"for 10-15 min this week."
        )
    rushed = _rushed_signal(conn, config)
    if rushed:
        actions.append(f"{len(actions) + 1}. {rushed}")
    if len(actions) < 3:
        actions.append(
            f"{len(actions) + 1}. Your blunder rate is highest in the **{worst_phase}** "
            f"({by_phase_overall[worst_phase]['rate']} per 100 moves) - worth extra focus there too."
        )
    lines.extend(actions)
    lines.append("")

    blunder_examples = conn.execute(
        """
        SELECT g.id as game_id, g.chesscom_uuid, p.ply, p.san, p.fen_before, p.phase
        FROM positions p JOIN games g ON g.id = p.game_id
        WHERE p.is_my_move = 1 AND g.time_class = ? AND p.severity = 'blunder'
        ORDER BY g.played_at DESC LIMIT 10
        """,
        (config.time_class,),
    ).fetchall()
    if blunder_examples:
        lines.append("## Most recent 10 blunders")
        lines.append("")
        lines.append("| Game | Ply | Move | Phase | Tags | Position before |")
        lines.append("|---|---|---|---|---|---|")
        for b in blunder_examples:
            tag_rows = conn.execute(
                "SELECT tag FROM error_tags WHERE game_id = ? AND ply = ? AND tag != 'untagged'",
                (b["game_id"], b["ply"]),
            ).fetchall()
            tags_str = ", ".join(t["tag"] for t in tag_rows) or "-"
            lines.append(
                f"| [{b['chesscom_uuid'][:8]}](https://www.chess.com/game/live/{b['chesscom_uuid']}) "
                f"| {b['ply']} | {b['san']} | {b['phase']} | {tags_str} | `{b['fen_before']}` |"
            )
        lines.append("")

    if all_weeks:
        lines.append("## Trend (blunders per 100 moves, by week)")
        lines.append("")
        lines.append("| Week of | Games | My moves | Blunders/100 |")
        lines.append("|---|---|---|---|")
        for w in all_weeks:
            lines.append(
                f"| {w['week_start']} | {w['games_count']} | {w['my_moves_count']} | "
                f"{w['blunders_per_100']} |"
            )
        lines.append("")

    content = "\n".join(lines)

    vault = Path(config.obsidian_vault_path)
    vault.mkdir(parents=True, exist_ok=True)
    out_path = vault / "chess-trainer-baseline-report.md"
    out_path.write_text(content, encoding="utf-8")

    conn.execute(
        "UPDATE weekly_report SET report_markdown_path = ? WHERE week_start = (SELECT MAX(week_start) FROM weekly_report)",
        (str(out_path),),
    )
    conn.commit()

    return out_path
