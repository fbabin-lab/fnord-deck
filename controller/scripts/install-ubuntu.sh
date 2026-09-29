#!/usr/bin/env bash
# Run as your normal desktop user. sudo is used only for packages and the USB rule.
set -euo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOSTART=0
SYSTEM_DEPS=1
for argument in "$@"; do
  case "$argument" in
    --autostart) AUTOSTART=1 ;;
    --skip-system-deps) SYSTEM_DEPS=0 ;;
    -h|--help) printf 'Usage: %s [--autostart] [--skip-system-deps]\n' "$0"; exit 0 ;;
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
    raise SystemExit('Python 3.12 or 3.13 is required; Ubuntu 24.04 supplies Python 3.12.')
PY
if [[ "$SYSTEM_DEPS" -eq 1 ]]; then
  sudo apt-get update
  sudo apt-get install -y python3-venv libhidapi-libusb0 libcairo2 fonts-dejavu-core
  sudo install -m 0644 "$ROOT/packaging/udev/70-streamdeck-linux-xl.rules" /etc/udev/rules.d/70-streamdeck-linux-xl.rules
  sudo udevadm control --reload-rules
fi
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/streamdeck-linux"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
PREFIX="$DATA_ROOT/app"
BIN="$HOME/.local/bin"
case "$DATA_ROOT" in /*) ;; *) echo 'XDG_DATA_HOME must be absolute.' >&2; exit 1;; esac
case "$CONFIG_ROOT" in /*) ;; *) echo 'XDG_CONFIG_HOME must be absolute.' >&2; exit 1;; esac
RESTART=0
if systemctl --user is-active --quiet sdl-controller.service 2>/dev/null; then
  RESTART=1
  systemctl --user stop sdl-controller.service
fi
mkdir -p "$PREFIX" "$BIN" "$CONFIG_ROOT/systemd/user"
python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/python" -m pip install 'setuptools==82.0.1'
"$PREFIX/venv/bin/python" -m pip install -r "$ROOT/requirements.lock"
"$PREFIX/venv/bin/python" -m pip install --no-deps --no-build-isolation "$ROOT"
for command in sdl-controller sdlctl sdl-session; do
  if [[ -e "$BIN/$command" || -L "$BIN/$command" ]]; then
    if [[ ! -L "$BIN/$command" || "$(readlink "$BIN/$command")" != "$PREFIX/venv/bin/$command" ]]; then
      echo "Refusing to replace unrelated executable or symlink: $BIN/$command" >&2
      exit 1
    fi
  fi
  ln -sfn "$PREFIX/venv/bin/$command" "$BIN/$command"
done
mkdir -p "$PREFIX/docs" "$PREFIX/examples"
cp -R "$ROOT/docs/." "$PREFIX/docs/"
cp -R "$ROOT/examples/." "$PREFIX/examples/"
ROOT="$ROOT" PREFIX="$PREFIX" CONFIG_ROOT="$CONFIG_ROOT" AUTOSTART="$AUTOSTART" python3 - <<'PY'
import os
from pathlib import Path
root, prefix, config = (Path(os.environ[k]) for k in ('ROOT', 'PREFIX', 'CONFIG_ROOT'))
def escaped(path):
    return str(path).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
unit = (root/'packaging/systemd-user/sdl-controller.service.in').read_text()
unit = unit.replace('@CONTROLLER@', escaped(prefix/'venv/bin/sdl-controller')).replace('@DOCS@', escaped(prefix/'docs'))
(config/'systemd/user/sdl-controller.service').write_text(unit)
(config/'systemd/user/app-sdlplugins.slice').write_text((root/'packaging/systemd-user/app-sdlplugins.slice').read_text())
if os.environ['AUTOSTART'] == '1':
    directory = config/'autostart'
    directory.mkdir(parents=True, exist_ok=True)
    desktop = (root/'packaging/desktop/sdl-session.desktop.in').read_text()
    (directory/'sdl-session.desktop').write_text(desktop.replace('@SESSION@', escaped(prefix/'venv/bin/sdl-session')))
PY
if [[ -d "$ROOT/../plugins/cpu" ]]; then
  "$PREFIX/venv/bin/python" -m sdl_controller.plugins.install "$ROOT/../plugins/cpu"
fi
if systemctl --user daemon-reload 2>/dev/null; then
  if [[ "$RESTART" -eq 1 || ( "$AUTOSTART" -eq 1 && ( -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ) ) ]]; then
    "$PREFIX/venv/bin/sdl-session" --start
  fi
else
  echo 'No systemd user manager in this terminal. Log in graphically before starting the service.'
fi
printf '\nInstalled to %s\n' "$PREFIX"
printf 'Unplug/replug the Stream Deck to apply USB permissions. Stop other Stream Deck controllers.\n'
printf 'For this terminal: export PATH="$HOME/.local/bin:$PATH"\n'
printf 'Start with: sdl-session --start\nCheck with: sdlctl status\n'
if [[ "$AUTOSTART" -eq 0 ]]; then
  echo 'Automatic login startup was not enabled. Re-run with --autostart to opt in.'
fi

printf 'Plugin files are installed without approval. Review them in Configurator → Plugins, or run sdlctl plugins.\n'
