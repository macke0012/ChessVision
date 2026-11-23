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
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chessvision.cv_globals import BOARD_SIZE  # noqa: E402
from chessvision.data_processing.extract_squares import extract_squares  # noqa: E402

# Same label -> folder mapping as computeroot/cv_endpoint.py (folders must differ on
# case-insensitive file systems, hence the underscore for black pieces).
PIECE2DIR = {"R": "R", "r": "_r", "K": "K", "k": "_k", "Q": "Q", "q": "_q",
             "N": "N", "n": "_n", "P": "P", "p": "_p", "B": "B", "b": "_b", "f": "f"}

EDGE_THRESHOLD = 25  # min colour step between neighbouring pixels that counts as an edge


class BoardNotFound(Exception):
    pass


def _strong_edges(img: np.ndarray, axis: int) -> np.ndarray:
    """Boolean map of large colour steps between neighbouring pixels along `axis`."""
    return np.abs(np.diff(img.astype(np.int16), axis=axis)).max(axis=2) > EDGE_THRESHOLD


def _line_centers(profile: np.ndarray, min_count: float) -> np.ndarray:
    """Centres of runs of positions whose edge count reaches min_count."""
    idx = np.flatnonzero(profile >= min_count)
    if idx.size == 0:
        return np.empty(0)
    centers = []
    for group in np.split(idx, np.flatnonzero(np.diff(idx) > 2) + 1):
        weights = profile[group].astype(float)
        centers.append(float((group * weights).sum() / weights.sum()))
    return np.array(centers)


def _grids(centers: np.ndarray, min_spacing: float) -> list[tuple[float, float]]:
    """All (first, last) line pairs that have 7 equally spaced lines between them, largest first."""
    found = []
    n = len(centers)
    for i in range(n):
        for j in range(n - 1, i, -1):
            spacing = (centers[j] - centers[i]) / 8
            if spacing < min_spacing:
                break
            expected = centers[i] + spacing * np.arange(1, 8)
            pos = np.clip(np.searchsorted(centers, expected), 1, n - 1)
            dist = np.minimum(np.abs(centers[pos] - expected), np.abs(centers[pos - 1] - expected))
            if (dist <= max(2.0, 0.04 * spacing)).all():
                found.append((centers[i], centers[j]))
                break
    return sorted(found, key=lambda g: g[0] - g[1])


def _check_checkerboard(board: np.ndarray) -> None:
    """Raise BoardNotFound unless the crop's squares alternate light/dark like a chessboard."""
    h, w = board.shape[:2]
    s = min(h, w) / 8
    inset, size = max(1, int(0.06 * s)), max(2, int(0.08 * s))
    colors = np.zeros((8, 8, 3))
    for r in range(8):
        for c in range(8):
            y0, x0, y1, x1 = int(r * s), int(c * s), int((r + 1) * s), int((c + 1) * s)
            corners = [board[y0 + inset:y0 + inset + size, x0 + inset:x0 + inset + size],
                       board[y0 + inset:y0 + inset + size, x1 - inset - size:x1 - inset],
                       board[y1 - inset - size:y1 - inset, x0 + inset:x0 + inset + size],
                       board[y1 - inset - size:y1 - inset, x1 - inset - size:x1 - inset]]
            # median over all corner pixels: robust to a coordinate label in one corner
            colors[r, c] = np.median(np.concatenate([p.reshape(-1, 3) for p in corners]), axis=0)
    parity = (np.add.outer(np.arange(8), np.arange(8)) % 2).astype(bool)
    light, dark = colors[~parity].mean(axis=0), colors[parity].mean(axis=0)
    if np.linalg.norm(light - dark) < 20:
        raise BoardNotFound("detected grid has no light/dark square pattern")
    if light.mean() <= dark.mean():
        raise BoardNotFound("top-left square of detected grid is not a light square")
    to_light = np.linalg.norm(colors - light, axis=2)
    to_dark = np.linalg.norm(colors - dark, axis=2)
    consistent = int(np.where(parity, to_dark < to_light, to_light < to_dark).sum())
    if consistent < 56:
        raise BoardNotFound(f"only {consistent}/64 squares match a checkerboard pattern")


def find_board(img: np.ndarray) -> tuple[int, int, int, int]:
    """Locate an axis-aligned digital chessboard. Returns (x0, y0, x1, y1), end-exclusive."""
    h, w = img.shape[:2]
    ex, ey = _strong_edges(img, axis=1), _strong_edges(img, axis=0)
    min_spacing = 0.15 * min(h, w) / 8
    reasons = []

    for x_first, x_last in _grids(_line_centers(ex.sum(axis=0), 0.2 * h), min_spacing)[:5]:
        xa, xb = int(round(x_first)), int(round(x_last))
        ys = _grids(_line_centers(ey[:, xa:xb].sum(axis=1), 0.5 * (xb - xa)), min_spacing)
        ys = [g for g in ys if abs((g[1] - g[0]) - (xb - xa)) <= 0.03 * (xb - xa)]
        if not ys:
            reasons.append("no matching horizontal grid lines")
            continue
        ya, yb = int(round(ys[0][0])), int(round(ys[0][1]))
        # refine the vertical lines using only rows inside the board
        xs = _grids(_line_centers(ex[ya:yb].sum(axis=0), 0.5 * (yb - ya)), min_spacing)
        xs = [g for g in xs if abs((g[1] - g[0]) - (yb - ya)) <= 0.03 * (yb - ya)]
        if xs:
            xa, xb = int(round(xs[0][0])), int(round(xs[0][1]))
        # an edge at index i lies between pixels i and i+1
        x0, y0, x1, y1 = xa + 1, ya + 1, xb + 1, yb + 1
        try:
            _check_checkerboard(img[y0:y1, x0:x1])
        except BoardNotFound as e:
            reasons.append(str(e))
            continue
        return x0, y0, x1, y1

    raise BoardNotFound("; ".join(reasons) or "no 8x8 grid found")


def board_to_squares(img: np.ndarray, bbox: tuple[int, int, int, int], flip: bool):
    """Crop, resize and split the board exactly like the inference pipeline expects."""
    x0, y0, x1, y1 = bbox
    board = cv2.resize(img[y0:y1, x0:x1], BOARD_SIZE, interpolation=cv2.INTER_AREA)
    board = cv2.cvtColor(board, cv2.COLOR_BGR2GRAY)
    squares, names = extract_squares(board, flip=flip)
    return board, squares, names


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
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(row["file"]).stem)
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
