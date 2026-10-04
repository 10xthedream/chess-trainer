# Chess Trainer

Personal, single-user chess improvement tool. Full plan: see the Obsidian vault notes
`chess-trainer-mvp-plan.md` and `chess-trainer-review-app-plan.md`.

## What this is, right now

Pulls your chess.com games, runs Stockfish on every move, classifies severity
(inaccuracy/mistake/blunder) by win% drop, tags the game phase, and auto-tags *why*
each blunder happened (7 root-cause tags: hung piece, missed capture, unsound
sacrifice, self-exposed king, ignored threat, self-obstruction, wasted tempo) -
validated against hand-labeled positions, not just asserted. A weekly report drops
into Obsidian with the real breakdown and top actions. A small local web app lets
you review flagged positions on a real board and label them yourself, which both
trains you and grows the ground truth the tagger gets checked against.

An endgame-first curriculum (Units 1-4: king+pawn endings, basic checkmates, rook
endings, minor piece endings; Unit 5: queen endings) is playable from the CLI,
graded live against the Lichess tablebase API. Unit 6 (tactics) routes through the
weekly report's puzzle-theme links instead of being separately authored. Units 7-8
(middlegame and opening principles) are review lessons pointing back at your own
flagged games, since there's no tablebase for "was this a good middlegame."

An FSRS-scheduled drill loop (`chess_trainer.cli drill-build` / `drill`) turns your
own flagged blunders and mistakes into spaced-repetition cards: each one asks what
you should have played instead, graded against the engine's best move.

## Setup

1. Install Python 3.12+.
2. Install Stockfish and note the path to the binary (`which stockfish` on
   Linux/macOS, or download the Windows build and note the `.exe` path).
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in:
   - `CHESSCOM_USERNAME` — your chess.com username
   - `CHESSCOM_CONTACT_EMAIL` — chess.com asks API clients to identify a contact
     email in the User-Agent header so they can reach you instead of blocking you
     if something misbehaves. Put your own email here.
   - `STOCKFISH_PATH` — full path to the Stockfish binary
   - `STOCKFISH_THREADS` — CPU threads Stockfish uses per position (set to your
     physical core count, leave 1-2 free for the OS)
   - `TIME_CLASS` — which chess.com time class to analyze (`rapid`, `blitz`,
     `bullet`, or `daily`). Pick whichever one is most of your games and the one
     you actually want to improve at — mixing time controls dilutes the signal.
   - `OBSIDIAN_VAULT_PATH` — where to drop the weekly report (defaults to the
     vault path already used for other notes)

## Running it

```
python -m chess_trainer.cli fetch      # incremental sync of your games -> SQLite
python -m chess_trainer.cli analyze    # run Stockfish on unanalyzed positions
python -m chess_trainer.cli tag        # auto-tag root causes for blunders
python -m chess_trainer.cli report     # write this week's markdown report
```

Or `python -m chess_trainer.cli run` to do all four in sequence (this is what the
weekly scheduled task calls - see Automation below).

First run against ~300 games will take a while depending on your machine and the
configured node budget (`STOCKFISH_NODES` in `.env`, default 1,000,000 nodes/position).
Safe to stop and resume; already analyzed/tagged positions are skipped.

Endgame curriculum:

```
python -m chess_trainer.cli curriculum-ingest           # load + tablebase-verify lessons
python -m chess_trainer.cli curriculum-list              # see what's available
python -m chess_trainer.cli curriculum-practice <id>      # play one out, e.g. u1-01
```

Own-blunder drill loop (FSRS):

```
python -m chess_trainer.cli drill-build   # turn newly flagged blunders/mistakes into cards
python -m chess_trainer.cli drill         # practice whatever's due
```

`drill-build` computes the engine's best move for each new flagged position once,
at build time. `drill` shows the position before your mistake, you enter the move
you'd play now, and it's graded by win%-drop vs. the stored best move: within 5% is
Good (Easy if solved in under 5s on a card's first rep), 5-15% is Hard, 15%+ (i.e.
you made another blunder-tier move) is Again. FSRS (the `fsrs` package) schedules
the next review from there - run `drill` whenever, due cards show up automatically.

## Review app (labeling your own blunders)

```
python -m chess_trainer.web
```

Opens a local page at `http://127.0.0.1:8765`. Enter your chess.com username, pick
how many recent games to review, and it shows each flagged move one at a time on a
real board (coordinates, the opponent's previous move highlighted, your move marked
with an arrow) with a free-text box and one-key tag shortcuts. Saves straight to
SQLite - no more copy-pasting labels back into chat. Closing the terminal stops it;
nothing stays resident between sessions. See `chess-trainer-review-app-plan.md` for
the full design.

## Automation

`run_weekly.ps1` runs the full pipeline (`fetch` -> `analyze` -> `tag` -> `report`)
and logs to `logs/`. It's registered as a Windows Scheduled Task
(`ChessTrainerWeeklyRun`, Sundays 9am, catches up on next boot if the machine
was off). Manage it with:

```
Get-ScheduledTask -TaskName ChessTrainerWeeklyRun          # check status
Start-ScheduledTask -TaskName ChessTrainerWeeklyRun         # run now
Unregister-ScheduledTask -TaskName ChessTrainerWeeklyRun    # remove it
```

Runs only while you're logged in (default Task Scheduler behavior) - fine for
a machine that's generally on and logged in, not set up for a headless server.

## Known limitations (by design, see the plan docs for why)

- Root-cause tags are heuristics, not gospel - the weekly report says so explicitly.
  A same-day hand-labeling pass against the automated tagger (2026-10-04, 25
  positions) found only ~24% agreement, well below the original 79.2% validation
  figure from the initial 29-label set - worth digging into before trusting the
  tagger's categorization on anything beyond raw counts.
- The FSRS drill loop's grading is win%-drop vs. the engine's single best move -
  it doesn't yet credit a different move that's objectively just as good (multi-PV
  tolerance), so an equally-strong alternative to the engine's top choice can grade
  as Hard/Again when it shouldn't.
- Curriculum Units 7-8 (middlegame/opening) are review lessons pointing at your own
  games, not graded positions - there's no tablebase for "was this well played."
- The review app is local-only, no auth - fine since it only binds to 127.0.0.1.
