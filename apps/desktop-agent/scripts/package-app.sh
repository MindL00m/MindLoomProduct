#!/usr/bin/env bash
# Build MindLoomAgent, wrap it in a .app bundle, and publish a downloadable zip
# to the web app (apps/web/public/downloads/LoomCapture-macos.zip).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

WEB_BASE="${LOOM_WEB_BASE:-http://localhost:5500}"
WEB_BASE="${WEB_BASE%/}"
if [[ -n "${LOOM_API_BASE:-}" ]]; then
  API_BASE="${LOOM_API_BASE}"
elif [[ "$WEB_BASE" == *localhost* || "$WEB_BASE" == *127.0.0.1* ]]; then
  API_BASE="http://localhost:8000"
else
  # Same-origin nginx proxy used by Compose / typical Railway frontend images.
  API_BASE="/api"
fi
API_BASE="${API_BASE%/}"

echo "Packaging Loom Capture"
echo "  webBase: $WEB_BASE"
echo "  apiBase: $API_BASE"
if [[ "$WEB_BASE" == *localhost* || "$WEB_BASE" == *127.0.0.1* ]]; then
  echo "  warning: webBase is localhost. People who download this build from a" >&2
  echo "  deployed site will not be able to sign in. Re-run with:" >&2
  echo "    LOOM_WEB_BASE=https://your-web-host ./scripts/package-app.sh" >&2
fi

build_arch() {
  local arch="$1"
  swift build -c release --arch "$arch" >&2
  local candidate="$ROOT/.build/${arch}-apple-macosx/release/MindLoomAgent"
  if [[ -x "$candidate" ]]; then
    printf '%s' "$candidate"
    return 0
  fi
  return 1
}

ARM_BIN=""
X86_BIN=""
ARM_BIN="$(build_arch arm64 || true)"
X86_BIN="$(build_arch x86_64 || true)"

BIN=""
if [[ -n "$ARM_BIN" && -n "$X86_BIN" ]]; then
  BIN="$ROOT/.build/MindLoomAgent-universal"
  lipo -create -output "$BIN" "$ARM_BIN" "$X86_BIN"
  echo "  binary: universal (arm64 + x86_64)"
elif [[ -n "$ARM_BIN" ]]; then
  BIN="$ARM_BIN"
  echo "  binary: arm64"
elif [[ -n "$X86_BIN" ]]; then
  BIN="$X86_BIN"
  echo "  binary: x86_64"
else
  # Host-arch fallback (older SwiftPM layouts).
  swift build -c release
  if [[ -x "$ROOT/.build/arm64-apple-macosx/release/MindLoomAgent" ]]; then
    BIN="$ROOT/.build/arm64-apple-macosx/release/MindLoomAgent"
  elif [[ -x "$ROOT/.build/release/MindLoomAgent" ]]; then
    BIN="$ROOT/.build/release/MindLoomAgent"
  else
    echo "error: could not find the MindLoomAgent release binary" >&2
    exit 1
  fi
  echo "  binary: $(basename "$(dirname "$(dirname "$BIN")")")"
fi

APP="$ROOT/dist/Loom Capture.app"
CONTENTS="$APP/Contents"
MACOS="$CONTENTS/MacOS"
IDENTIFIER="com.mindloom.capture-agent"
VERSION="${LOOM_CAPTURE_VERSION:-0.2.0}"
BUILD="${LOOM_CAPTURE_BUILD:-2}"
ENTITLEMENTS="$ROOT/scripts/LoomCapture.entitlements"
# Developer ID Application: Your Name (TEAMID) — required for downloads Gatekeeper will open.
CODESIGN_IDENTITY="${MACOS_CODESIGN_IDENTITY:-}"

rm -rf "$ROOT/dist"
mkdir -p "$MACOS"

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>
  <string>Loom Capture</string>
  <key>CFBundleDisplayName</key>
  <string>Loom Capture</string>
  <key>CFBundleIdentifier</key>
  <string>${IDENTIFIER}</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleVersion</key>
  <string>${BUILD}</string>
  <key>CFBundleShortVersionString</key>
  <string>${VERSION}</string>
  <key>CFBundleExecutable</key>
  <string>MindLoomAgent</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.productivity</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAccessibilityUsageDescription</key>
  <string>Loom Capture uses Accessibility to record allowlisted app interaction metadata (not keystroke content) for workflow skill files.</string>
  <key>LoomWebBase</key>
  <string>${WEB_BASE}</string>
  <key>LoomAPIBase</key>
  <string>${API_BASE}</string>
</dict>
</plist>
PLIST

cp "$BIN" "$MACOS/MindLoomAgent"
chmod +x "$MACOS/MindLoomAgent"

