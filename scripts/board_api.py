"""
Local HTTP API used by the macOS overlay app (macos/ChessVisionOverlay): finds a digital chessboard
in an image, recognizes the position and annotates pins and attacked pieces.

    python scripts/board_api.py [--port 8765]

    GET  /health                       -> {"ok": true}
    GET  /moves?fen=<placement>&square=e4
         -> {"square", "piece", "moves": [{"to", "san", "kind": safe|trade|blunder|wins|mate, "delta"}]}
         rates each legal move of that piece (as if its side were to move) for the hover tracker.
    POST /analyze?flip=0|1&region=0|1&turn=auto|white|black  body: PNG or JPEG bytes of a screen capture
         -> {"found": bool, "bbox": [x0, y0, x1, y1] (image pixels, end-exclusive), "fen": str,
             "uncertain": int, "reused": bool, "ms": float, "pins": [...], "attacked": [...],
             "turn": "white"|"black"|null, "turn_source": str, "highlights": [squares],
             "mates": [...], "mates_complete": bool, "threats": [...]}
         region=1 means the image is just the board's area (enables faster detection).
         flip=1 means the board is shown from Black's side.
         turn=auto works out the side to move from a check, the moves it sees being played, or the
         last-move highlight.

Listens on 127.0.0.1 only. Requests are handled one at a time (the recognizer is not thread-safe).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from functools import lru_cache
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import chess
import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from live_board import DEFAULT_WEIGHTS, DETECT_MAX_SIDE, REGION_DETECT_MAX_SIDE, Recognizer  # noqa: E402

from chessvision.board_analysis import analyze_position, find_move, infer_side_to_move, move_safety  # noqa: E402

MAX_BODY = 64 * 1024 * 1024


@lru_cache(maxsize=512)
def cached_move_safety(fen: str, square: str) -> dict:
    return move_safety(chess.BaseBoard(fen), square)


@lru_cache(maxsize=256)
def cached_analysis(fen: str, turn: chess.Color | None) -> dict:
    return analyze_position(chess.BaseBoard(fen), turn)


class TurnTracker:
    """Side to move for the position on screen, in order of preference:
    1. a king in check (only the side to move can be in check)
    2. the move it saw being played: the previous stable position plus one legal move gives this one
       (positions already seen keep the side they had, which handles stepping back through a game)
    3. the last-move highlight
    A position counts as "stable" once it has been seen in two requests in a row, so a one-frame
    misread is never taken for a move. The known side is kept while the position stays the same."""

    MEMORY = 2000

    def __init__(self):
        self.candidate: str | None = None
        self.stable: str | None = None
        self.known: dict[str, tuple[chess.Color, str]] = {}   # placement -> (side to move, how decided)

    def _remember(self, fen: str, turn: chess.Color, source: str) -> None:
        self.known.pop(fen, None)
        self.known[fen] = (turn, source)
        while len(self.known) > self.MEMORY:
            self.known.pop(next(iter(self.known)))

    def resolve(self, fen: str, highlights, requested: str) -> tuple[chess.Color | None, str]:
        placement = chess.BaseBoard(fen)
        if fen != self.stable and fen == self.candidate:  # just became stable
            if fen not in self.known and self.stable is not None:
                found = find_move(chess.BaseBoard(self.stable), placement)
                if found is not None:
                    self._remember(fen, *found)
            self.stable = fen
        self.candidate = fen

        if requested in ("white", "black"):
            return requested == "white", "set in menu"
        turn, source = infer_side_to_move(placement, highlights)
        if source == "check":
            return turn, source
        if fen in self.known:
            return self.known[fen]
        if turn is not None:  # last-move highlight
            if fen == self.stable:
                self._remember(fen, turn, source)
            return turn, source
        return None, source


def make_handler(recognizer: Recognizer):
    tracker = TurnTracker()

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            url = urlparse(self.path)
            if url.path == "/health":
                self._send(200, {"ok": True})
            elif url.path == "/moves":
                q = parse_qs(url.query)
                try:
                    fen, square = q["fen"][0].split()[0], q["square"][0]
                    chess.BaseBoard(fen)
                    chess.parse_square(square)
                except (KeyError, IndexError, ValueError):
                    self._send(400, {"error": "need ?fen=<piece placement>&square=<e.g. e4>"})
                    return
                self._send(200, cached_move_safety(fen, square))
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            url = urlparse(self.path)
            if url.path != "/analyze":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                self._send(400, {"error": "missing or too large image body"})
                return
            img = cv2.imdecode(np.frombuffer(self.rfile.read(length), np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                self._send(400, {"error": "could not decode image"})
                return
            q = parse_qs(url.query)
            flip = q.get("flip", ["0"])[0] == "1"
            region = q.get("region", ["0"])[0] == "1"
            requested_turn = q.get("turn", ["auto"])[0]

            start = time.perf_counter()
            result = recognizer.recognize(img, flip, REGION_DETECT_MAX_SIDE if region else DETECT_MAX_SIDE)
            payload = {"found": result.fen is not None, "image": [img.shape[1], img.shape[0]],
                       "pins": [], "attacked": [], "mates": [], "threats": []}
            if result.fen is not None:
                turn, source = tracker.resolve(result.fen, result.highlights, requested_turn)
                payload.update(bbox=list(result.bbox), fen=result.fen, uncertain=result.uncertain,
                               reused=result.reused, highlights=list(result.highlights),
                               turn=None if turn is None else ("white" if turn else "black"), turn_source=source,
                               **cached_analysis(result.fen, turn))
            payload["ms"] = round((time.perf_counter() - start) * 1000, 1)
            self._send(200, payload)

        def log_message(self, fmt, *args):  # keep the console quiet
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Board recognition API for the overlay app.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    args = parser.parse_args()
    if not args.weights.exists():
        print(f"Classifier weights not found: {args.weights}", file=sys.stderr)
        return 2
    recognizer = Recognizer(args.weights)
    server = HTTPServer(("127.0.0.1", args.port), make_handler(recognizer))
    print(f"board API listening on http://127.0.0.1:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
