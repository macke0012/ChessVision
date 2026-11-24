"""Assemble an Ultralytics classification dataset: existing squares + new screenshot squares.

Usage: build_yolo_dataset.py REPO SQ_NEW OUT [VAL_FROM_INDEX] [KEEP_EMPTY]
  REPO          ChessVision repo (uses data/squares/training + validation)
  SQ_NEW        output of screenshots_to_squares.py (class folders, files <site>_<NNN>_<sq>.JPG)
  OUT           dataset dir to (re)create: OUT/train/<class>, OUT/val/<class>
  screenshots with index >= VAL_FROM_INDEX (default 90) go to val, the rest to train
  KEEP_EMPTY    fraction of new empty squares to keep (default 0.25)
"""
import hashlib
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

repo, sq_new, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
val_from = int(sys.argv[4]) if len(sys.argv) > 4 else 90
keep_empty = float(sys.argv[5]) if len(sys.argv) > 5 else 0.25

if out.exists():
    shutil.rmtree(out)
counts = Counter()
for split, src in (("train", "training"), ("val", "validation")):
    for cls_dir in sorted((repo / "data/squares" / src).iterdir()):
        if not cls_dir.is_dir():
            continue
        dst = out / split / cls_dir.name
        dst.mkdir(parents=True, exist_ok=True)
        for f in cls_dir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png"):
                shutil.copy2(f, dst / f"orig_{f.name}")
                counts[(split, "orig")] += 1

pat = re.compile(r"^(?P<site>[a-z]+)_(?P<idx>\d+)_(?P<sq>[a-h][1-8])\.JPG$")
for cls_dir in sorted(sq_new.iterdir()):
    if not cls_dir.is_dir():
        continue
    for f in sorted(cls_dir.iterdir()):
        m = pat.match(f.name)
        if not m:
            continue
        if cls_dir.name == "f" and int(hashlib.md5(f.name.encode()).hexdigest(), 16) % 1000 >= keep_empty * 1000:
            continue
        split = "val" if int(m["idx"]) >= val_from else "train"
        dst = out / split / cls_dir.name
        dst.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst / f.name)
        counts[(split, m["site"])] += 1

for k in sorted(counts):
    print(k, counts[k])
