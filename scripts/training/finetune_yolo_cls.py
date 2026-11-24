"""Fine-tune the ChessVision-3LC YOLO square classifier on a folder dataset.

Usage: finetune_yolo_cls.py BASE_WEIGHTS DATASET PROJECT_DIR [EPOCHS]
"""
import sys

import torch
from ultralytics import YOLO

base, data, project = sys.argv[1], sys.argv[2], sys.argv[3]
epochs = int(sys.argv[4]) if len(sys.argv) > 4 else 15

model = YOLO(base)
model.train(data=data, imgsz=64, epochs=epochs, patience=5, batch=128, device="mps" if torch.backends.mps.is_available() else None,
            workers=4, project=project, name="finetune", exist_ok=True, plots=False, verbose=False)
