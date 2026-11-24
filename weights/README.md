# Weights

| File | What it is |
| --- | --- |
| `yolo_classifier_screenshots.pt` | Square classifier used by `scripts/live_board.py`, `scripts/board_api.py` and the overlay app. `yolov8n-cls`, 64x64 input, 13 classes (`B K N P Q R _b _k _n _p _q _r f`, `f` = empty). Fine-tuned from the base model below on 200 lichess/chess.com screenshots plus `data/squares`. |
| `yolo_classifier_3lc_base.pt` | The base model: `best_yolo_classifier.pt` from [ChessVision-3LC](https://github.com/gudbrandtandberg/ChessVision-3LC). |
| `yolo_classifier_screenshots_training.csv` | Per-epoch training log of the fine-tune (Ultralytics `results.csv`). |

On the 14 test screenshots (`python scripts/test_screenshots.py`): base model 97.7% of squares (875/896, 7 of 14 boards exact), fine-tuned 99.7% (893/896, 12 of 14 exact; the misses are pieces under chess.com's "Checkmate" badge).

Both models are YOLO (Ultralytics) models and are licensed under AGPL-3.0, like the ChessVision-3LC weights they come from.
