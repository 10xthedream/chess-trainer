"""Endgame-first curriculum content (Waitzkin/Steps-method-inspired structure).

Every `start_fen` below was hand-authored to illustrate a specific named
technique, then legality-checked with python-chess and had its `target_result`
filled in by querying the live Lichess tablebase API during ingestion
(`curriculum.ingest_lessons`) - never hand-asserted. That's deliberate: getting
"is this actually winning/drawing" right by hand for 18 endgame positions is
exactly the kind of thing that's easy to get subtly wrong, and the tablebase
is free, authoritative, and covers everything here (all positions are <=7
pieces, within the API's range - no local Syzygy download needed).

Units 5-8 (queen endings, tactics, middlegame, openings) were added 2026-10-04,
after the 4-6 week observation period was deliberately cut short (see the
Chess Trainer project memory / vault note for why). Unit 5 follows the same
tablebase-verified pattern as 1-4. Unit 6 was never meant to be separately
authored - see its single entry below, now wired up since tagging exists.
Units 7-8 (middlegame and opening principles) don't have a forced result to
grade against - there's no tablebase for "was this a well-played middlegame" -
so instead of inventing textbook positions with no ground truth, they're
review lessons that point back at the user's own flagged games, which is this
tool's actual differentiator anyway.
"""
from __future__ import annotations

UNIT_NAMES = {
    1: "King and pawn endings",
    2: "Basic checkmates",
    3: "Rook endings",
    4: "Minor piece endings",
    5: "Queen endings",
    6: "Tactical motifs",
    7: "Middlegame principles",
    8: "Opening principles",
}

# exercise_type: 'tablebase_dtz' (play it out, graded live against the tablebase),
#                'concept' (no forced result - instructional, self-assessed),
#                'roadmap' (not authored yet)

