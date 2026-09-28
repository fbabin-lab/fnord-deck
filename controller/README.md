# Stream Deck Linux Controller — 0.1.0

Standalone Controller for **Ubuntu 24.04 LTS** and **Elgato Stream Deck XL 20GAT9901** (USB `0fd9:006c`, 32 keys). It owns the USB connection, draws icon/text buttons, navigates nested sections and launches explicitly configured programs.

**This release does not include the graphical Configurator or a plugin host.** A separate `sdlctl` command-line client edits/applies JSON configurations through the same local API that the future Configurator will use. Plugin configuration fields are preserved but plugin code is never loaded.

## Install on Ubuntu

Run as your ordinary desktop user, **not** with `sudo`:

```bash
./scripts/install-ubuntu.sh
export PATH="$HOME/.local/bin:$PATH"
```

The installer requests `sudo` only for Ubuntu packages and a narrowly scoped USB permission rule. It installs a private Python virtual environment, command entry points, and a systemd **user** service. It does not enable login startup unless you pass `--autostart`.

Unplug and reconnect the Stream Deck after installing the rule. Stop any other application controlling the deck. Then, from a terminal in your graphical desktop session:

```bash
sdl-session --start
sdlctl status
sdlctl apply examples/demo.json
sdlctl select-device
```

A new installation initially displays an empty safe deck. Applying `demo.json` displays a three-level demonstration without executing any programs. Key 0 opens **Tools**; its key 1 opens **More**. Every child section has generated **Back** at key 0. **Home** returns directly to root. Key indexes are zero-based and row-major.

`select-device` stores the connected device's serial in the applied configuration. Before an explicit selection, a single matching deck can be connected and its first serial is remembered locally; multiple matching devices require selection. A missing selected device is not replaced with another automatically.

For optional automatic startup at graphical login:

```bash
./scripts/install-ubuntu.sh --autostart
```

## Configure buttons without the future GUI

```bash
sdlctl get --output my-deck.json
# Edit my-deck.json in your preferred editor.
sdlctl validate my-deck.json
sdlctl apply my-deck.json
sdlctl preview "$HOME/streamdeck-preview.png"
```

The document schema is in `schemas/configuration-v1.schema.json`. Icon/text rendering, paths, nested pages, and action settings are explained in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

Import an image and use the returned `assetId` as the button's `appearance.iconAssetId`:

```bash
sdlctl asset-import /absolute/path/to/icon.png
```

Supported inputs: PNG, JPEG, WebP, BMP, first-frame GIF, and a restricted self-contained SVG subset. Original files are stored by content hash; filenames/extensions do not determine decoding. Deleting the originally selected image does not break the stored asset.

To try an explicitly reviewed task:

```bash
sdlctl review examples/execute-demo.json
sdlctl apply examples/execute-demo.json --approve-executables
# Physically press and release key 1 (Test task).
sdlctl executions --include-output
```

The example runs `/usr/bin/printf` only when deliberately activated. Apply, startup, reconnect, preview and navigation do not run it. Without `--approve-executables`, an interactive terminal asks for confirmation; noninteractive use fails rather than silently approving commands. The `test BUTTON_UUID --yes` command deliberately executes an approved button without a physical click.

## Simulator — no hardware required

In one terminal:

```bash
sdl-controller --simulate
```

In another:

```bash
sdlctl --simulator apply examples/demo.json
sdlctl --simulator status
sdlctl --simulator click 0
sdlctl --simulator preview "$HOME/streamdeck-simulator.png"
sdlctl --simulator click 0
```

Simulator configuration, socket, assets and history are separate from the real Controller. **Real execution is disabled in the simulator** unless its process is explicitly started with `--allow-execution`. There is no network listener or browser server. `press`, `release`, `disconnect` and `reconnect` are simulator-only test controls.

## Daily operation

```bash
sdlctl pause
sdlctl resume
sdlctl brightness 40
sdlctl devices
sdlctl retry
sdlctl config-history
sdlctl executions
sdlctl doctor
sdlctl doctor --offline
journalctl --user -u sdl-controller.service -n 100 --no-pager
systemctl --user stop sdl-controller.service
```

Pause blocks activations; it is **not** an authentication feature and does not automatically follow screen locking. Closing a CLI/editor does not stop the Controller. Task-mode processes are cleaned up when it stops; application-mode processes belong to independent user-service units and are intended to survive a Controller restart.

## Backup and restore drafts

```bash
sdlctl export "$HOME/my-deck.sdl.zip"
sdlctl import "$HOME/my-deck.sdl.zip" "$HOME/imported-deck.json"
# Import does not Apply. Review program paths before enabling actions:
sdlctl apply "$HOME/imported-deck.json"
```

Bundles contain configuration and original images, not installed plugins, approvals, session variables or execution output. Import creates fresh identities and clears device/secret bindings so local approvals are not inherited from an archive.

## Development and verification

On Ubuntu, after installing the system packages described in the installation document:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock
./scripts/test.sh
```

An optional `Jenkinsfile` is included for self-hosted CI; no GitHub Actions are required. Tests use temporary state and synthetic scripts, not your programs or home files.

**Verification boundary:** tests were executed in the supplied Debian/Python 3.13 environment, not on a physical Stream Deck or an Ubuntu 24.04 desktop. Python 3.12 syntax was checked separately. The real HID library/backend, clean Ubuntu installation, user-service application survival, seat permissions and physical mapping/orientation require the local checks in [docs/HARDWARE-ACCEPTANCE.md](docs/HARDWARE-ACCEPTANCE.md). Do not interpret simulator success as physical hardware certification.

## Documentation

- [Installation and operations](docs/INSTALLATION.md)
- [Configuration and examples](docs/CONFIGURATION.md)
- [Architecture and future plugin seam](docs/ARCHITECTURE.md)
- [Local API](docs/LOCAL_API.md)
- [Security boundaries](docs/SECURITY.md)
- [Verification evidence](docs/VERIFICATION.md)
- [Scope/deviations and remaining work](docs/REQUIREMENTS.md)
- [Dependency/license inventory and primary references](docs/DEPENDENCIES.md)

Uninstall with `./scripts/uninstall.sh`. Configurations, assets and runtime history are preserved; the script does not delete user data or remove Ubuntu packages.
