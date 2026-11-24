"""
Tactical annotations for a recognized position: absolute pins and attacked pieces (both colours),
the side to move (from a check or the last-move highlight), forced mates in 1 or 2 for the side to
move, and the opponent's mate threats.
"""
from __future__ import annotations

import time

import chess

VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 100}
_UNPLAYABLE = (chess.STATUS_NO_WHITE_KING | chess.STATUS_NO_BLACK_KING | chess.STATUS_TOO_MANY_KINGS
               | chess.STATUS_PAWNS_ON_BACKRANK | chess.STATUS_OPPOSITE_CHECK)


def playable(placement: chess.BaseBoard, turn: chess.Color) -> chess.Board | None:
    """A Board to search with `turn` to move, or None if that can't be a real position
    (missing/extra kings, pawns on the back rank, or the side not to move in check).
    Castling and en passant are unknown from a screenshot and left out."""
    board = chess.Board(None)
    board.set_piece_map(placement.piece_map())
    board.turn = turn
    board.castling_rights = chess.BB_EMPTY
    return None if board.status() & _UNPLAYABLE else board


def infer_side_to_move(placement: chess.BaseBoard, highlights: list[str] | tuple[str, ...]) -> tuple[chess.Color | None, str]:
    """(side to move or None, how it was decided)."""
    in_check = [c for c in (chess.WHITE, chess.BLACK)
                if placement.king(c) is not None and placement.is_attacked_by(not c, placement.king(c))]
    if len(in_check) == 1:  # only the side to move can be in check
        return in_check[0], "check"
    squares = [chess.parse_square(s) for s in highlights]
    if len(squares) == 2:
        occupied = [s for s in squares if placement.piece_at(s)]
        if len(occupied) == 1:  # the piece on the last move's destination belongs to the side that just moved
            turn = not placement.piece_at(occupied[0]).color
            if playable(placement, turn) is not None:
                return turn, "last-move highlight"
    return None, "unknown (no last-move highlight)"


def _mating_move(board: chess.Board) -> chess.Move | None:
    for move in board.legal_moves:
        if board.gives_check(move):
            board.push(move)
            mate = board.is_checkmate()
            board.pop()
            if mate:
                return move
    return None


def find_mates(board: chess.Board, budget: float = 0.5, max_results: int = 3) -> tuple[list[dict], bool]:
    """Forced mates in 1, or else in 2, for the side to move. Returns (mates, search finished).
    Each mate: {"side", "n", "from", "to", "line": [SAN moves of one example line]}."""
    side = "white" if board.turn else "black"

    def entry(n, move, line):
        return {"side": side, "n": n, "from": chess.square_name(move.from_square),
                "to": chess.square_name(move.to_square), "line": line}

    mates = []
    for move in board.legal_moves:
        if board.gives_check(move):
            san = board.san(move)
            board.push(move)
            if board.is_checkmate():
                mates.append(entry(1, move, [san]))
            board.pop()
    if mates:
        return mates[:max_results], True

    deadline = time.perf_counter() + budget
    first_moves = sorted(board.legal_moves, key=lambda m: (not board.gives_check(m), not board.is_capture(m)))
    for move in first_moves:
        if time.perf_counter() > deadline:
            return mates, False
        san = board.san(move)
        board.push(move)
        replies = list(board.legal_moves)
        forced, example = bool(replies), None  # no replies here = stalemate (mate was handled above)
        for reply in replies:
            board.push(reply)
            mate = _mating_move(board)
            if mate is None:
                board.pop()
                forced = False
                break
            if example is None:
                mate_san = board.san(mate)
                board.pop()
                example = [san, board.san(reply), mate_san]
            else:
                board.pop()
        board.pop()
        if forced:
            mates.append(entry(2, move, example))
            if len(mates) >= max_results:
                break
    return mates, True


def find_threats(placement: chess.BaseBoard, turn: chess.Color) -> list[dict]:
    """Mates in 1 the opponent would have if it were their move (only when `turn` isn't in check)."""
    opponent = playable(placement, not turn)
    if opponent is None:
        return []
    mates, _ = find_mates(opponent, budget=0.0)  # mates in 1 only
    return [m | {"kind": "mate"} for m in mates if m["n"] == 1]


def analyze_position(placement: chess.BaseBoard, turn: chess.Color | None, budget: float = 0.5) -> dict:
    """Pins/attacks plus mates and threats. With turn None, mates are searched for both sides."""
    result = analyze(placement)
    mates, complete = [], True
    for side in ([turn] if turn is not None else [chess.WHITE, chess.BLACK]):
        board = playable(placement, side)
        if board is not None:
            found, done = find_mates(board, budget)
            mates += found
            complete = complete and done
    result.update(mates=mates, mates_complete=complete,
                  threats=find_threats(placement, turn) if turn is not None else [])
    return result


