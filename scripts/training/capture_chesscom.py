"""Capture chess.com analysis-board screenshots of random positions, with a labels CSV.

Usage: capture_chesscom.py OUT_DIR COUNT [SEED]
Writes OUT_DIR/chesscom/NNN.png and appends rows to OUT_DIR/labels.csv (file,fen,flip).
Stops at the first sign of a bot check / rate limit instead of working around it.
"""
import csv
import random
import sys
import time
import urllib.parse
from pathlib import Path

import chess
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

out, count = Path(sys.argv[1]), int(sys.argv[2])
seed = int(sys.argv[3]) if len(sys.argv) > 3 else 1
rng = random.Random(seed)


def random_position() -> chess.Board:
    """Random legal position from a random game; varied length gives openings to endgames."""
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


VIEWPORTS = [(888, 727), (888, 727), (888, 727), (1000, 800), (1280, 800), (1440, 900), (820, 1000)]

(out / "chesscom").mkdir(parents=True, exist_ok=True)
labels = out / "labels.csv"
new_file = not labels.exists()
seen = set()
with sync_playwright() as p, labels.open("a", newline="") as fh:
    w = csv.writer(fh)
    if new_file:
        w.writerow(["file", "fen", "flip"])
    browser = p.chromium.launch()
    ctx = browser.new_context(viewport={"width": 888, "height": 727}, device_scale_factor=2, locale="en-US")
    page = ctx.new_page()
    done = 0
    while done < count:
        board = random_position()
        placement = board.board_fen()
        if placement in seen:
            continue
        seen.add(placement)
        flip = rng.random() < 0.25
        vw, vh = rng.choice(VIEWPORTS)
        page.set_viewport_size({"width": vw, "height": vh})
        url = "https://www.chess.com/analysis?fen=" + urllib.parse.quote(board.fen()) + ("&flip=true" if flip else "")
        resp = page.goto(url, wait_until="domcontentloaded", timeout=60000)
        title = page.title()
        if resp is None or resp.status in (403, 429) or "just a moment" in title.lower():
            print(f"STOP: blocked or rate-limited (status={resp.status if resp else None}, title={title!r})")
            break
        try:
            page.wait_for_selector("wc-chess-board .piece, .board .piece", timeout=20000)
        except PWTimeout:
            print(f"skip {done:03d}: board did not render")
            continue
        # info pop-up first (it blocks clicks), then the privacy banner: reject non-essential cookies
        ok = page.get_by_role("button", name="Ok", exact=True)
        try:
            if ok.count() and ok.first.is_visible():
                ok.first.click(timeout=3000)
                page.wait_for_timeout(500)
        except PWTimeout:
            pass
        reject = page.locator("#onetrust-reject-all-handler")
        try:
            if done == 0:
                reject.wait_for(state="visible", timeout=10000)
            if reject.count() and reject.first.is_visible():
                reject.first.click(timeout=5000)
                page.wait_for_timeout(1000)
        except PWTimeout:
            pass
        page.mouse.move(1, 1)
        page.wait_for_timeout(1500)
        if (reject.count() and reject.first.is_visible()) or (ok.count() and ok.first.is_visible()):
            print(f"skip: overlay still covering the board ({placement})")
            continue
        fname = f"chesscom/{done:03d}.png"
        page.screenshot(path=str(out / fname))
        w.writerow([fname, board.fen(), "true" if flip else "false"])
        fh.flush()
        done += 1
        print(f"{done}/{count} {vw}x{vh} flip={flip} {placement}", flush=True)
        time.sleep(rng.uniform(2.0, 4.0))
    browser.close()
print(f"captured {done}")
