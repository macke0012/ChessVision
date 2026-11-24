"""Render lichess-style board 'screenshots' of random positions locally (no site access).

Usage: render_lichess.py OUT_DIR COUNT [SEED]
Writes OUT_DIR/lichess/NNN.png and OUT_DIR/labels_lichess.csv (file,fen,flip).
Pieces: python-chess's built-in SVG set (cburnett, lichess's default). Board colours
sampled from a real lichess screenshot, with small per-image jitter.
"""
import csv
import random
import sys
from pathlib import Path

import chess
import chess.svg
import cv2
import numpy as np
import pymupdf

out, count = Path(sys.argv[1]), int(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 2
rng = random.Random(seed)

LIGHT, DARK = (0xEC, 0xDA, 0xB9), (0xAE, 0x8A, 0x69)  # sampled from a real lichess screenshot
PAGE_BG, PANEL = (0x16, 0x15, 0x12), (0x26, 0x24, 0x22)


def random_position():
    """Random game of varied length; returns (board_before_last_move, board)."""
    while True:
        b = chess.Board()
        for _ in range(rng.choice([rng.randint(0, 20), rng.randint(10, 60), rng.randint(40, 140)])):
            moves = list(b.legal_moves)
            if not moves:
                break
            captures = [m for m in moves if b.is_capture(m)]
            b.push(rng.choice(captures) if captures and rng.random() < 0.3 else rng.choice(moves))
        if not b.is_game_over() or rng.random() < 0.3:
            return b


def hexcol(rgb):
    return "#%02x%02x%02x" % tuple(int(np.clip(c, 0, 255)) for c in rgb)


def jitter(rgb, amount=5):
    return tuple(c + rng.randint(-amount, amount) for c in rgb)


def render_board(board: chess.Board, size: int, flip: bool) -> np.ndarray:
    light, dark = jitter(LIGHT), jitter(DARK)
    colors = {"square light": hexcol(light), "square dark": hexcol(dark),
              "square light lastmove": "#cdd26a", "square dark lastmove": "#aaa23b"}
    lastmove = board.peek() if board.move_stack and rng.random() < 0.4 else None
    check = board.king(board.turn) if board.is_check() else None
    svg = chess.svg.board(board, size=size, coordinates=False, colors=colors, lastmove=lastmove,
                          check=check, orientation=chess.BLACK if flip else chess.WHITE)
    pix = pymupdf.open(stream=svg.encode(), filetype="svg")[0].get_pixmap()
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3][:, :, ::-1].copy()
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    # lichess draws coordinates inside the edge squares: ranks top-right on the right file,
    # files bottom-left on the bottom rank, in the opposite square colour
    s = size / 8
    font, scale, thick = cv2.FONT_HERSHEY_SIMPLEX, s / 110, max(1, int(s / 50))
    files, ranks = "abcdefgh", "12345678"
    for i in range(8):
        rank_label = ranks[i] if flip else ranks[7 - i]
        on_light = (i + 7) % 2 == 0
        col = (dark if on_light else light)[::-1]
        cv2.putText(img, rank_label, (int(size - s * 0.22), int(i * s + s * 0.2)), font, scale, col, thick, cv2.LINE_AA)
        file_label = files[7 - i] if flip else files[i]
        on_light = (i + 7) % 2 == 0
        col = (dark if on_light else light)[::-1]
        cv2.putText(img, file_label, (int(i * s + s * 0.08), int(size - s * 0.08)), font, scale, col, thick, cv2.LINE_AA)
    return img


(out / "lichess").mkdir(parents=True, exist_ok=True)
seen = set()
with (out / "labels_lichess.csv").open("w", newline="") as fh:
    w = csv.writer(fh)
    w.writerow(["file", "fen", "flip"])
    done = 0
    while done < count:
        board = random_position()
        if board.board_fen() in seen:
            continue
        seen.add(board.board_fen())
        flip = rng.random() < 0.25
        size = rng.choice([640, 760, 880, 1008, 1008, 1008, 1120])
        tile = render_board(board, size, flip)
        # page frame like the lichess analysis page: dark background, side panel, header bar
        W = size + rng.randint(60, 900)
        H = size + rng.randint(160, 500)
        page = np.full((H, W, 3), PAGE_BG[::-1], np.uint8)
        page[: rng.randint(90, 120)] = (0x1f, 0x24, 0x27)
        x0, y0 = rng.randint(16, 30), rng.randint(120, 140)
        if W - (x0 + size) > 200:
            cv2.rectangle(page, (x0 + size + 30, y0), (W - 10, y0 + size), PANEL[::-1], -1)
        page[y0:y0 + size, x0:x0 + size] = tile
        fname = f"lichess/{done:03d}.png"
        cv2.imwrite(str(out / fname), page)
        w.writerow([fname, board.fen(), "true" if flip else "false"])
        done += 1
print(f"rendered {done}")