def _pinner(board: chess.BaseBoard, king: int, pinned: int) -> int | None:
    """First piece beyond `pinned`, walking away from the king along their shared line."""
    df = (chess.square_file(pinned) > chess.square_file(king)) - (chess.square_file(pinned) < chess.square_file(king))
    dr = (chess.square_rank(pinned) > chess.square_rank(king)) - (chess.square_rank(pinned) < chess.square_rank(king))
    f, r = chess.square_file(pinned) + df, chess.square_rank(pinned) + dr
    while 0 <= f < 8 and 0 <= r < 8:
        sq = chess.square(f, r)
        if board.piece_at(sq):
            return sq
        f, r = f + df, r + dr
    return None


def analyze(board: chess.BaseBoard) -> dict:
    """Pins and attacked pieces for both colours.

    pins:     [{"pinned", "pinner", "king", "color"}]  (square names; color = colour of the pinned piece)
    attacked: [{"square", "piece", "attackers", "defenders", "level", "reason"}]
              level "danger"  = check, undefended, or attacked by a cheaper piece
              level "defended" = attacked but defended by at least as much
    """
    pins, attacked = [], []
    for sq, piece in board.piece_map().items():
        color, enemy = piece.color, not piece.color
        king = board.king(color)
        if piece.piece_type != chess.KING and king is not None and board.is_pinned(color, sq):
            pinner = _pinner(board, king, sq)
            if pinner is not None:
                pins.append({"pinned": chess.square_name(sq), "pinner": chess.square_name(pinner),
                             "king": chess.square_name(king), "color": "white" if color else "black"})

        attackers = board.attackers(enemy, sq)
        if not attackers:
            continue
        defenders = board.attackers(color, sq)
        cheapest = board.piece_type_at(min(attackers, key=lambda a: VALUES[board.piece_type_at(a)]))
        if piece.piece_type == chess.KING:
            level, reason = "danger", "check"
        elif not defenders:
            level, reason = "danger", "undefended"
        elif VALUES[cheapest] < VALUES[piece.piece_type]:
            level, reason = "danger", f"attacked by a {chess.piece_name(cheapest)}"
        else:
            level, reason = "defended", "attacked but defended"
        attacked.append({"square": chess.square_name(sq), "piece": piece.symbol(),
                         "attackers": [chess.square_name(a) for a in attackers],
                         "defenders": len(defenders), "level": level, "reason": reason})
    return {"pins": pins, "attacked": attacked}


# ---------------------------------------------------------------- move tracking

def _search_boards(placement: chess.BaseBoard, turn: chess.Color) -> list[chess.Board]:
    """Boards with `turn` to move, allowing every castling right the piece placement permits and
    every en passant square an opponent pawn could have just created (both unknown otherwise)."""
    board = chess.Board(None)
    board.set_piece_map(placement.piece_map())
    board.turn = turn
    king = board.king(not turn)
    if king is not None and board.is_attacked_by(turn, king):
        return []  # the side not to move can't be in check
    board.set_castling_fen("KQkq")
    board.castling_rights = board.clean_castling_rights()
    boards = [board]
    rank, behind, start = (4, 5, 6) if turn == chess.WHITE else (3, 2, 1)
    for f in range(8):
        pawn = board.piece_at(chess.square(f, rank))
        if (pawn and pawn.piece_type == chess.PAWN and pawn.color != turn
                and not board.piece_at(chess.square(f, behind)) and not board.piece_at(chess.square(f, start))):
            ep = board.copy(stack=False)
            ep.ep_square = chess.square(f, behind)
            boards.append(ep)
    return boards


def find_move(before: chess.BaseBoard, after: chess.BaseBoard) -> tuple[chess.Color, str] | None:
    """How `before` became `after`: returns (side to move in `after`, description) or None.
    Tries one move forward, one move back (stepping back through a game), then two moves forward."""
    target, origin = after.board_fen(), before.board_fen()
    for color in (chess.WHITE, chess.BLACK):
        for board in _search_boards(before, color):
            for move in board.legal_moves:
                san = board.san(move)
                board.push(move)
                hit = board.board_fen() == target
                board.pop()
                if hit:
                    return (not color), f"saw {'' if color else '…'}{san}"
    for color in (chess.WHITE, chess.BLACK):
        for board in _search_boards(after, color):
            for move in board.legal_moves:
                board.push(move)
                hit = board.board_fen() == origin
                board.pop()
                if hit:
                    return color, "saw a move taken back"
    for color in (chess.WHITE, chess.BLACK):
        for board in _search_boards(before, color):
            for first in list(board.legal_moves):
                board.push(first)
                for second in board.legal_moves:
                    board.push(second)
                    hit = board.board_fen() == target
                    board.pop()
                    if hit:
                        board.pop()
                        return color, "saw two moves"
                board.pop()
    return None


