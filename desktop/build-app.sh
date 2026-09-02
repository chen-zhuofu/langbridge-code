#!/bin/bash
set -euo pipefail

DESKTOP_ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$DESKTOP_ROOT/.." && pwd)"
BUILD_ROOT="$DESKTOP_ROOT/.build"
OUTPUT_ROOT="$DESKTOP_ROOT/dist"
APP_PATH="$OUTPUT_ROOT/LangBridge.app"
CONTENTS_PATH="$APP_PATH/Contents"
APP_BINARY="LangBridgeDesktop"
NOTIFIER_BINARY="LangBridgeNotifier"
CREDENTIAL_HELPER_BINARY="LangBridgeCredentialHelper"
MODULE_CACHE="$BUILD_ROOT/module-cache"

if [[ ! -f "$REPO_ROOT/pyproject.toml" || ! -f "$REPO_ROOT/src/ui/bridge.py" ]]; then
    echo "Run this script from a LangBridge checkout." >&2
    exit 1
fi

if command -v uv >/dev/null 2>&1; then
    echo "Preparing the LangBridge Python environment…"
    # Keep optional developer/eval packages already present in the repo venv.
    uv sync --project "$REPO_ROOT" --inexact
elif [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
        echo "Python environment missing. Install uv, then run: uv sync" >&2
        exit 1
fi

echo "Preparing Playwright Chromium…"
"$REPO_ROOT/.venv/bin/python" -m playwright install chromium

mkdir -p "$BUILD_ROOT" "$OUTPUT_ROOT" "$MODULE_CACHE"

echo "Building the native macOS client…"
CLANG_MODULE_CACHE_PATH="$MODULE_CACHE/clang" \
SWIFT_MODULECACHE_PATH="$MODULE_CACHE/swift" \
swift build \
    --disable-sandbox \
    -c release \
    --package-path "$DESKTOP_ROOT" \
    --scratch-path "$BUILD_ROOT"

BIN_DIR="$(CLANG_MODULE_CACHE_PATH="$MODULE_CACHE/clang" SWIFT_MODULECACHE_PATH="$MODULE_CACHE/swift" swift build --disable-sandbox -c release --package-path "$DESKTOP_ROOT" --scratch-path "$BUILD_ROOT" --show-bin-path)"
BINARY_PATH="$BIN_DIR/$APP_BINARY"
NOTIFIER_PATH="$BIN_DIR/$NOTIFIER_BINARY"
CREDENTIAL_HELPER_PATH="$BIN_DIR/$CREDENTIAL_HELPER_BINARY"
NOTIFIER_APP="$CONTENTS_PATH/Helpers/LangBridgeNotifier.app"
NOTIFIER_CONTENTS="$NOTIFIER_APP/Contents"
RESOURCE_BUNDLE="$BIN_DIR/LangBridgeDesktop_LangBridgeDesktop.bundle"

if [[ ! -x "$BINARY_PATH" ]]; then
    echo "Built executable not found: $BINARY_PATH" >&2
    exit 1
fi
if [[ ! -x "$NOTIFIER_PATH" ]]; then
    echo "Built notifier not found: $NOTIFIER_PATH" >&2
    exit 1
fi
if [[ ! -x "$CREDENTIAL_HELPER_PATH" ]]; then
    echo "Built credential helper not found: $CREDENTIAL_HELPER_PATH" >&2
    exit 1
fi
if [[ ! -d "$RESOURCE_BUNDLE" ]]; then
    echo "Built resource bundle not found: $RESOURCE_BUNDLE" >&2
    exit 1
fi

if [[ -d "$APP_PATH" ]]; then
    rm -rf "$APP_PATH"
fi
mkdir -p "$CONTENTS_PATH/MacOS" "$CONTENTS_PATH/Resources" "$NOTIFIER_CONTENTS/MacOS"
cp "$BINARY_PATH" "$CONTENTS_PATH/MacOS/$APP_BINARY"
cp "$NOTIFIER_PATH" "$NOTIFIER_CONTENTS/MacOS/$NOTIFIER_BINARY"
cp "$CREDENTIAL_HELPER_PATH" "$CONTENTS_PATH/Helpers/$CREDENTIAL_HELPER_BINARY"
cp -R "$RESOURCE_BUNDLE" "$CONTENTS_PATH/Resources/"
printf '%s\n' "$REPO_ROOT" > "$CONTENTS_PATH/Resources/repo-root.txt"

cat > "$CONTENTS_PATH/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>LangBridge</string>
    <key>CFBundleExecutable</key>
    <string>LangBridgeDesktop</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.langbridge.app</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>LangBridge</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSApplicationCategoryType</key>
    <string>public.app-category.developer-tools</string>
    <key>LSMinimumSystemVersion</key>
    <string>14.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSPrincipalClass</key>
    <string>NSApplication</string>
</dict>
</plist>
PLIST

cat > "$NOTIFIER_CONTENTS/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>LangBridge Notifications</string>
    <key>CFBundleExecutable</key>
    <string>LangBridgeNotifier</string>
    <key>CFBundleIdentifier</key>
    <string>com.langbridge.app.notifier</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>LangBridge Notifications</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
PLIST

CLANG_MODULE_CACHE_PATH="$MODULE_CACHE/clang" \
SWIFT_MODULECACHE_PATH="$MODULE_CACHE/swift" \
swift "$DESKTOP_ROOT/Scripts/GenerateIcon.swift" \
    "$DESKTOP_ROOT/Sources/LangBridgeDesktop/Resources/LangbridgeMark.svg" \
    "$CONTENTS_PATH/Resources/AppIcon.icns" \
    white

chmod +x "$CONTENTS_PATH/MacOS/$APP_BINARY"
chmod +x "$NOTIFIER_CONTENTS/MacOS/$NOTIFIER_BINARY"
chmod +x "$CONTENTS_PATH/Helpers/$CREDENTIAL_HELPER_BINARY"
plutil -lint "$CONTENTS_PATH/Info.plist" >/dev/null
plutil -lint "$NOTIFIER_CONTENTS/Info.plist" >/dev/null
xattr -cr "$APP_PATH"
codesign --force --sign - "$NOTIFIER_APP"
codesign --force --deep --sign - "$APP_PATH"
codesign --verify --deep --strict "$APP_PATH"

USER_DESKTOP="$(osascript -e 'POSIX path of (path to desktop folder)')"
DESKTOP_APP_PATH="${USER_DESKTOP%/}/LangBridge.app"
case "$DESKTOP_APP_PATH" in
    */Desktop/LangBridge.app) ;;
    *)
        echo "Refusing unexpected desktop install path: $DESKTOP_APP_PATH" >&2
        exit 1
        ;;
esac
INSTALL_STAGING="$(mktemp -d "${TMPDIR:-/tmp}/langbridge-install.XXXXXX")"
trap 'rm -rf "$INSTALL_STAGING"' EXIT
ditto "$APP_PATH" "$INSTALL_STAGING/LangBridge.app"
if [[ -e "$DESKTOP_APP_PATH" ]]; then
    rm -rf "$DESKTOP_APP_PATH"
fi
mv "$INSTALL_STAGING/LangBridge.app" "$DESKTOP_APP_PATH"
codesign --verify --deep --strict "$DESKTOP_APP_PATH"

echo
echo "Built: $APP_PATH"
echo "Installed: $DESKTOP_APP_PATH"
echo "Open it with: open \"$DESKTOP_APP_PATH\""
