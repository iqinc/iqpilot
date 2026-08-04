#!/bin/bash
# Copyright © IQ.Lvbs, apart of Project Teal Lvbs, All Rights Reserved, licensed under https://konn3kt.com/tos/
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" >/dev/null && pwd)"
REPO="$(cd "$HERE/../../.." >/dev/null && pwd)"
OUT="${1:-$HERE/dist}"
APP="$OUT/IQ Vision.app"

rm -rf "$OUT"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>IQ Vision</string>
  <key>CFBundleIdentifier</key><string>com.iqpilot.iqvision</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>iqvision</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/iqvision" <<'LAUNCH'
#!/bin/bash
RES="$(cd "$(dirname "$0")/../Resources" && pwd)"
if [ ! -x "$HOME/Library/Application Support/IQVision/venv/bin/python" ]; then
  osascript -e "tell application \"Terminal\"
    activate
    do script \"bash '$RES/macos/setup.sh'\"
  end tell"
else
  exec bash "$RES/macos/setup.sh" >/tmp/iqvision.log 2>&1
fi
LAUNCH
chmod +x "$APP/Contents/MacOS/iqvision"

RES="$APP/Contents/Resources"
SRC="$RES/openpilot/iqpilot/iqvd_private_src"
mkdir -p "$RES/tools/iqmacvisiond" "$RES/macos" "$SRC/offload" "$SRC/models"

cp "$REPO/tools/iqmacvisiond/server.py" "$REPO/tools/iqmacvisiond/menubar.py" "$RES/tools/iqmacvisiond/"
cp "$HERE/setup.sh" "$RES/macos/"
cp "$REPO/iqpilot/iqvd_private_src/__init__.py" "$REPO/iqpilot/iqvd_private_src/yolov8_net.py" "$SRC/"
cp "$REPO/iqpilot/iqvd_private_src/offload/"*.py "$SRC/offload/"
cp "$REPO/iqpilot/iqvd_private_src/models/yolov8n.safetensors" "$SRC/models/"
touch "$RES/openpilot/__init__.py" "$RES/openpilot/iqpilot/__init__.py" "$RES/tools/__init__.py" \
      "$RES/tools/iqmacvisiond/__init__.py"
rsync -a --exclude=".git" --exclude="__pycache__" --exclude="extra" --exclude="test" \
      --exclude="examples" --exclude="docs" "$REPO/tinygrad_repo/" "$RES/tinygrad_repo/"

hdiutil create -volname "IQ Vision" -srcfolder "$OUT" -ov -format UDZO "$OUT/IQVision.dmg" >/dev/null
echo "built: $OUT/IQVision.dmg ($(du -h "$OUT/IQVision.dmg" | cut -f1))"
