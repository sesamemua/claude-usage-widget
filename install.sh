#!/usr/bin/env bash
# Claude Usage Widget — installer
# Builds a macOS .app bundle and copies it into /Applications.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Claude Usage"
APP_BUNDLE="/Applications/${APP_NAME}.app"
PYTHON_BIN="$(command -v python3)"

if [[ -z "${PYTHON_BIN}" ]]; then
    echo "Error: python3 not found in PATH." >&2
    exit 1
fi

echo "==> Installing Python dependencies"
"${PYTHON_BIN}" -m pip install --upgrade pip
"${PYTHON_BIN}" -m pip install --upgrade pyobjc-framework-WebKit pyobjc-framework-Cocoa

echo "==> Removing any existing ${APP_NAME}.app"
rm -rf "${APP_BUNDLE}"

echo "==> Building app bundle"
mkdir -p "${APP_BUNDLE}/Contents/MacOS"
mkdir -p "${APP_BUNDLE}/Contents/Resources"

cp "${REPO_DIR}/claude_usage_widget.py" \
   "${APP_BUNDLE}/Contents/Resources/claude_usage_widget.py"

# Optional icon
if [[ -f "${REPO_DIR}/AppIcon.icns" ]]; then
    cp "${REPO_DIR}/AppIcon.icns" \
       "${APP_BUNDLE}/Contents/Resources/AppIcon.icns"
fi

cat > "${APP_BUNDLE}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>${APP_NAME}</string>
    <key>CFBundleDisplayName</key><string>${APP_NAME}</string>
    <key>CFBundleIdentifier</key><string>com.claude.usage-widget</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleShortVersionString</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>LSUIElement</key><true/>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

cat > "${APP_BUNDLE}/Contents/MacOS/launcher" <<EOF
#!/bin/bash
DIR="\$(cd "\$(dirname "\$0")/../Resources" && pwd)"
exec ${PYTHON_BIN} "\$DIR/claude_usage_widget.py"
EOF
chmod +x "${APP_BUNDLE}/Contents/MacOS/launcher"

echo "==> Touching to refresh Finder cache"
touch "${APP_BUNDLE}"

echo "==> Done. Launching ${APP_NAME}"
open -a "${APP_NAME}"

echo
echo "Installed to ${APP_BUNDLE}"
echo "You can also run it from Spotlight, Launchpad, or Finder → Applications."
