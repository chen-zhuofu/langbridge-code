#!/bin/bash
set -euo pipefail

DESKTOP_ROOT="$(cd "$(dirname "$0")" && pwd)"
bash "$DESKTOP_ROOT/build-app.sh"
USER_DESKTOP="$(osascript -e 'POSIX path of (path to desktop folder)')"
open "${USER_DESKTOP%/}/LangBridge.app"
