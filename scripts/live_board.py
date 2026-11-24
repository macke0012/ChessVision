"""
Live board: watch the screen, find a digital chessboard (lichess, chess.com, ...) and
show the recognized position in a small always-on-top window that keeps updating.

Setup (Python 3.10+ with tkinter; on Homebrew Python: brew install python-tk@3.10):

    pip install -r requirements-live.txt

Run from the repo root:

    python scripts/live_board.py                                   # watch the screen
    python scripts/live_board.py --image screenshots/start.png --once   # recognize one file, print FEN

macOS: the app that runs Python (Terminal, iTerm, VS Code, ...) needs Screen Recording
permission (System Settings > Privacy & Security > Screen & System Audio Recording).
Without it the capture only shows the desktop background, and no board is found.

Uses the retrained YOLO square classifier in weights/yolo_classifier_screenshots.pt.
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import chess
import cv2
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from chessvision.cv_globals import BOARD_SIZE  # noqa: E402
from chessvision.digital_board import BoardNotFound, board_to_squares, find_board, highlighted_squares  # noqa: E402

DEFAULT_WEIGHTS = REPO / "weights" / "yolo_classifier_screenshots.pt"
DETECT_MAX_SIDE = 1600  # find the board on a downscaled copy for speed, crop from full resolution
REGION_DETECT_MAX_SIDE = 800  # when capturing just the board's region, the board fills it: go smaller
REGION_MARGIN = 0.05    # once found, capture only the board plus this fraction of its size around it
UNCHANGED_DIFF = 3.0    # max mean grey difference per square (0-255) to treat the board as unchanged

Rect = tuple[int, int, int, int]  # left, top, width, height in screen points


@dataclass
class Frame:
    img: np.ndarray          # captured BGR pixels
    left: float = 0.0        # where the image's top-left corner is on screen, in points
    top: float = 0.0
    scale: float = 1.0       # image pixels per point (2 on Retina)
    mask: tuple[int, int, int, int] | None = None  # blacked-out rect (our own window), image pixels


@dataclass
class Result:
    fen: str | None          # piece placement, None if no board was found
    message: str             # status line
    seconds: float           # time taken
    uncertain: int = 0       # squares whose top class probability is below 0.6
    bbox: tuple[int, int, int, int] | None = None  # board location in the captured image, pixels
    covered: bool = False    # our own window overlaps the board (those squares read as empty)
    reused: bool = False     # board looked unchanged, so the previous classification was reused
    watching: bool = False   # captured only the board's region, not the whole screen
    highlights: tuple[str, ...] = ()  # squares with a coloured background (e.g. last-move highlight)


class Recognizer:
    def __init__(self, weights: Path):
        import torch
        from ultralytics import YOLO

        self.torch = torch
        self.model = YOLO(str(weights))
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        # class folders were B, K, ..., _b, _k, ..., f  ->  piece symbols, 'f' = empty
        self.labels = [self.model.names[i].lstrip("_") for i in range(len(self.model.names))]
        self._last = None  # (squares as int16, flip, fen, uncertain) of the last classified board

    def recognize(self, img: np.ndarray, flip: bool, detect_max_side: int = DETECT_MAX_SIDE) -> Result:
        start = time.perf_counter()
        h, w = img.shape[:2]
        scale = min(1.0, detect_max_side / max(h, w))
        small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else img
        try:
            bbox = tuple(int(round(v / scale)) for v in find_board(small))
        except BoardNotFound:
            return Result(None, "No board on screen", time.perf_counter() - start)

        _, squares, names = board_to_squares(img, bbox, flip)
        x0, y0, x1, y1 = bbox
        board_bgr = cv2.resize(img[y0:y1, x0:x1], BOARD_SIZE, interpolation=cv2.INTER_AREA)
        highlights = tuple(names[r * 8 + c] for r, c in highlighted_squares(board_bgr))
        squares16 = squares.astype(np.int16)
        last = self._last
        if last is not None and last[1] == flip:
            per_square = np.abs(squares16 - last[0]).mean(axis=(1, 2, 3))
            if per_square.max() < UNCHANGED_DIFF:  # nothing moved: skip the classifier
                return Result(last[2], "", time.perf_counter() - start, last[3], bbox, reused=True,
                              highlights=highlights)

        x = self.torch.from_numpy(squares).permute(0, 3, 1, 2).float().div(255.0).repeat(1, 3, 1, 1)
        results = self.model.predict(x, device=self.device, verbose=False)
        probs = np.stack([r.probs.data.cpu().numpy() for r in results])

        board = chess.BaseBoard.empty()
        for p, name in zip(probs, names):
            label = self.labels[int(p.argmax())]
            if label != "f":
                board.set_piece_at(chess.parse_square(name), chess.Piece.from_symbol(label))
        uncertain = int((probs.max(axis=1) < 0.6).sum())
        self._last = (squares16, flip, board.board_fen(), uncertain)
        return Result(board.board_fen(), "", time.perf_counter() - start, uncertain, bbox, highlights=highlights)


def _apply_mask(frame: Frame, exclude: Rect | None) -> None:
    """Black out our own window (screen points) wherever it falls inside the captured image."""
    if not exclude:
        return
    x, y, w, h = exclude
    s = frame.scale
    xa, ya = max(0, int((x - frame.left) * s)), max(0, int((y - frame.top) * s))
    xb = min(frame.img.shape[1], int((x - frame.left + w) * s))
    yb = min(frame.img.shape[0], int((y - frame.top + h) * s))
    if xa < xb and ya < yb:
        frame.img[ya:yb, xa:xb] = 0
        frame.mask = (xa, ya, xb, yb)


class ScreenSource:
    """Grabs one monitor, or just a region of it, with mss."""

    def __init__(self, monitor: int):
        self.monitor_index = monitor
        self._sct = None

    def _mss(self):
        import mss

        if self._sct is None:  # create in the thread that uses it
            self._sct = mss.MSS() if hasattr(mss, "MSS") else mss.mss()
        return self._sct

    def bounds(self) -> Rect:
        mon = self._mss().monitors[self.monitor_index]
        return mon["left"], mon["top"], mon["width"], mon["height"]

    def grab(self, exclude: Rect | None = None, region: Rect | None = None) -> Frame:
        left, top, width, height = region or self.bounds()
        shot = self._mss().grab({"left": left, "top": top, "width": width, "height": height})
        img = np.asarray(shot)[:, :, :3].copy()  # BGRA -> BGR
        frame = Frame(img, left, top, img.shape[1] / width)
        _apply_mask(frame, exclude)
        return frame


class ImageSource:
    """A fixed image standing in for the screen (1 pixel = 1 point)."""

    def __init__(self, path: Path | None = None, img: np.ndarray | None = None):
        if img is None:
            img = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if img is None:
                raise SystemExit(f"cannot read image {path}")
        self.img = img

    def bounds(self) -> Rect:
        return 0, 0, self.img.shape[1], self.img.shape[0]

    def grab(self, exclude: Rect | None = None, region: Rect | None = None) -> Frame:
        # our window isn't part of the image, so there is nothing to black out (exclude is ignored)
        left, top, width, height = region or self.bounds()
        return Frame(self.img[top:top + height, left:left + width].copy(), left, top, 1.0)


def board_region(bbox: tuple[int, int, int, int], frame: Frame, bounds: Rect) -> Rect:
    """Screen rect (points) around a board found in `frame`, with a margin, clipped to the screen."""
    x0, y0, x1, y1 = bbox
    left, top = frame.left + x0 / frame.scale, frame.top + y0 / frame.scale
    right, bottom = frame.left + x1 / frame.scale, frame.top + y1 / frame.scale
    m = max(8.0, REGION_MARGIN * (right - left))
    bl, bt, bw, bh = bounds
    l_, t_ = max(bl, int(left - m)), max(bt, int(top - m))
    r_, b_ = min(bl + bw, int(right + m) + 1), min(bt + bh, int(bottom + m) + 1)
    return l_, t_, r_ - l_, b_ - t_


def describe(result: Result, frame: Frame) -> str:
    if result.bbox is None:
        return "No board on screen"
    x0, y0, x1, _ = result.bbox
    where = (f"Board at {frame.left + x0 / frame.scale:.0f},{frame.top + y0 / frame.scale:.0f} "
             f"({(x1 - x0) / frame.scale:.0f} pt)")
    return where + (" · watching board only" if result.watching else " · whole screen")


def screen_capture_allowed() -> bool | None:
    """macOS only: whether this process may record the screen (None if unknown)."""
    if sys.platform != "darwin":
        return None
    try:
        import ctypes

        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except (OSError, AttributeError):
        return None


class App:
    SQ = 28  # square size in the mini board, in points
    LIGHT, DARK = "#f0d9b5", "#b58863"
    GLYPHS = {"k": "♚", "q": "♛", "r": "♜", "b": "♝", "n": "♞", "p": "♟"}
    OUTLINE = {"k": "♔", "q": "♕", "r": "♖", "b": "♗", "n": "♘", "p": "♙"}

    def __init__(self, recognizer: Recognizer, source, interval: float, flip: bool):
        import tkinter as tk
        from tkinter import font as tkfont

        self.tk = tk
        self.recognizer, self.source, self.interval = recognizer, source, interval
        self.root = tk.Tk()
        self.root.title("Live board")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)

        families = set(tkfont.families(self.root))
        family = next((f for f in ("Apple Symbols", "Segoe UI Symbol", "DejaVu Sans") if f in families), None)
        self.piece_font = (family, int(self.SQ * 0.8)) if family else ("TkDefaultFont", int(self.SQ * 0.8))

        size = 8 * self.SQ
        self.canvas = tk.Canvas(self.root, width=size, height=size, highlightthickness=0)
        self.canvas.pack(padx=6, pady=(6, 2))
        self.fen_var = tk.StringVar(value="")
        tk.Label(self.root, textvariable=self.fen_var, font=("TkFixedFont", 9), wraplength=size,
                 justify="left").pack(padx=6, anchor="w")
        self.status_var = tk.StringVar(value="Starting…")
        tk.Label(self.root, textvariable=self.status_var, font=("TkDefaultFont", 9), fg="#666",
                 wraplength=size, justify="left").pack(padx=6, anchor="w")
        row = tk.Frame(self.root)
        row.pack(fill="x", padx=6, pady=(2, 6))
        self.flip_var = tk.BooleanVar(value=flip)
        tk.Checkbutton(row, text="Black at bottom", variable=self.flip_var,
                       command=self._settings_changed).pack(side="left")
        tk.Button(row, text="Copy FEN", command=self._copy_fen).pack(side="right")
        self.root.bind("f", lambda e: (self.flip_var.set(not self.flip_var.get()), self._settings_changed()))
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        # open in the top-right corner, away from where sites usually put the board: the app can't
        # see the squares under its own window (it blacks itself out so it never reads its mini board)
        self.root.geometry("-12+40")

        self._lock = threading.Lock()
        self._latest: Result | None = None
        self._exclude: tuple[int, int, int, int] | None = None
        self._flip = flip
        self._shown_fen: str | None = None
        self._candidate: str | None = None
        self._running = True
        self._draw(None)
        self._last_kind: str | None = None
        if screen_capture_allowed() is False and isinstance(source, ScreenSource):
            msg = ("Screen Recording permission missing: allow the app running Python in "
                   "System Settings > Privacy & Security > Screen & System Audio Recording, then restart.")
            self.status_var.set(msg)
            print(msg, file=sys.stderr, flush=True)
        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(100, self._poll)

    # ---- background thread: capture + recognize ----
    def _worker(self):
        region: Rect | None = None  # where the board was last seen; None = search the whole screen
        while self._running:
            start = time.perf_counter()
            with self._lock:
                exclude, flip = self._exclude, self._flip
            try:
                result, frame, region = self.step(exclude, flip, region)
                self._annotate(result, frame)
            except Exception as e:  # keep the loop alive; show the error in the window
                result, region = Result(None, f"Error: {e}", 0.0), None
            result.seconds = time.perf_counter() - start  # whole frame: capture + recognition
            with self._lock:
                self._latest = result
            time.sleep(max(0.0, self.interval - (time.perf_counter() - start)))

    def step(self, exclude: Rect | None, flip: bool, region: Rect | None):
        """One capture: just the board's region if we know it, else (or if it's gone) the whole screen."""
        frame = self.source.grab(exclude, region)
        result = self.recognizer.recognize(frame.img, flip, REGION_DETECT_MAX_SIDE if region else DETECT_MAX_SIDE)
        if result.bbox is None and region is not None:
            # board left its old spot (page scrolled, window moved or closed): search the whole screen
            region = None
            frame = self.source.grab(exclude, None)
            result = self.recognizer.recognize(frame.img, flip)
        result.watching = region is not None
        region = board_region(result.bbox, frame, self.source.bounds()) if result.bbox else None
        return result, frame, region

    @staticmethod
    def _annotate(result: Result, frame: Frame) -> None:
        result.message = describe(result, frame)
        if result.fen is None and frame.mask:
            result.message += " (is this window covering it?)"
        if result.bbox and frame.mask:
            bx0, by0, bx1, by1 = result.bbox
            mx0, my0, mx1, my1 = frame.mask
            if mx0 < bx1 and bx0 < mx1 and my0 < by1 and by0 < my1:
                result.covered = True
                result.message += " · this window covers part of the board, move it aside"

    # ---- UI thread ----
    def _poll(self):
        if not self._running:
            return
        r = self.root
        with self._lock:
            self._exclude = (r.winfo_rootx(), r.winfo_rooty(), r.winfo_width(), r.winfo_height())
            result, self._latest = self._latest, None
        if result is not None:
            self._show(result)
        self.root.after(100, self._poll)

    def _show(self, result: Result):
        extra = (" · unchanged" if result.reused else "") + (f" · {result.uncertain} uncertain" if result.uncertain else "")
        self.status_var.set(f"{result.message} · {result.seconds * 1000:.0f} ms{extra}")
        if result.fen:
            kind = "covered" if result.covered else ("watching" if result.watching else "whole screen")
        else:
            kind = result.message
        if kind != self._last_kind:  # also log to the terminal when the state changes
            print(time.strftime("%H:%M:%S"), result.message, flush=True)
            self._last_kind = kind
        if result.fen is None:
            return
        # only switch to a new position once it has been seen twice in a row (skips mid-animation frames)
        if result.fen != self._shown_fen:
            if result.fen == self._candidate:
                self._shown_fen = result.fen
                self.fen_var.set(result.fen)
                self._draw(result.fen)
                print(time.strftime("%H:%M:%S"), "position", result.fen, flush=True)
            self._candidate = result.fen

    def _draw(self, fen: str | None):
        c, s = self.canvas, self.SQ
        c.delete("all")
        flip = self.flip_var.get()
        board = chess.BaseBoard(fen) if fen else chess.BaseBoard.empty()
        for row in range(8):
            for col in range(8):
                x, y = col * s, row * s
                c.create_rectangle(x, y, x + s, y + s, width=0,
                                   fill=self.LIGHT if (row + col) % 2 == 0 else self.DARK)
                file, rank = (7 - col, row) if flip else (col, 7 - row)
                piece = board.piece_at(chess.square(file, rank))
                if piece:
                    sym = piece.symbol().lower()
                    cx, cy = x + s / 2, y + s / 2
                    if piece.color == chess.WHITE:  # white body under a black outline glyph
                        c.create_text(cx, cy, text=self.GLYPHS[sym], font=self.piece_font, fill="white")
                        c.create_text(cx, cy, text=self.OUTLINE[sym], font=self.piece_font, fill="black")
                    else:
                        c.create_text(cx, cy, text=self.GLYPHS[sym], font=self.piece_font, fill="black")

    def _settings_changed(self):
        with self._lock:
            self._flip = self.flip_var.get()
        self._shown_fen = self._candidate = None

    def _copy_fen(self):
        if self._shown_fen:
            self.root.clipboard_clear()
            self.root.clipboard_append(f"{self._shown_fen} w - - 0 1")

    def _close(self):
        self._running = False
        self.root.destroy()

    def run(self, exit_after: float | None = None):
        if exit_after:
            self.root.after(int(exit_after * 1000), self._close)
        self.root.mainloop()


def main() -> int:
    parser = argparse.ArgumentParser(description="Show the chess position currently on screen.")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS, help="YOLO square classifier (.pt)")
    parser.add_argument("--interval", type=float, default=0.25, help="seconds between captures (default 0.25)")
    parser.add_argument("--monitor", type=int, default=1, help="mss monitor number, 1 = main display")
    parser.add_argument("--flip", action="store_true", help="start with 'Black at bottom' ticked")
    parser.add_argument("--image", type=Path, help="recognize this image file instead of the screen")
    parser.add_argument("--once", action="store_true", help="recognize once, print the FEN and exit (no window)")
    parser.add_argument("--exit-after", type=float, help=argparse.SUPPRESS)  # for smoke tests
    args = parser.parse_args()

    if not args.weights.exists():
        print(f"Classifier weights not found: {args.weights}", file=sys.stderr)
        return 2
    recognizer = Recognizer(args.weights)
    source = ImageSource(args.image) if args.image else ScreenSource(args.monitor)

    if args.once:
        frame = source.grab()
        result = recognizer.recognize(frame.img, args.flip)
        print(result.fen or "-", f"# {describe(result, frame)}, {result.seconds * 1000:.0f} ms, "
                                 f"{result.uncertain} uncertain")
        return 0 if result.fen else 1

    App(recognizer, source, args.interval, args.flip).run(args.exit_after)
    return 0


if __name__ == "__main__":
    sys.exit(main())
