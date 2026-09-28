#!/usr/bin/env bash
# Installs ONLY the Configurator. Does not modify Controller services or USB rules.
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEM_DEPS=1
for argument in "$@"; do
  case "$argument" in
    --skip-system-deps) SYSTEM_DEPS=0 ;;
    -h|--help) printf 'Usage: %s [--skip-system-deps]\n' "$0"; exit 0 ;;
    *) printf 'Unknown argument: %s\n' "$argument" >&2; exit 2 ;;
  esac
done
if [[ "$EUID" -eq 0 ]]; then
  echo 'Run this installer as your desktop user, not with sudo.' >&2
  exit 1
fi
python3 - <<'PY'
import sys
if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
    raise SystemExit('Python 3.12 or 3.13 is required. Ubuntu 24.04 provides Python 3.12.')
PY
DATA_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
case "$DATA_BASE" in /*) ;; *) echo 'XDG_DATA_HOME must be absolute.' >&2; exit 1 ;; esac
PREFIX="$DATA_BASE/streamdeck-linux-configurator/app"
BIN="$HOME/.local/bin"
DESKTOP="$DATA_BASE/applications/streamdeck-configurator.desktop"
if [[ -e "$BIN/sdl-configurator" || -L "$BIN/sdl-configurator" ]]; then
  if [[ ! -L "$BIN/sdl-configurator" || "$(readlink "$BIN/sdl-configurator")" != "$PREFIX/venv/bin/sdl-configurator" ]]; then
    echo "Refusing to overwrite unrelated file: $BIN/sdl-configurator" >&2; exit 1
  fi
fi
if [[ -e "$DESKTOP" ]] && ! grep -q '^# Managed by Stream Deck Linux Configurator installer$' "$DESKTOP"; then
  echo "Refusing to overwrite unrelated desktop entry: $DESKTOP" >&2; exit 1
fi
if [[ "$SYSTEM_DEPS" -eq 1 ]]; then
  sudo apt-get update
  sudo apt-get install -y python3-venv libcairo2 fonts-dejavu-core \
    libgl1 libegl1 libdbus-1-3 libxkbcommon0 libxkbcommon-x11-0 \
    libfontconfig1 libfreetype6 libxcb-util1 libsm6 libice6 libxrender1 libx11-xcb1 \
    libxcb1 libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 \
    libxcb-render-util0 libxcb-randr0 libxcb-shape0 libxcb-xfixes0 \
    libxcb-sync1 libxcb-render0 libxcb-xinerama0 \
    libwayland-client0 libwayland-cursor0 libwayland-egl1
fi
mkdir -p "$PREFIX" "$BIN" "$(dirname "$DESKTOP")"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install 'setuptools==82.0.1'
"$PREFIX/venv/bin/python" -m pip install -r "$ROOT/requirements.lock"
"$PREFIX/venv/bin/python" -m pip install --no-deps --no-build-isolation "$ROOT"
ln -sfn "$PREFIX/venv/bin/sdl-configurator" "$BIN/sdl-configurator"
mkdir -p "$PREFIX/docs" "$PREFIX/examples"
cp -R "$ROOT/docs/." "$PREFIX/docs/"
cp -R "$ROOT/examples/." "$PREFIX/examples/"
PREFIX="$PREFIX" DESKTOP="$DESKTOP" python3 - <<'PY'
import os
from pathlib import Path
prefix, desktop = (Path(os.environ[k]) for k in ('PREFIX', 'DESKTOP'))
executable = str(prefix/'venv/bin/sdl-configurator')
# Desktop Exec quoting, not a shell command. %f is an optional local file argument.
for char, replacement in [('\\','\\\\'), ('"','\\"'), ('`','\\`'), ('$','\\$'), ('%','%%')]:
    executable = executable.replace(char, replacement)
text = f'''# Managed by Stream Deck Linux Configurator installer
[Desktop Entry]
Type=Application
Version=1.0
Name=Stream Deck Configurator
Name[fr]=Configurateur Stream Deck
Comment=Configure Stream Deck buttons and sections
Comment[fr]=Configurer les boutons et sections du Stream Deck
Exec="{executable}" %f
Icon=input-keyboard
Terminal=false
Categories=Utility;
StartupNotify=true
'''
desktop.write_text(text)
desktop.chmod(0o644)
PY
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$(dirname "$DESKTOP")" >/dev/null 2>&1 || true
fi
"$PREFIX/venv/bin/sdl-configurator" --check
printf '\nInstalled the Configurator to %s\n' "$PREFIX"
printf 'Launch from Applications → Stream Deck Configurator, or:\n  %s/sdl-configurator\n' "$BIN"
printf 'The existing Controller and its service were not changed or restarted.\n'
printf 'To make the short command available in this terminal:\n  export PATH="$HOME/.local/bin:$PATH"\n'
