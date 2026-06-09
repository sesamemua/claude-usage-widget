#!/usr/bin/env bash
# Claude Usage Widget — installer
# Builds a macOS .app bundle with a self-contained virtualenv and copies it
# into /Applications. The venv lives inside the bundle so the launcher never
# depends on a Python path that might later be uninstalled.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Claude Usage"
APP_BUNDLE="/Applications/${APP_NAME}.app"

# Find a working python3 to seed the venv from. We try a few candidates and
# verify each one actually runs (a bare `command -v` can resolve to a dangling
# symlink left behind by an uninstalled Python).
find_python() {
    for cand in python3.13 python3.12 python3.11 python3 /usr/bin/python3; do
        local p
        p="$(command -v "${cand}" 2>/dev/null || true)"
        if [[ -n "${p}" ]] && "${p}" -c 'import sys' >/dev/null 2>&1; then
            echo "${p}"
            return 0
        fi
    done
    return 1
}

PYTHON_BIN="$(find_python)" || {
    echo "Error: no working python3 found in PATH." >&2
    exit 1
}
echo "==> Using base Python: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version 2>&1))"

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

echo "==> Creating self-contained virtualenv (inside the bundle)"
VENV="${APP_BUNDLE}/Contents/Resources/venv"
"${PYTHON_BIN}" -m venv "${VENV}"
"${VENV}/bin/python" -m pip install --upgrade pip >/dev/null
echo "==> Installing Python dependencies into the venv"
"${VENV}/bin/python" -m pip install pyobjc-framework-WebKit pyobjc-framework-Cocoa

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

# The launcher resolves Python relative to its own location, so the path is
# always valid as long as the bundle exists — no absolute system path to break.
cat > "${APP_BUNDLE}/Contents/MacOS/launcher" <<'EOF'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
exec "$DIR/venv/bin/python" "$DIR/claude_usage_widget.py"
EOF
chmod +x "${APP_BUNDLE}/Contents/MacOS/launcher"

echo "==> Touching to refresh Finder cache"
touch "${APP_BUNDLE}"

echo "==> Done. Launching ${APP_NAME}"
open -a "${APP_NAME}"

echo
echo "Installed to ${APP_BUNDLE}"
echo "You can also run it from Spotlight, Launchpad, or Finder → Applications."
