#!/usr/bin/env bash
set -euo pipefail
if [[ "$EUID" -eq 0 ]]; then echo 'Run as your desktop user, not root.' >&2; exit 1; fi
DATA_BASE="${XDG_DATA_HOME:-$HOME/.local/share}"
case "$DATA_BASE" in /*) ;; *) exit 1 ;; esac
PREFIX="$DATA_BASE/streamdeck-linux-configurator/app"
BIN="$HOME/.local/bin/sdl-configurator"
DESKTOP="$DATA_BASE/applications/streamdeck-configurator.desktop"
if [[ -L "$BIN" && "$(readlink "$BIN")" == "$PREFIX/venv/bin/sdl-configurator" ]]; then rm -- "$BIN"; fi
if [[ -f "$DESKTOP" ]] && grep -q '^# Managed by Stream Deck Linux Configurator installer$' "$DESKTOP"; then rm -- "$DESKTOP"; fi
rm -rf -- "$PREFIX"
echo 'Configurator application removed. Drafts, images, and Controller files were retained.'