# ---------------------------------------------------------------- move safety (hover)

def _value_moved(board: chess.Board, move: chess.Move) -> int:
    return VALUES[move.promotion] if move.promotion else VALUES[board.piece_type_at(move.from_square)]


def _captured_value(board: chess.Board, move: chess.Move) -> int:
    if board.is_en_passant(move):
        return VALUES[chess.PAWN]
    captured = board.piece_type_at(move.to_square)
    gain = VALUES[captured] if captured else 0
    return gain + (VALUES[move.promotion] - VALUES[chess.PAWN] if move.promotion else 0)


def see(board: chess.Board, move: chess.Move) -> int:
    """Static exchange evaluation: material the side to move nets from `move` plus the best sequence
    of captures and recaptures on its destination (each side may stop when continuing loses)."""
    gains = [_captured_value(board, move)]
    on_square = _value_moved(board, move)
    b = board.copy(stack=False)
    b.push(move)
    target = chess.BB_SQUARES[move.to_square]
    while True:
        captures = list(b.generate_legal_captures(to_mask=target))
        if not captures:
            break
        capture = min(captures, key=lambda m: _value_moved(b, m))
        gains.append(on_square - gains[-1])
        on_square = _value_moved(b, capture)
        b.push(capture)
    for i in range(len(gains) - 1, 0, -1):
        gains[i - 1] = -max(-gains[i - 1], gains[i])
    return gains[0]


def best_capture(board: chess.Board) -> int:
    """Most material the side to move can win with a capture (by exchange evaluation), 0 if none."""
    return max([0] + [see(board, m) for m in board.generate_legal_captures()])


def _best_capture_excluding(board: chess.Board, excluded: int) -> int:
    """Most material the side to move can win with a capture whose target isn't `excluded`."""
    return max([0] + [see(board, m) for m in board.generate_legal_captures() if m.to_square != excluded])


def move_safety(placement: chess.BaseBoard, square: str) -> dict:
    """Rate every legal move of the piece on `square` (as if its side were to move).

    exchange = material the move nets on its destination square, after the best sequence of captures
               and recaptures there (negative: the piece gets lost or traded down)
    exposed  = how much more the opponent could then win elsewhere than it can now (pieces the move
               leaves undefended or uncovers)
    delta    = exchange - exposed
    kind     = "mate", "wins" (delta >= 1), "blunder" (delta <= -1), "trade" (pieces get exchanged
               without net loss) or "safe"
    """
    sq = chess.parse_square(square)
    piece = placement.piece_at(sq)
    result = {"square": square, "piece": piece.symbol() if piece else None, "moves": []}
    if piece is None:
        return result
    board = playable(placement, piece.color)
    if board is None:
        result["note"] = "not a legal position with this side to move"
        return result
    pass_board = playable(placement, not piece.color)  # None if this side is in check
    hanging_now = _best_capture_excluding(pass_board, sq) if pass_board is not None else 0

    for move in board.legal_moves:
        if move.from_square != sq or (move.promotion and move.promotion != chess.QUEEN):
            continue
        san = board.san(move)
        exchange = see(board, move)
        won = _captured_value(board, move)
        board.push(move)
        if board.is_checkmate():
            kind, delta = "mate", 0
        else:
            exposed = max(0, _best_capture_excluding(board, move.to_square) - hanging_now)
            delta = exchange - exposed
            # a trade = pieces really change hands without net loss: we capture and they can take back
            # without losing on it, or they can take our piece in an even exchange
            retakes = [see(board, m) for m in board.generate_legal_captures(to_mask=chess.BB_SQUARES[move.to_square])]
            best_retake = max(retakes) if retakes else None
            traded = best_retake is not None and (best_retake >= 0 if won > 0 else best_retake == 0)
            kind = "wins" if delta >= 1 else "blunder" if delta <= -1 else "trade" if traded else "safe"
        board.pop()
        result["moves"].append({"to": chess.square_name(move.to_square), "san": san, "kind": kind, "delta": delta})
    return result
