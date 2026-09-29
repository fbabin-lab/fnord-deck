> Version 0.2.0 adds approved polling/display plugins and API 1.1. The [current plugin guide](../../docs/PLUGIN-HOST-0.2.0.md) supersedes earlier plugin-deferred statements below. Run both this document's baseline acceptance checks and the plugin guide's additional checks.

# Ubuntu 24.04 installation and operation

## Prerequisites and installation

Target: an ordinary logged-in Ubuntu 24.04 desktop user, Python 3.12, a local graphical session, and Stream Deck XL model 20GAT9901. The Controller does not require Docker, Node, a database, Elgato's software or a GUI toolkit.

Run `./scripts/install-ubuntu.sh` as that user. The privileged phase installs `python3-venv`, `libhidapi-libusb0`, `libcairo2`, `fonts-dejavu-core`, and the supplied USB rule. Network access is needed by apt/pip at installation, but not by normal Controller rendering/navigation.

The unprivileged phase creates `${XDG_DATA_HOME:-~/.local/share}/streamdeck-linux/app/venv`, installs the exact runtime versions from `requirements.lock`, installs this package, links `sdl-controller`, `sdlctl`, `sdl-session` under `~/.local/bin`, and writes a user unit under `${XDG_CONFIG_HOME:-~/.config}/systemd/user/`. Repeat installation updates the application without replacing configurations/assets. An already running user service is stopped for upgrade and restarted afterward; running tasks stop during that upgrade. Do not upgrade over a separately running foreground Controller.

The installer refuses root execution. `--skip-system-deps` is for administrators who have already provided the packages and USB rule; it is not an alternative to obtaining the required permissions.

## USB permission rule

The only rule matches USB vendor `0fd9`, product `006c`, `DEVTYPE=usb_device`, with `MODE=0660` and `TAG+=uaccess`. It is named `70-streamdeck-linux-xl.rules` so tagging precedes the usual seat permission processing. The adapter explicitly selects the library's **`libusb`** backend, so access is to the USB device node, not an unrelated hidraw node.

Unplug/replug after installing/reloading the rule. Active-seat access normally requires a local active graphical login; SSH-only/headless use may require an administrator-managed dedicated group rule instead. This release does not broaden the rule automatically or add world-writable mode `0666`.

Stop competing Stream Deck applications manually. The Controller does not terminate them or force-release their USB claims. Only exact `0fd9:006c` devices are selected; another XL revision's product ID is intentionally not accepted.

## Start and stop

From a terminal belonging to the actual graphical session:

```bash
export PATH="$HOME/.local/bin:$PATH"
sdl-session --start
sdlctl status
systemctl --user status sdl-controller.service
systemctl --user stop sdl-controller.service
```

`sdl-session` imports a fixed allowlist of actual session variables into the user manager and sends the same context to the Controller; it does not guess `DISPLAY=:0`. No environment values are printed or logged.

For login startup, rerun installation with `--autostart`. This creates a user XDG autostart desktop entry that calls `sdl-session --start`; it does not enable lingering or system boot startup. The unit is tied to `graphical-session.target`. A manual `systemctl --user start` without session import can leave GUI launch context incomplete.

Foreground operation is also supported: stop the user service first, then run `sdl-controller` in a graphical terminal. Ctrl+C requests orderly shutdown. Only one physical Controller instance per UID can own the process lock/socket.

## Diagnostics

```bash
sdlctl doctor --offline
sdlctl doctor
sdlctl devices
sdlctl retry
lsusb -d 0fd9:006c
journalctl --user -u sdl-controller.service -n 100 --no-pager
```

`doctor --offline` does not need a running Controller or open USB. It reports Python/package versions, system font path, HID library resolution and Pillow codecs. Missing library/permission/busy states appear separately when recognizable from the backend error; ambiguous backend errors remain `DEVICE_IO_ERROR` rather than being falsely classified.

A preview is an upright logical rendering, not proof that the physical image encoding/orientation is correct. If no buttons light up, inspect `device.state`, `device.error`, `lastError`, `recovery`, and `deviceSynchronized` in `sdlctl status`.

## State locations

All app-owned directories are private (`0700`); files created by the Controller use a `0077` umask.

| Base | Controller contents |
|---|---|
| `$XDG_CONFIG_HOME/streamdeck-linux` | Current pointer, retained revision documents |
| `$XDG_DATA_HOME/streamdeck-linux/assets` | Original images, canonical PNGs, metadata |
| `$XDG_DATA_HOME/streamdeck-linux/app` | Installed virtual environment and documentation |
| `$XDG_STATE_HOME/streamdeck-linux` | Execution intent/outcome history, selected-device pin, bounded logs |
| `$XDG_CACHE_HOME/streamdeck-linux` | Reserved for future regenerable disk caches |
| `$XDG_RUNTIME_DIR/streamdeck-linux` | Unix socket, process lock, uploads and private launch requests |

The simulator uses `streamdeck-linux-simulator` instead, including its socket and state. It can run alongside the physical Controller without opening USB.

An absent/unsafe `XDG_RUNTIME_DIR` is an error. The program does not fall back to a public `/tmp/controller.sock`. A legitimate login session normally supplies the directory.

## Recovery

The Controller retains up to 100 revision documents and Apply outcomes. A failed pointer write leaves the previous active revision intact. Corrupt current data is preserved and an empty, non-executing configuration is used. List history, inspect a valid revision with `sdlctl get --revision N --output recovered.json`, then review/apply it as a **new** revision.

Execution history damage disables further execution rather than discarding uncertain intents. Stop the Controller, preserve a copy of the damaged `executions.json`, move it aside, and restart only after reviewing the uncertainty. No script is automatically rerun. Task output buffers are memory-only and are not restored after restart.

## Uninstall

Run `./scripts/uninstall.sh` as the desktop user. It stops/disables the service, removes this application's entry points/unit/autostart file/venv, and preserves configurations, assets, history and logs. It prints the optional command to remove the narrowly named udev rule. It does not remove shared Ubuntu packages or erase user data.
