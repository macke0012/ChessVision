"""
Locate flat digital chessboards (lichess, chess.com, ...) in screenshots.

The board is found from its 8x8 grid of light/dark squares, so no board-extraction
model is needed. Works for axis-aligned boards only (screenshots, not photos).
"""
from __future__ import annotations

import cv2
import numpy as np

from .cv_globals import BOARD_SIZE
from .data_processing.extract_squares import extract_squares

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


PARITY = (np.add.outer(np.arange(8), np.arange(8)) % 2).astype(bool)  # True = dark square (top-left is light)
HIGHLIGHT_THRESHOLD = 30.0  # colour distance from the usual square colour that counts as highlighted


def _square_colors(board: np.ndarray) -> np.ndarray:
    """Background colour of each square (8x8x3), sampled in the corners where pieces rarely reach."""
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
    return colors


def highlighted_squares(board: np.ndarray) -> list[tuple[int, int]]:
    """(row, col) of squares whose background differs from the usual light/dark colour, e.g. the
    last-move highlight that lichess and chess.com draw. Row 0 is the top of the image."""
    colors = _square_colors(board)
    found = []
    for dark in (False, True):
        base = np.median(colors[PARITY == dark], axis=0)
        distance = np.linalg.norm(colors - base, axis=2)
        found += [(r, c) for r in range(8) for c in range(8)
                  if PARITY[r, c] == dark and distance[r, c] > HIGHLIGHT_THRESHOLD]
    return sorted(found)


def _check_checkerboard(board: np.ndarray) -> None:
    """Raise BoardNotFound unless the crop's squares alternate light/dark like a chessboard."""
    colors = _square_colors(board)
    parity = PARITY
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
    """Locate an axis-aligned digital chessboard in a BGR image. Returns (x0, y0, x1, y1), end-exclusive."""
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
    """Crop, resize and split the board exactly like the inference pipeline expects.

    Returns (512x512 grey board, squares of shape (64, 64, 64, 1), square names).
    """
    x0, y0, x1, y1 = bbox
    board = cv2.resize(img[y0:y1, x0:x1], BOARD_SIZE, interpolation=cv2.INTER_AREA)
    board = cv2.cvtColor(board, cv2.COLOR_BGR2GRAY)
    squares, names = extract_squares(board, flip=flip)
    return board, squares, names
