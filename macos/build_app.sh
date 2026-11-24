#!/bin/bash
# Build "ChessVision Overlay.app" (menu-bar app that draws pins / attacked pieces over a board on screen).
#
#   macos/build_app.sh            -> macos/build/ChessVision Overlay.app
#
# The app starts the board API (scripts/board_api.py) with the Python from CV_PYTHON
# (default: <repo>/.venv/bin/python, set up with: pip install -r requirements-live.txt).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
PYTHON="${CV_PYTHON:-$REPO/.venv/bin/python}"
PORT="${CV_PORT:-8765}"
APP="$HERE/build/ChessVision Overlay.app"

if [ ! -x "$PYTHON" ]; then
  echo "Python not found at $PYTHON (create it: python3 -m venv .venv && .venv/bin/pip install -r requirements-live.txt)" >&2
  exit 1
fi

(cd "$HERE/ChessVisionOverlay" && swift build -c release)

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$HERE/ChessVisionOverlay/.build/release/ChessVisionOverlay" "$APP/Contents/MacOS/ChessVisionOverlay"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>ChessVision Overlay</string>
  <key>CFBundleDisplayName</key><string>ChessVision Overlay</string>
  <key>CFBundleIdentifier</key><string>local.chessvision.overlay</string>
  <key>CFBundleExecutable</key><string>ChessVisionOverlay</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# paths for the app to start the board API with (JSON-escaped)
/usr/bin/python3 -c 'import json, sys; print(json.dumps({"python": sys.argv[1], "repo": sys.argv[2], "port": int(sys.argv[3])}))' \
  "$PYTHON" "$REPO" "$PORT" > "$APP/Contents/Resources/config.json"

# Ad-hoc signing ("-") changes the signature on every build, so macOS asks for Screen Recording again
# after each rebuild. Set CV_SIGN_IDENTITY to a code-signing certificate's name to keep the permission.
codesign --force --sign "${CV_SIGN_IDENTITY:--}" "$APP"
echo "Built: $APP"
