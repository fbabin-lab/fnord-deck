#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -eq 0 ]]; then
  echo 'Run as the desktop user, not root.' >&2
  exit 1
fi
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/streamdeck-linux"
CONFIG_ROOT="${XDG_CONFIG_HOME:-$HOME/.config}"
case "$DATA_ROOT" in /*) ;; *) echo 'XDG_DATA_HOME must be absolute.' >&2; exit 1;; esac
case "$CONFIG_ROOT" in /*) ;; *) echo 'XDG_CONFIG_HOME must be absolute.' >&2; exit 1;; esac
systemctl --user stop sdl-controller.service 2>/dev/null || true
systemctl --user disable sdl-controller.service 2>/dev/null || true
rm -f -- "$CONFIG_ROOT/systemd/user/sdl-controller.service" "$CONFIG_ROOT/autostart/sdl-session.desktop"
for command in sdl-controller sdlctl sdl-session; do
  target="$HOME/.local/bin/$command"
  if [[ -L "$target" && "$(readlink "$target")" == "$DATA_ROOT/app/venv/bin/$command" ]]; then
    rm -f -- "$target"
  fi
done
# The assets directory is a sibling of app, not inside it; preserve all user configuration/data.
rm -rf -- "$DATA_ROOT/app"
systemctl --user daemon-reload 2>/dev/null || true
printf 'Application removed. Configurations, assets and state were preserved.\n'
printf 'Optional USB-rule removal: sudo rm /etc/udev/rules.d/70-streamdeck-linux-xl.rules\n'
printf 'Then run: sudo udevadm control --reload-rules\n'