sign_bundle() {
  local identity="$1"
  local extra=()
  if [[ "$identity" != "-" ]]; then
    extra+=(--options runtime --timestamp --entitlements "$ENTITLEMENTS")
  fi
  # Ad-hoc + entitlements makes TCC Accessibility fail to stick on local builds.
  if [[ ${#extra[@]} -gt 0 ]]; then
    codesign --force --sign "$identity" --identifier "$IDENTIFIER" \
      "${extra[@]}" "$MACOS/MindLoomAgent"
    codesign --force --sign "$identity" --identifier "$IDENTIFIER" \
      "${extra[@]}" "$APP"
  else
    codesign --force --sign "$identity" --identifier "$IDENTIFIER" \
      "$MACOS/MindLoomAgent"
    codesign --force --sign "$identity" --identifier "$IDENTIFIER" \
      "$APP"
  fi
}

if [[ -n "$CODESIGN_IDENTITY" ]]; then
  echo "  signing: Developer ID ($CODESIGN_IDENTITY)"
  sign_bundle "$CODESIGN_IDENTITY"
else
  echo "  signing: ad-hoc (Gatekeeper will block website downloads)"
  echo "  set MACOS_CODESIGN_IDENTITY='Developer ID Application: … (TEAMID)' to ship a notarized build."
  # Ad-hoc sign so TCC ties Accessibility to this app id (not a naked binary).
  sign_bundle "-"
fi

xattr -cr "$APP" 2>/dev/null || true
codesign --verify --verbose=2 "$APP"

ZIP="$ROOT/dist/LoomCapture-macos.zip"
# ditto preserves macOS metadata that a plain zip can strip.
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"

should_notarize() {
  if [[ -z "$CODESIGN_IDENTITY" ]]; then
    return 1
  fi
  if [[ "${MACOS_NOTARIZE:-}" == "0" ]]; then
    return 1
  fi
  if [[ -n "${MACOS_NOTARY_PROFILE:-}" ]]; then
    return 0
  fi
  if [[ -n "${APPLE_API_KEY:-}" && -n "${APPLE_API_KEY_ID:-}" && -n "${APPLE_API_ISSUER:-}" ]]; then
    return 0
  fi
  return 1
}

if should_notarize; then
  echo "  notarizing…"
  if [[ -n "${MACOS_NOTARY_PROFILE:-}" ]]; then
    xcrun notarytool submit "$ZIP" --keychain-profile "$MACOS_NOTARY_PROFILE" --wait
  else
    xcrun notarytool submit "$ZIP" \
      --key "$APPLE_API_KEY" \
      --key-id "$APPLE_API_KEY_ID" \
      --issuer "$APPLE_API_ISSUER" \
      --wait
  fi
  xcrun stapler staple "$APP"
  ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
  echo "  notarized and stapled"
elif [[ -n "$CODESIGN_IDENTITY" ]]; then
  echo "  warning: signed but not notarized. Gatekeeper still blocks first launch" >&2
  echo "  until you staple a ticket. Set MACOS_NOTARY_PROFILE or APPLE_API_KEY /" >&2
  echo "  APPLE_API_KEY_ID / APPLE_API_ISSUER (or MACOS_NOTARIZE=0 to skip)." >&2
elif [[ "$WEB_BASE" != *localhost* && "$WEB_BASE" != *127.0.0.1* ]]; then
  echo "  warning: this zip is for a public web origin but is only ad-hoc signed." >&2
  echo "  Downloaded copies will show: Apple cannot check it for malicious software." >&2
fi

# Confirm the zip still verifies (zipping can strip the signature if ditto is skipped).
VERIFY_DIR="$(mktemp -d)"
ditto -x -k "$ZIP" "$VERIFY_DIR"
codesign --verify --verbose=2 "$VERIFY_DIR/Loom Capture.app"
spctl --assess --type execute -v "$VERIFY_DIR/Loom Capture.app" 2>/dev/null || true
rm -rf "$VERIFY_DIR"

WEB_DOWNLOADS="$ROOT/../web/public/downloads"
mkdir -p "$WEB_DOWNLOADS"
cp "$ZIP" "$WEB_DOWNLOADS/LoomCapture-macos.zip"

# dist/ is deleted on every rebuild, so TCC rows for that path go stale.
# ~/Applications keeps a stable path for Accessibility.
LOCAL_APP="${HOME}/Applications/Loom Capture.app"
mkdir -p "${HOME}/Applications"
rm -rf "$LOCAL_APP"
ditto "$APP" "$LOCAL_APP"
xattr -cr "$LOCAL_APP" 2>/dev/null || true

echo
echo "Packaged: $APP"
echo "Zip:      $ZIP"
echo "Web:      $WEB_DOWNLOADS/LoomCapture-macos.zip"
echo
echo "Launch local (stable TCC path): open \"$LOCAL_APP\""
echo "Then: System Settings → Privacy & Security → Accessibility → remove old Loom Capture rows,"
echo "add ~/Applications/Loom Capture.app, toggle ON, quit, reopen."
echo
echo "People can download it from the website after you rebuild/redeploy the frontend image"
echo "so public/downloads/LoomCapture-macos.zip is included."
echo "  Web UI:  /download"
echo "  Direct:  /downloads/LoomCapture-macos.zip"
