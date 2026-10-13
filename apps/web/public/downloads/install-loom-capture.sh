#!/usr/bin/env bash
# Install Loom Capture from this site without Finder's quarantine bit.
# Browser-downloaded .app zips are blocked by Gatekeeper until Apple notarizes
# them. Files fetched with curl in Terminal are not, after xattr is cleared.
#
# Usage (from the website):
#   curl -fsSL https://YOUR_HOST/downloads/install-loom-capture.sh | bash -s -- https://YOUR_HOST
# Optional second argument: absolute zip URL if it is not on the same host.
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Loom Capture is a macOS app. Run this on a Mac." >&2
  exit 1
fi

ORIGIN="${1:-}"
ZIP_URL="${2:-${LOOM_CAPTURE_ZIP_URL:-}}"
if [[ -z "$ZIP_URL" && -n "$ORIGIN" ]]; then
  ZIP_URL="${ORIGIN%/}/downloads/LoomCapture-macos.zip"
fi
if [[ -z "$ZIP_URL" ]]; then
  echo "Usage: curl -fsSL https://<host>/downloads/install-loom-capture.sh | bash -s -- https://<host>" >&2
  exit 1
fi
if [[ "$ZIP_URL" != http://* && "$ZIP_URL" != https://* ]]; then
  if [[ -z "$ORIGIN" ]]; then
    echo "error: relative zip URL requires the site origin as the first argument" >&2
    exit 1
  fi
  ZIP_URL="${ORIGIN%/}/${ZIP_URL#/}"
fi

DEST="${HOME}/Applications"
mkdir -p "$DEST"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading Loom Capture from $ZIP_URL"
curl -fL --retry 3 -o "$TMP/LoomCapture-macos.zip" "$ZIP_URL"
ditto -x -k "$TMP/LoomCapture-macos.zip" "$TMP"
APP="$(find "$TMP" -name "Loom Capture*.app" -maxdepth 3 -print | head -n 1 || true)"
if [[ -z "$APP" ]]; then
  echo "error: the zip did not contain Loom Capture.app" >&2
  exit 1
fi

xattr -cr "$TMP" 2>/dev/null || true
rm -rf "${DEST}/Loom Capture.app"
ditto "$APP" "${DEST}/Loom Capture.app"
xattr -cr "${DEST}/Loom Capture.app" 2>/dev/null || true

echo "Installed ${DEST}/Loom Capture.app"
open "${DEST}/Loom Capture.app"
echo "If Accessibility is requested: enable Loom Capture, quit, and reopen."
