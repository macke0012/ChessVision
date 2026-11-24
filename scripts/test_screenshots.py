"""Check the live-board recognizer against the test screenshots in screenshots/.

Usage: python scripts/test_screenshots.py [--weights weights/yolo_classifier_screenshots.pt] [--dir screenshots]

Each screenshot is named after its position; "<name> 2.png" is the chess.com version of
"<name>.png" (lichess). Positions ending in "_black" are shown from Black's side.
"""
import argparse
import sys
from pathlib import Path

import chess
import cv2

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from live_board import DEFAULT_WEIGHTS, Recognizer  # noqa: E402

TRUTH = {
    "start": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR",
    "back_rank_mate": "R5k1/5ppp/8/8/8/8/5PPP/6K1",
    "check": "r1bqk1nr/ppp2Q1p/2n3p1/4p3/4P3/8/PPPP1PPP/RNB1K1NR",
    "checkmate": "r1bqkb1r/ppp2Qpp/2n2n2/4p3/2B1P3/8/PPPP1PPP/RNB1K1NR",
    "endgame": "8/8/8/8/3k4/8/8/3KQ3",
    "italian_midgame": "r1bqk2r/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R",
    "italian_midgame_black": "r1bqk2r/ppp2ppp/2np1n2/2b1p3/2B1P3/2NP1N2/PPP2PPP/R1BQK2R",
}


def errors(pred: str | None, true: str) -> list[str]:
    p, t = chess.BaseBoard(pred) if pred else chess.BaseBoard(None), chess.BaseBoard(true)
    return [f"{chess.square_name(q)}:{t.piece_at(q).symbol() if t.piece_at(q) else '-'}"
            f"->{p.piece_at(q).symbol() if p.piece_at(q) else '-'}"
            for q in chess.SQUARES if p.piece_at(q) != t.piece_at(q)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--dir", type=Path, default=REPO / "screenshots")
    args = parser.parse_args()

    recognizer = Recognizer(args.weights)
    total = correct = exact = 0
    for path in sorted(args.dir.glob("*.png")):
        key = path.stem.removesuffix(" 2")
        if key not in TRUTH:
            print(f"{path.name:<30} skipped (no known position)")
            continue
        site = "chess.com" if path.stem.endswith(" 2") else "lichess"
        recognizer._last = None  # classify every image from scratch
        result = recognizer.recognize(cv2.imread(str(path), cv2.IMREAD_COLOR), flip=key.endswith("_black"))
        errs = errors(result.fen, TRUTH[key])
        total += 64
        correct += 64 - len(errs)
        exact += not errs
        status = "no board" if result.fen is None else f"{64 - len(errs):>2}/64"
        print(f"{path.name:<30} {site:<9} {status}  {', '.join(errs) if result.fen and errs else ''}")
    if total:
        print(f"\n{correct}/{total} squares ({correct / total:.1%}), {exact}/{total // 64} boards exact")
    return 0 if total and correct == total else 1


if __name__ == "__main__":
    sys.exit(main())
