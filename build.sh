#!/usr/bin/env bash
# build.sh — build TagWig.app for macOS (Apple Silicon)
set -e

PYTHON="$(dirname "$0")/venv/bin/python3"
SPEC="$(dirname "$0")/TagWig.spec"
DIST="$(dirname "$0")/dist"

echo "==> Cleaning previous build…"
rm -rf "$DIST" "$(dirname "$0")/build"

echo "==> Running PyInstaller…"
"$PYTHON" -m PyInstaller "$SPEC" --noconfirm

echo ""
echo "✅  Done.  App bundle is at:"
echo "    $DIST/TagWig.app"
echo ""
echo "To run: open \"$DIST/TagWig.app\""
