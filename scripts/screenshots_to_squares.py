"""
Turn FEN-labelled chessboard screenshots into labelled square images for training
the square classifier.

The board is located from its 8x8 grid of light/dark squares, so this works on
flat digital boards (lichess, chess.com, ...) without the board-extraction model.
Each board is cut into 64 squares with the same code the classifier uses at
inference time, and every square is saved as a 64x64 greyscale JPG in the class
folder matching data/squares/training (R, N, ..., _r, _n, ..., f).

Labels CSV (paths are relative to the CSV file):

    file,fen,flip
    lichess/start.png,rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1,false
    chesscom/italian_black.png,r1bqk2r/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R,true

    fen   full FEN or just the piece-placement field
    flip  true if the board is shown from Black's side (optional, default false)

Usage:

    python scripts/screenshots_to_squares.py labels.csv -o data/new_squares --debug-dir data/new_squares_debug

Keep the screenshots you test with out of the labels file, so the test stays honest.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from pathlib import Path

import chess
import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chessvision.digital_board import BoardNotFound, board_to_squares, find_board  # noqa: E402

# Same label -> folder mapping as computeroot/cv_endpoint.py (folders must differ on
# case-insensitive file systems, hence the underscore for black pieces).
PIECE2DIR = {"R": "R", "r": "_r", "K": "K", "k": "_k", "Q": "Q", "q": "_q",
             "N": "N", "n": "_n", "P": "P", "p": "_p", "B": "B", "b": "_b", "f": "f"}

def parse_flip(value: str | None) -> bool:
    value = (value or "").strip().lower()
    if value in ("", "0", "false", "no", "n"):
        return False
    if value in ("1", "true", "yes", "y"):
        return True
    raise ValueError(f"flip must be true/false, got {value!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("labels", type=Path, help="CSV with columns file,fen[,flip]")
    parser.add_argument("-o", "--out-dir", type=Path, default=Path("data/new_squares"),
                        help="where to write <class>/<image>_<square>.JPG (default: data/new_squares)")
    parser.add_argument("--debug-dir", type=Path,
                        help="also write each detected grid and 512x512 board here, for checking")
    args = parser.parse_args()

    with args.labels.open(newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows or not {"file", "fen"} <= set(rows[0]):
        print(f"{args.labels}: needs a header with at least 'file' and 'fen' columns", file=sys.stderr)
        return 2

    counts, failed = Counter(), []
    for line_no, row in enumerate(rows, start=2):
        try:
            path = (args.labels.parent / row["file"].strip()).resolve()
            position = chess.BaseBoard(row["fen"].split()[0])
            flip = parse_flip(row.get("flip"))
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                raise FileNotFoundError(f"cannot read image {path}")
            bbox = find_board(img)
        except (ValueError, IndexError, AttributeError, FileNotFoundError, BoardNotFound) as e:
            print(f"line {line_no} ({row['file']}): SKIPPED - {e}", file=sys.stderr)
            failed.append(row["file"])
            continue

        board, squares, names = board_to_squares(img, bbox, flip)
        # name after the path, not just the file name, so lichess/000.png and chesscom/000.png don't collide
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(row["file"].strip()).with_suffix("").as_posix()).strip("_")
        for square, name in zip(squares, names):
            piece = position.piece_at(chess.parse_square(name))
            label = piece.symbol() if piece else "f"
            out = args.out_dir / PIECE2DIR[label] / f"{stem}_{name}.JPG"
            out.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out), square)
            counts[label] += 1

        if args.debug_dir:
            args.debug_dir.mkdir(parents=True, exist_ok=True)
            x0, y0, x1, y1 = bbox
            grid = img.copy()
            for k in range(9):
                x = int(round(x0 + k * (x1 - x0) / 8))
                y = int(round(y0 + k * (y1 - y0) / 8))
                cv2.line(grid, (x, y0), (x, y1), (0, 0, 255), 3)
                cv2.line(grid, (x0, y), (x1, y), (0, 0, 255), 3)
            cv2.imwrite(str(args.debug_dir / f"{stem}__grid.png"), grid)
            cv2.imwrite(str(args.debug_dir / f"{stem}__board.png"), board)

        print(f"{row['file']}: board at x={bbox[0]}..{bbox[2]} y={bbox[1]}..{bbox[3]}, 64 squares written")

    order = ["K", "Q", "R", "B", "N", "P", "k", "q", "r", "b", "n", "p", "f"]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} screenshots -> {sum(counts.values())} squares in {args.out_dir}")
    print("  " + "  ".join(f"{PIECE2DIR[k]}:{counts[k]}" for k in order))
    if failed:
        print(f"Skipped {len(failed)}: {', '.join(failed)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
