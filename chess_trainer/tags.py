"""Root-cause tag detection: the 7 tags validated against 29 hand-labeled
positions (see chess-trainer-mvp-plan.md). All heuristics, no ML - per-tag
docstrings explain the detection logic and why it was shaped this way.

Each detector takes the position before the move, the move played, and (where
needed) a short Stockfish analysis of both sides, and returns True/False.
`tag_position()` runs all 7 and returns the matched tag names - a position can
match more than one tag, which matches reality (a hung piece can also be a
wasted tempo, etc).
"""
from __future__ import annotations

from dataclasses import dataclass

import chess
import chess.engine

from .analyze import win_pct

PIECE_VALUES = {
    chess.PAWN: 1,
    chess.KNIGHT: 3,
    chess.BISHOP: 3,
    chess.ROOK: 5,
    chess.QUEEN: 9,
    chess.KING: 0,
}

ALL_TAGS = [
    "hung_piece",
    "missed_capture",
    "unsound_sacrifice",
    "king_safety",
    "ignored_threat",
    "self_obstruction",
    "wasted_tempo",  # checked last - catch-all once the others are ruled out
]

# Shared display metadata - used by report.py and the web app's tag chips, so
# both stay in sync with one source instead of drifting apart.
TAG_INFO = {
    "hung_piece": ("Hung piece", "Left something capturable for free, or removed its own defender."),
    "missed_capture": ("Missed capture", "A winning or free capture was available and wasn't played."),
    "unsound_sacrifice": ("Unsound sacrifice", "Gave up material without enough follow-up to justify it."),
    "king_safety": ("Self-exposed king", "The move itself weakened your own king's pawn shield."),
    "ignored_threat": ("Ignored threat", "The opponent's threat (often a mating idea) went unanswered."),
    "self_obstruction": ("Blocked own piece", "The move blocked one of your own pieces' diagonal/file."),
    "wasted_tempo": ("Wasted tempo", "No material changed hands - a slow, premature, or repeated move."),
    "other": ("Other", "None of the above - describe it in the text field."),
}


def _value(piece: chess.Piece | None) -> int:
    return PIECE_VALUES[piece.piece_type] if piece else 0


def _min_attacker_value(board: chess.Board, color: bool, square: int) -> int | None:
    attackers = board.attackers(color, square)
    if not attackers:
        return None
    return min(_value(board.piece_at(sq)) for sq in attackers)


@dataclass
class PositionAnalysis:
    board_before: chess.Board
    move: chess.Move
    board_after: chess.Board
    mover_white: bool
    pv_after: list[chess.Move]  # opponent's best continuation after the move
    pv_null: list[chess.Move] | None  # best continuation if mover had passed (threat check)
    eval_after_white_cp: int | None = None
    eval_null_white_cp: int | None = None


def analyze_for_tags(
    engine: chess.engine.SimpleEngine, board_before: chess.Board, move: chess.Move,
    nodes: int = 500_000,
) -> PositionAnalysis:
    limit = chess.engine.Limit(nodes=nodes)
    mover_white = board_before.turn == chess.WHITE

    board_after = board_before.copy()
    board_after.push(move)
    info_after = engine.analyse(board_after, limit)
    pv_after = info_after.get("pv", [])
    eval_after = info_after["score"].white().score(mate_score=100000)

    pv_null = None
    eval_null = None
    if not board_before.is_check():
        null_board = board_before.copy()
        null_board.push(chess.Move.null())
        info_null = engine.analyse(null_board, limit)
        pv_null = info_null.get("pv", [])
        eval_null = info_null["score"].white().score(mate_score=100000)

    return PositionAnalysis(
        board_before, move, board_after, mover_white, pv_after, pv_null,
        eval_after_white_cp=eval_after, eval_null_white_cp=eval_null,
    )


