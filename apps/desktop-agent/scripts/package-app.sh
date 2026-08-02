#!/usr/bin/env bash
# Build MindLoomAgent and wrap it in a minimal .app bundle so macOS treats it
# as a real GUI app (status item + window behave reliably).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

swift build -c release
BIN="$ROOT/.build/release/MindLoomAgent"
APP="$ROOT/dist/MindLoomAgent.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"

rm -rf "$APP"
mkdir -p "$MACOS"

cat > "$CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>MindLoomAgent</string>
  <key>CFBundleDisplayName</key>
  <string>Loom Capture</string>
  <key>CFBundleIdentifier</key>
  <string>com.mindloom.capture-agent</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>CFBundleShortVersionString</key>
  <string>0.1.0</string>
  <key>CFBundleExecutable</key>
  <string>MindLoomAgent</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAccessibilityUsageDescription</key>
  <string>Loom Capture uses Accessibility to record allowlisted app interaction metadata (not keystroke content) for workflow skill files.</string>
</dict>
</plist>
PLIST

cp "$BIN" "$MACOS/MindLoomAgent"
chmod +x "$MACOS/MindLoomAgent"

echo "Packaged: $APP"
echo "Launch with: open \"$APP\""
