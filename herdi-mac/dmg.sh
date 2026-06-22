#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Herdi"
DMG_NAME="Herdi"
VERSION="0.3.2"
APP_DIR="$SCRIPT_DIR/dist/$APP_NAME.app"
DMG_DIR="$SCRIPT_DIR/dist/dmg"
DMG_PATH="$SCRIPT_DIR/dist/$DMG_NAME-$VERSION.dmg"

# Build first if needed
if [ ! -d "$APP_DIR" ]; then
    echo "▸ App not found, building..."
    bash "$SCRIPT_DIR/build.sh"
fi

echo "▸ Creating DMG..."
rm -rf "$DMG_DIR"
mkdir -p "$DMG_DIR"
cp -r "$APP_DIR" "$DMG_DIR/"

# Create symlink to Applications
ln -s /Applications "$DMG_DIR/Applications"

# Create DMG
rm -f "$DMG_PATH"
hdiutil create -volname "$DMG_NAME" \
    -srcfolder "$DMG_DIR" \
    -ov -format UDZO \
    "$DMG_PATH"

rm -rf "$DMG_DIR"
echo "✓ DMG: $DMG_PATH"
echo "  Size: $(du -h "$DMG_PATH" | cut -f1)"