LESSONS = [
    # --- Unit 1: King and pawn endings ---
    dict(
        id="u1-01", unit=1, seq=1,
        title="The square of the pawn — easy catch",
        objective=(
            "Black to move. Walk your king toward the a-pawn and see whether you "
            "can stop it from queening. This one has margin to spare - the point "
            "is learning to count squares before you move, not finding a knife-edge line."
        ),
        start_fen="4k3/8/8/8/8/8/P7/4K3 b - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u1-02", unit=1, seq=2,
        title="The square of the pawn — too far away",
        objective=(
            "Black to move, king starts on the far side of the board. Try to catch "
            "the pawn anyway and watch it queen - the lesson is recognizing 'too late' "
            "early enough to not waste moves on a lost race."
        ),
        start_fen="7k/8/8/8/8/8/P7/K7 b - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u1-03", unit=1, seq=3,
        title="King and pawn vs king — key squares",
        objective=(
            "White to move, central pawn with the king two ranks behind it on the "
            "same file. This is the classic key-squares test: does pushing the pawn "
            "immediately help or hurt? Find out, then check whether the engine's "
            "declared result matches what you expected."
        ),
        start_fen="8/4k3/8/4K3/4P3/8/8/8 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u1-04", unit=1, seq=4,
        title="Rook pawn fortress — the corner draw",
        objective=(
            "White to move. Rook (a-file) pawns are the exception to normal king-and-pawn "
            "technique: if the defending king reaches the corner in front of the pawn, "
            "it's an unbreakable fortress regardless of whose move it is. Confirm it."
        ),
        start_fen="k7/8/K7/P7/8/8/8/8 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u1-05", unit=1, seq=5,
        title="Opposition — one file off makes all the difference",
        objective=(
            "White to move. Same pawn-and-king shape as the key-squares lesson, except "
            "the black king is one file to the side instead of directly in front. Play "
            "it out and compare the result to lesson u1-03 - a single file of difference "
            "in where the defending king stands changes everything."
        ),
        start_fen="8/3k4/8/4K3/4P3/8/8/8 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u1-06", unit=1, seq=6,
        title="Triangulation — losing a tempo on purpose",
        objective=(
            "White to move. Sometimes the winning try is to NOT advance - maneuvering "
            "the king in a triangle to hand the opponent the move instead. Try advancing "
            "immediately first, then try waiting, and see which one the tablebase rewards."
        ),
        start_fen="8/3k4/8/3K4/3P4/8/8/8 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u1-07", unit=1, seq=7,
        title="Outside passed pawn",
        objective=(
            "White to move. The central pawns are locked face to face; White has an "
            "extra pawn on the far side of the board. Use it to either queen or to drag "
            "the Black king away and invade with your own king on the other wing."
        ),
        start_fen="8/8/4k3/P3p3/4P3/4K3/8/8 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),

    # --- Unit 2: Basic checkmates ---
    dict(
        id="u2-01", unit=2, seq=1,
        title="King and queen vs king — box method",
        objective=(
            "White to move. Shrink the box the black king is trapped in with the queen, "
            "a knight's move away each time, then bring your own king up to deliver mate. "
            "Watch out for stalemate - it's the only way to mess this up."
        ),
        start_fen="8/8/8/4k3/8/8/3Q4/4K3 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u2-02", unit=2, seq=2,
        title="King and rook vs king — box method",
        objective=(
            "White to move. Same shrinking-box idea as the queen, slower since the rook "
            "alone can't restrict the king as tightly - you'll need your own king's help "
            "earlier than you did with the queen."
        ),
        start_fen="8/8/8/4k3/8/8/3R4/4K3 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u2-03", unit=2, seq=3,
        title="King and two bishops vs king",
        objective=(
            "White to move. The two bishops (on opposite-colored squares) herd the king "
            "to a corner together - neither one alone can do it. This is the mate most "
            "players never properly learn; take it slowly."
        ),
        start_fen="4k3/8/8/8/8/8/8/2B2BK1 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u2-04", unit=2, seq=4,
        title="King, bishop and knight vs king",
        objective=(
            "White to move. The hardest of the 'basic' mates - the king must be driven "
            "specifically into the corner that matches the bishop's color, and getting it "
            "there without stalemating or losing the win is notoriously easy to botch. "
            "Unlike the two-bishop mate, there's no shortcut: take your time."
        ),
        start_fen="4k3/8/8/8/3NK3/8/8/5B2 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u2-05", unit=2, seq=5,
        title="Two knights vs king — the mate that doesn't exist",
        objective=(
            "White to move, up two knights against a lone king. It looks overwhelming, "
            "and intuition says it should work like the two-bishop or bishop-and-knight "
            "mates - but with no pawn to exploit zugzwang, two knights alone can never "
            "force checkmate against correct defense. Play it out and watch the win "
            "evaporate; this is the one 'basic mate' that's actually just a draw."
        ),
        start_fen="4k3/8/8/8/8/2N2N2/8/4K3 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),

    # --- Unit 3: Rook endings ---
    dict(
        id="u3-01", unit=3, seq=1,
        title="Building the bridge (Lucena-type)",
        objective=(
            "White to move. Pawn one step from queening, your king shielding it, your "
            "rook far away, the defending king cut off and the defending rook giving "
            "checks from behind. Bring the rook to the 4th rank to 'build a bridge' and "
            "block the checks so the pawn can queen."
        ),
        start_fen="8/4PK1k/8/8/8/8/r7/1R6 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u3-02", unit=3, seq=2,
        title="Third-rank defense (Philidor-type)",
        objective=(
            "Black to move, defending. Keep your rook on the rank in front of the pawn "
            "until it advances to that rank - then switch to checking the attacking king "
            "from behind instead. This holds the draw against almost anything White tries."
        ),
        start_fen="4k3/8/r7/4K3/4P3/8/8/7R b - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u3-03", unit=3, seq=3,
        title="Converting an advanced knight-pawn vs a side-checking rook",
        objective=(
            "White to move. The pawn is already on the 6th rank with the king escorting "
            "it; Black's rook is trying the side-checking idea that holds the draw in true "
            "Vancura positions, but here it isn't quite enough. Find the king march that "
            "shields the checks and pushes the pawn home."
        ),
        start_fen="k7/6K1/6P1/8/2r5/8/8/7R w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u3-04", unit=3, seq=4,
        title="Extra pawn, blockaded — when 'rook behind the pawn' isn't enough",
        objective=(
            "White to move, up a clean pawn with the rook correctly placed behind it. It "
            "looks winning, but Black's king has reached the one square that matters: "
            "directly in front of the pawn. Try to make progress and see why that exact "
            "blockade - not just 'rook behind the pawn' - is the real drawing technique "
            "in rook endings."
        ),
        start_fen="1r6/4k3/8/4P3/4K3/8/8/4R3 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),

    # --- Unit 4: Minor piece endings ---
    dict(
        id="u4-01", unit=4, seq=1,
        title="Opposite-colored bishops — the drawing fortress",
        objective=(
            "White to move, up a pawn with bishops on opposite colors. This is the most "
            "notorious fortress in chess - an extra pawn often isn't enough. See how far "
            "you can push it, and notice how little progress a 'winning' extra pawn buys you."
        ),
        start_fen="8/4k3/8/4P3/4K1b1/8/8/2B5 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u4-02", unit=4, seq=2,
        title="Knight vs rook pawn",
        objective=(
            "Black to move. A knight can be surprisingly bad at stopping a rook pawn "
            "escorted by its king, because it can run out of safe squares near the "
            "corner. Try to hold it and see where the knight gets into trouble."
        ),
        start_fen="7k/8/K7/P7/3n4/8/8/8 b - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u4-03", unit=4, seq=3,
        title="Bishop vs knight — when the bishop is better",
        objective=(
            "No forced result here - this is a reading lesson, not a drill. In open "
            "positions with pawns on both wings, the bishop's long range usually beats "
            "the knight's short hops. In closed, blocked positions it's often the reverse. "
            "Play through a few of your own games from this phase and ask which type you had."
        ),
        start_fen=None,
        exercise_type="concept",
    ),
    dict(
        id="u4-04", unit=4, seq=4,
        title="The 'wrong' bishop — rook pawn fortress",
        objective=(
            "White to move, escorting an h-pawn with a light-squared bishop that cannot "
            "control the dark promotion square h8. If the defending king reaches the "
            "corner, this is an automatic draw no matter how far the pawn advances."
        ),
        start_fen="7k/8/6K1/7P/8/8/8/5B2 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),

    # --- Unit 5: Queen endings ---
    dict(
        id="u5-01", unit=5, seq=1,
        title="Queen vs. a central pawn on the brink of queening",
        objective=(
            "White to move. Black's e-pawn is one square from promoting and Black's king "
            "is clear across the board. A lone queen can still stop and win against any "
            "pawn except a rook or bishop pawn, purely through checks - see how the "
            "technique forces it in just a few moves."
        ),
        start_fen="7k/8/8/3Q4/8/8/4p3/K7 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u5-02", unit=5, seq=2,
        title="Queen vs. a rook pawn in the corner — the one real exception",
        objective=(
            "White to move. Same idea as the last lesson, except Black's pawn is a rook "
            "pawn and Black's king has reached the extreme corner in front of it. This is "
            "the one pawn type where that corner fortress holds even against a lone queen "
            "- play it out and see the stalemate tricks that make it a draw."
        ),
        start_fen="8/8/8/3Q2K1/8/8/p7/k7 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u5-03", unit=5, seq=3,
        title="Queen vs. rook — the long technique",
        objective=(
            "White to move, no pawns on the board. Queen beats rook with no pawns, but "
            "it's one of the longest and most error-prone 'easy' wins in chess - driving "
            "the rook away from its own king is the key idea. Don't expect to find it "
            "quickly the first time."
        ),
        start_fen="4k2r/8/8/8/8/3Q4/8/4K3 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),
    dict(
        id="u5-04", unit=5, seq=4,
        title="A scary check isn't always a bad position",
        objective=(
            "White to move and in check, up a pawn with an exposed king. Before assuming "
            "you're in trouble, calculate precisely - sometimes a 'dangerous-looking' "
            "check has a forcing refutation. Find it, then compare to the tablebase."
        ),
        start_fen="8/7k/8/8/8/q3K3/2P5/3Q4 w - - 0 1",
        exercise_type="tablebase_dtz",
    ),

    # --- Unit 6: Tactical motifs (deliberately not separately authored) ---
    dict(
        id="u6-01", unit=6, seq=1,
        title="Tactical motifs — routed through your weekly report",
        objective=(
            "Not a separate drill set here, on purpose. Now that root-cause tagging "
            "exists, `chess_trainer.cli report` already matches your top blunder tags "
            "to themed Lichess puzzle sets (lichess.org/training/<theme>) and links the "
            "top 1-2 each week under 'top actions'. Drill those instead of a generic "
            "tactics set - they're chosen from your actual mistakes, not a textbook."
        ),
        start_fen=None, exercise_type="concept",
    ),

    # --- Unit 7: Middlegame principles (review lessons - no tablebase for "good middlegame play") ---
    dict(
        id="u7-01", unit=7, seq=1,
        title="Piece activity — read your own middlegame blunders",
        objective=(
            "Review lesson, no forced position. Pull up the review app filtered to "
            "'middlegame' phase blunders and mistakes. For each one, ask: was a piece "
            "of mine doing nothing on that move? An inactive piece is often the real "
            "root cause behind a tactic landing, even when the auto-tagger calls it "
            "'hung_piece' or 'ignored_threat'."
        ),
        start_fen=None, exercise_type="concept",
    ),
    dict(
        id="u7-02", unit=7, seq=2,
        title="Weak squares and pawn structure",
        objective=(
            "Review lesson, no forced position. Look through a few of your own "
            "middlegame-phase games - not just the flagged blunders - and mark any "
            "permanent pawn-structure weaknesses you created: a hole in front of your "
            "king, a backward pawn, doubled pawns on a half-open file the opponent "
            "controls. These rarely show up as a single 'blunder' move, but they're "
            "often what made the later tactical blunder possible."
        ),
        start_fen=None, exercise_type="concept",
    ),
    dict(
        id="u7-03", unit=7, seq=3,
        title="King safety in the middlegame",
        objective=(
            "The question isn't only 'did I hang a piece' - it's 'did I leave my king "
            "somewhere that made ANY tactic dangerous.' That's a structural problem, "
            "not a one-move fix. Drill the positions where the auto-tagger caught this "
            "below - real moves from your own games, not a manual review."
        ),
        start_fen=None, exercise_type="concept",
    ),
    dict(
        id="u7-04", unit=7, seq=4,
        title="Open files and the 7th/2nd rank",
        objective=(
            "Review lesson. Look at your rook moves in middlegame-phase games you "
            "lost. Did an open or half-open file exist, and did you contest it? A rook "
            "that never reaches an open file is a piece playing at a material "
            "disadvantage even though it's still on the board."
        ),
        start_fen=None, exercise_type="concept",
    ),

    # --- Unit 8: Opening principles (review lessons, deliberately last) ---
    dict(
        id="u8-01", unit=8, seq=1,
        title="Development tempo — count your wasted opening moves",
        objective=(
            "A non-developing move in the opening - repositioning a piece you'd "
            "already moved, grabbing a flank pawn, reacting to a threat that didn't "
            "need reacting to - is a tempo your opponent gets for free. Drill the "
            "positions where the auto-tagger actually caught you doing this below, "
            "instead of manually combing through your own games for examples."
        ),
        start_fen=None, exercise_type="concept",
    ),
    dict(
        id="u8-02", unit=8, seq=2,
        title="Center control before flank play",
        objective=(
            "Review lesson. In your opening-phase blunders and mistakes, check how "
            "many happened in positions where you'd already committed to a flank pawn "
            "push (a4/h4-style) before either side had resolved the center. A "
            "contested, unresolved center usually means flank play is premature - the "
            "center can still open up and punish a king that moved there."
        ),
        start_fen=None, exercise_type="concept",
    ),
    dict(
        id="u8-03", unit=8, seq=3,
        title="King safety in the opening — castle before you attack",
        objective=(
            "The single most common 700-level opening mistake isn't a specific trap "
            "- it's playing for an attack or grabbing material before your own king "
            "has castled. Drill the opening-phase positions tagged 'king_safety' "
            "below - your own games, not a textbook."
        ),
        start_fen=None, exercise_type="concept",
    ),
    dict(
        id="u8-04", unit=8, seq=4,
        title="Common traps you've actually fallen for",
        objective=(
            "Review lesson. This app's own data is more useful here than a generic "
            "list of named traps (Scholar's mate, Legal's trap, the fried liver). "
            "Search your games for early blunders (ply <= 24, phase = opening) and see "
            "if the same trap-shape repeats. If one does, memorize the refutation to "
            "that specific trap - don't study traps in general."
        ),
        start_fen=None, exercise_type="concept",
    ),
]