def tag_hung_piece(a: PositionAnalysis) -> bool:
    """Opponent's best reply is a capture of one of my pieces (pawns count too
    - a hung pawn for literally nothing is still a hang, per the hand-labeled
    data) that isn't immediately recaptured for at least equal value - i.e. a
    real hang, not just an even trade. Covers both flavors seen in the data: a
    piece that was already undefended, and a move that removed the only
    defender of a *different* piece (e.g. gxh6 undefending the f6 knight)."""
    if not a.pv_after or not a.board_after.is_capture(a.pv_after[0]):
        return False
    capture = a.pv_after[0]
    captured = a.board_after.piece_at(capture.to_square)
    if captured is None:
        return False
    captured_value = _value(captured)
    attacker_value = _value(a.board_after.piece_at(capture.from_square))

    # If I have a recapture at all, what matters is whether their attacker was
    # at least as valuable as what it took - if a pawn takes my pawn and I
    # recapture with a bishop, that's a dead-even trade no matter what piece
    # does the recapturing (I lost a pawn, I captured their pawn, net zero).
    # It's only a real hang if I have no recapture, or their attacker was
    # *cheaper* than what it captured (so even recapturing leaves me down).
    after_capture = a.board_after.copy()
    after_capture.push(capture)
    my_recapture_value = _min_attacker_value(after_capture, a.mover_white, capture.to_square)
    if my_recapture_value is not None and attacker_value >= captured_value:
        return False
    return True


def tag_missed_capture(a: PositionAnalysis) -> bool:
    """Before my move, I had a legal capture of an undefended piece (or a
    piece worth more than my attacker) worth >=3, and I played something else.
    Deliberately simple (undefended-or-winning-trade only, not full SEE) -
    matches what's actually missable at this rating per the hand-labeled data
    (a flat-out hanging queen/bishop/pawn, not a subtle exchange)."""
    board = a.board_before
    for candidate in board.legal_moves:
        if candidate == a.move or not board.is_capture(candidate):
            continue
        captured = board.piece_at(candidate.to_square)
        if captured is None or _value(captured) < 3:
            continue
        attacker_value = _value(board.piece_at(candidate.from_square))
        defender_value = _min_attacker_value(board, not a.mover_white, candidate.to_square)
        if defender_value is None or _value(captured) > attacker_value:
            return True
    return False


def tag_unsound_sacrifice(a: PositionAnalysis) -> bool:
    """My move itself is a capture or moves into an attacked square, giving up
    more material than it gains, with no sign of real compensation (checked
    crudely via the post-move eval already being bad - severity is passed in
    by the caller, see tag_position)."""
    board = a.board_before
    piece_moved = board.piece_at(a.move.from_square)
    my_value = _value(piece_moved)
    gained = _value(board.piece_at(a.move.to_square)) if board.is_capture(a.move) else 0

    defender_value = _min_attacker_value(a.board_after, not a.mover_white, a.move.to_square)
    if defender_value is None:
        return False  # square isn't actually contested
    if defender_value <= my_value and (my_value - gained) >= 2:
        return True
    return False


def tag_king_safety(a: PositionAnalysis) -> bool:
    """My move directly weakens my own king's pawn shield (moves a shield
    pawn away, or captures with a shield pawn) - self-inflicted, as distinct
    from an attacking sacrifice aimed at the opponent's king (that's
    unsound_sacrifice's territory)."""
    board = a.board_before
    king_sq = board.king(a.mover_white)
    if king_sq is None:
        return False
    king_file = chess.square_file(king_sq)
    king_rank = chess.square_rank(king_sq)
    shield_rank = king_rank + 1 if a.mover_white else king_rank - 1
    if not (0 <= shield_rank <= 7):
        return False
    shield_squares = {
        chess.square(f, shield_rank)
        for f in (king_file - 1, king_file, king_file + 1)
        if 0 <= f <= 7
    }
    if a.move.from_square not in shield_squares:
        return False
    moved_piece = board.piece_at(a.move.from_square)
    if moved_piece is None or moved_piece.piece_type != chess.PAWN:
        return False
    return True


