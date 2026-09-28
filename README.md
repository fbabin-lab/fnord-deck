# fnord-deck

Linux controller and configuration application for Elgato Stream Deck hardware.

Current hardware target: **Elgato Stream Deck XL 20GAT9901** on **Ubuntu 24.04**.

The project is intentionally split into two applications:

- `controller/` — owns the Stream Deck USB device, renders keys, handles presses, navigation, and configured application/script execution.
- `configurator/` — desktop editor for layouts, icons, text, nested sections, and button actions. It communicates with the Controller over its local API and does not own the USB device.

The plugin runtime is intentionally deferred. Configuration and code boundaries preserve room for a future plugin mechanism without loading plugins in the current release.

## Install Controller

```bash
cd controller
./scripts/install-ubuntu.sh
export PATH="$HOME/.local/bin:$PATH"
sdl-session --start
sdlctl status
```

## Install Configurator

```bash
cd configurator
./scripts/install-ubuntu.sh
export PATH="$HOME/.local/bin:$PATH"
sdl-configurator
```

Run installers as the normal desktop user. They request elevated privileges only for required Ubuntu packages and, for the Controller, USB permission setup.

## Development

Each application is independently packaged and tested. See the README and documentation within each directory.

## License

GPL-3.0-only. See `LICENSE`.
