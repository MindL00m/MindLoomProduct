#!/usr/bin/env bash
# Check LoomDriver against real Accessibility: builds the "Loom Fixture" app and runs
# Loom Capture's --driver-selftest on it. Needs a packaged Loom Capture with
# Accessibility granted (./scripts/package-app.sh, then System Settings → Privacy &
# Security → Accessibility). Usage: ./scripts/driver-selftest.sh [path/to/Loom Capture.app]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
AGENT="${1:-$HOME/Applications/Loom Capture.app}"
if [[ ! -d "$AGENT" ]]; then
  echo "error: $AGENT not found. Run ./scripts/package-app.sh first." >&2
  exit 1
fi

swift build -c release --product DriverFixture >&2
BIN="$(swift build -c release --product DriverFixture --show-bin-path)/DriverFixture"
FIXTURE="$ROOT/.build/Loom Fixture.app"
rm -rf "$FIXTURE"
mkdir -p "$FIXTURE/Contents/MacOS"
cp "$BIN" "$FIXTURE/Contents/MacOS/DriverFixture"
cat > "$FIXTURE/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.mindloom.driver-fixture</string>
  <key>CFBundleName</key><string>Loom Fixture</string>
  <key>CFBundleExecutable</key><string>DriverFixture</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>LSMinimumSystemVersion</key><string>15.0</string>
</dict>
</plist>
PLIST
codesign --force --sign - "$FIXTURE" >/dev/null

LOG="$(mktemp -t loom-driver-selftest)"
# `open` (not the binary directly) so macOS checks Loom Capture's own Accessibility grant.
# -n starts a separate instance even if Loom Capture is already running.
open -n -W "$AGENT" --args --driver-selftest "$FIXTURE" "$LOG"
cat "$LOG"
grep -q "SELFTEST PASSED" "$LOG"