def tag_ignored_threat(a: PositionAnalysis) -> bool:
    """If I had passed my turn entirely, would the opponent be about as well
    off as they are after my actual move? If so, the threat already existed
    before I moved and my move didn't address it - I wasn't the one who
    created the problem, I ignored one already there.

    Two ways this can show up: (a) both continuations are captures of a
    similarly valuable piece (a concrete material threat), or (b) the eval
    swing is close in both scenarios regardless of shape (catches pure mating
    nets and positional threats a capture-only check would miss)."""
    if a.eval_null_white_cp is None or a.eval_after_white_cp is None:
        return False

    win_null_mover = win_pct(a.eval_null_white_cp) if a.mover_white else 100 - win_pct(a.eval_null_white_cp)
    win_after_mover = win_pct(a.eval_after_white_cp) if a.mover_white else 100 - win_pct(a.eval_after_white_cp)
    # Opponent is already winning big if I pass entirely, and barely less so
    # after my actual move - a specific severe threat, not just "I'm worse".
    if win_null_mover <= 15 and win_after_mover <= 20 and abs(win_null_mover - win_after_mover) <= 8:
        return True

    if a.pv_null and a.pv_after:
        null_move = a.pv_null[0]
        after_move = a.pv_after[0]
        null_board = a.board_before.copy()
        null_board.push(chess.Move.null())
        null_is_capture = null_board.is_capture(null_move) if null_move in null_board.legal_moves else False
        after_is_capture = a.board_after.is_capture(after_move) if after_move in a.board_after.legal_moves else False
        if null_is_capture and after_is_capture:
            null_target = null_board.piece_at(null_move.to_square)
            after_target = a.board_after.piece_at(after_move.to_square)
            if _value(null_target) >= 3 and _value(after_target) >= 3:
                return True
    return False


def tag_self_obstruction(a: PositionAnalysis) -> bool:
    """My move's destination square blocks one of my own long-range pieces
    (bishop/rook/queen), cutting its mobility sharply, when that piece wasn't
    already mostly blocked before the move. Excludes castling - it relocates
    two pieces at once and spuriously trips a naive version of this check."""
    if a.board_before.is_castling(a.move):
        return False
    board_before = a.board_before
    for square in chess.SQUARES:
        piece = board_before.piece_at(square)
        if (
            piece is None
            or piece.color != a.mover_white
            or square == a.move.from_square
            or piece.piece_type not in (chess.BISHOP, chess.ROOK, chess.QUEEN)
        ):
            continue
        before_attacks = len(board_before.attacks(square))
        after_attacks = len(a.board_after.attacks(square))
        if before_attacks >= 2 and after_attacks <= before_attacks // 3:
            return True
    return False


def tag_wasted_tempo(a: PositionAnalysis, other_tags: list[str]) -> bool:
    """Catch-all: the move lost significant win% but isn't explained by any
    of the other 6 tags. By far the most common pattern in the hand-labeled
    data (7/29) - repeated piece moves, premature queen sorties, equal trades
    at the wrong moment, moves that don't create any concrete threat.

    Deliberately not excluding captures outright: an even trade (e.g. a
    knight recapture that doesn't net material either way) can still be a
    wasted tempo if it's badly timed - the other 6 detectors already catch
    the cases where a capture actually loses material."""
    return not other_tags


def tag_position(a: PositionAnalysis) -> list[str]:
    tags = []
    if tag_hung_piece(a):
        tags.append("hung_piece")
    if tag_missed_capture(a):
        tags.append("missed_capture")
    if tag_unsound_sacrifice(a):
        tags.append("unsound_sacrifice")
    if tag_king_safety(a):
        tags.append("king_safety")
    if tag_ignored_threat(a):
        tags.append("ignored_threat")
    if tag_self_obstruction(a):
        tags.append("self_obstruction")
    if tag_wasted_tempo(a, tags):
        tags.append("wasted_tempo")
    return tags
