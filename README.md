# Chess Vision - a computer vision chess project

> **Note**: This project has moved to [ChessVision-3LC](https://github.com/gudbrandtandberg/ChessVision-3LC)

This repository contains code and resources for extracting chess positions from images using computer vision.

## Try it

The app is sometimes up and running, if you're lucky you can try it out at
[chessvision.net](http://chessvision.net).

## Example

From *any* square input image, the ChessVision algorithm extracts and classifies any chessboard contained in the image.

<p float="left">
    <img src="./img/example_raw.JPG" width="320"/>
    <img src="./img/example2.png" width="320"/>
</p>

## Earlier work

Several solutions to the chess-extraction-and-classification problem have been posed. Most rely on a combination of computer vision and machine learning, however, many different approaches are possible.

The following two links point to internet discussions about the problem

+ https://www.chess.com/forum/view/endgames/convert-pngimage-file-to-pgn-or-fen
+ https://www.reddit.com/r/chess/comments/856pjh/is_there_a_way_to_get_a_fen_from_an_image_of_the/

The next links point to existing implementations

+ [chessputzer](https://github.com/metterklume/chessputzer) [(try)](https://www.ocf.berkeley.edu/~abhishek/putz/run.fcgi/upload)
+ [ChessFigRdrLite](http://www.kgrothapps.com/)
+ [chessgrabber](http://www.chessgrabber.nicolaas.net/)
+ [tensorflow_chessbot](https://github.com/Elucidation/tensorflow_chessbot)
+ [chessify](https://chessify.me/)
+ [chessocr](https://github.com/npcardoso/chessocr)

## ChessVision

The goal of ChessVision is to be able to correctly classify as many as possible different inputs. Since ChessVision is based on machine learning, in particular deep learning, the hope is that the performance of ChessVision will improve as more training data comes in.

We impose as few constraints on the inputs as possible, in principle any photograph of a 2D representation of a chess board should be identifyable by the algorithm. In particular, different formats such as books, screenshots, photos of screens, etc. should be allowable, under any lighting and small scale and rotational deviations. However, the input image must be square, at least 512 pixels wide, and contain exactly one chessboard filling at least 35% of the image area.

The current performance of the best models can be evaluated in this [notebook](./chessvision/notebooks/Performance&#32;Report.ipynb).

## Algorithm details

### Board extraction

The first step is to extact square chessboards from raw photographs, the following plot shows the algorithms performance on a batch of test data. 

<img src="./img/test_extraction.png" />

Board extraction is done in two steps, first the image is resized to 256x256 and fed into a deep convolutional neural network (the [unet](https://github.com/zhixuhao/unet) architecture) after which a heatmap is produced, estimating the probability that each pixel is part of a chessboard. The heatmap is then converted to a binary mask using a simple thresholding operator. Next, a contour approximation algorithm approximates the mask using four points. The board is extracted from the raw image using these four points. The next image illustrates the trained model's performance on a batch of training data. 

<img src="./img/training_extraction.png" />

#### Training

The `u_net` model is quite large (approx 35M parameters) and takes quite a while to train (approx 6 hours from scratch on a high performance GPU). In order to train this network from a relatively small training set, image augmentation is used. This [notebook](./chessvision/notebooks/View&#32;Board&#32;Extraction&#32;Training&#32;Data.ipynb) shows what a batch of training data looks like and is useful for evaluating the current augmentation scheme.  

### Board classification

The next step is to cut the board image into 64 little 64x64-pixel squares, with accompanying square-names (in order to account for board orientation). We classify squares using a small convolutional network.

The performance of the current generation of square classifiers is promising; the next image shows the results on a batch of training data (99.6% accuracy)

<img src="./img/training_classification1.png" />

#### Training data from screenshots

`scripts/screenshots_to_squares.py` turns screenshots of digital boards (lichess, chess.com, ...) with a known FEN into labelled training squares. It finds the board from its square grid (no extraction model needed) and writes 64x64 greyscale squares into the same class folders as `data/squares/training`:

```
python scripts/screenshots_to_squares.py labels.csv -o data/new_squares --debug-dir data/new_squares_debug
```

`labels.csv` has the columns `file,fen,flip` (`flip` is `true` when the board is shown from Black's side). Check the grids in the debug folder, then move the squares into `data/squares/training` / `validation`. Keep the screenshots you test with out of the labels file.

## Live board

`scripts/live_board.py` watches the screen, finds a digital chessboard (lichess, chess.com, ...) and shows the recognized position in a small always-on-top window that updates four times a second (`--interval` to change). Once the board is found, only that part of the screen is captured; the whole screen is searched again if the board moves or disappears. It uses the square classifier in `weights/yolo_classifier_screenshots.pt` (a YOLO classifier fine-tuned on lichess and chess.com screenshots).

```
pip install -r requirements-live.txt
python scripts/live_board.py
```

### Overlay app (macOS)

`macos/ChessVisionOverlay` is a menu-bar app (Swift, macOS 14+) that draws over the board on screen:

- the side to move, worked out from a check, the moves it sees being played (also when stepping back through a game), or the last-move highlight (or set in the menu),
- when you hover over (or drag) a piece: every square it can move to, green if safe, yellow for an equal trade, red with the material lost for a blunder (exchange evaluation on the square plus pieces the move leaves hanging),
- forced mates in 1 or 2 for the side to move (green arrow, M1/M2; the full line is in the menu),
- mates the opponent threatens if it were their move (dashed red arrow),
- pieces in danger (check, undefended, or attacked by a cheaper piece): red for the side to move, blue for opponent pieces you can win, with arrows from the attackers,
- pinned pieces (purple, dashed line from the pinning piece to the king) and, optionally, attacked-but-defended pieces (orange).

Using this during a live game is outside assistance, which chess.com and lichess forbid; use it for analysis, reviewing games and watching. It captures the screen 4 times a second with ScreenCaptureKit (leaving out its own overlay), and gets the position and analysis from `scripts/board_api.py`, which it starts itself.

```
python3.10 -m venv .venv && .venv/bin/pip install -r requirements-live.txt
macos/build_app.sh
open "macos/build/ChessVision Overlay.app"
```

On first launch, allow "ChessVision Overlay" under System Settings > Privacy & Security > Screen & System Audio Recording, then quit and reopen it. The ♞ menu has the toggles (Black at bottom, which annotations to show), a legend and Copy FEN. The API log is in `~/Library/Logs/ChessVisionOverlay/board_api.log`.

### Live board options

Tick "Black at bottom" (or press `f`) when the board is shown from Black's side. "Copy FEN" copies the position with White to move. On macOS, the app running Python (Terminal, iTerm, VS Code, ...) needs Screen Recording permission in System Settings > Privacy & Security. To test without the screen: `python scripts/live_board.py --image screenshots/start.png --once`.

## Install

Prerequisites: MacOS or Linux with anaconda and conda installed

First, download this repository:

```
git clone https://github.com/gudbrandtandberg/ChessVision
cd ChessVision
```

Next, run

`./install.sh`

to create a python environment named `chessvision` and install all the dependencies.

To use chessvision, add the following to your `~/.bash_profile`:

```
export CVROOT="<path-to-ChessVision-folder>"
export PYTHONPATH=$PYTHONPATH:$CVROOT/chessvision/
export PYTHONPATH=$PYTHONPATH:$CVROOT/chessvision/model/
export PYTHONPATH=$PYTHONPATH:$CVROOT/chessvision/data_processing/
export PYTHONPATH=$PYTHONPATH:$CVROOT/chessvision/training/
```

and run 

`source ~/.bash_profile`.

Now open two terminal windows and activate the `chessvision` environment: `source activate chessvision`.

After running 

``` 
cd computeroot
python cv_endpoint.py --local
````

and 

```
cd webroot
python main.py --local
```

the ChessVision web server will be listening on `localhost:5000/`. Enjoy!

## References

+ https://github.com/ChessCV/chess
+ http://cvgl.stanford.edu/teaching/cs231a_winter1415/prev/projects/chess.pdf
+ http://www.raspberryturk.com/details/vision.html
+ http://vision.soic.indiana.edu/b657/sp2016/projects/rkanchib/paper.pdf
+ http://indigenousengineering.com/index.php/16-ie-project/10-extract-chess-position-from-an-image-using-image-processing-algorithms
+ https://vision.fe.uni-lj.si/cvww2016/proceedings/papers/21.pdf
+ https://ieeexplore.ieee.org/document/489004/
+ https://arxiv.org/pdf/1708.03898.pdf
+ https://ieeexplore.ieee.org/document/5967178/
+ https://web.stanford.edu/class/ee368/Project_Spring_1415/Reports/Danner_Kafafy.pdf
+ http://rodrigob.github.io/are_we_there_yet/build/classification_datasets_results.html#4d4e495354
+ http://sci2s.ugr.es/keel/pdf/algorithm/articulo/knnAdaptive.pdf
