# fnord-deck

**Controller and Configurator 0.2.0** for the **Elgato Stream Deck XL 20GAT9901**, targeting **Ubuntu 24.04**.

Two independent applications: `controller/` owns USB, rendering, navigation and configured actions; `configurator/` edits drafts and applies them over a local API. Closing the editor leaves the Controller running.

Version 0.2 adds a **visibility-aware, out-of-process display plugin host** and a **CPU usage plugin**. Only applied buttons on the current visible page create demand. Hidden instances do not sample. Workers stop when their last visible instance disappears. Refresh floors, shared batching, deduplication, bounded communication and a fair 8-key-writes/second dynamic output budget prevent refresh storms. The default production launcher requires enforced systemd/cgroup resource limits; it never silently falls back to unrestricted execution.

Native plugins are trusted same-user code, **not a security sandbox**. Installing files is separate from explicit package approval, button configuration, and Apply. No plugin code runs inside the Configurator. Ordinary editing/preview does not create plugin demand. Plugin button commands, push notifications, secret provisioning and a plugin marketplace are reserved for later; this release implements polling display contributions.

## Install or update both applications

From the repository root, as your normal desktop user (not sudo):

```bash
./controller/scripts/install-ubuntu.sh
./configurator/scripts/install-ubuntu.sh
export PATH="$HOME/.local/bin:$PATH"
systemctl --user restart sdl-controller.service
sdl-session --start
sdl-configurator
```

A fresh Controller install may require unplugging/reconnecting the deck for its USB rule. Stop other Stream Deck controllers. The Controller installer installs the bundled CPU 0.2.0 package **without approving or assigning it**. Existing configurations and drafts are retained; installing alone does not execute configured actions.

In the Configurator, use **Plugins…** to review and approve CPU usage. Select a button, choose **Plugin display…**, configure aggregate or logical-CPU usage, then **Review & apply…**. The plugin defaults to one sample every two seconds, with a one-second minimum. A button may retain its existing application/script action independently of the metric display.

Read [the 0.2.0 plugin guide](docs/PLUGIN-HOST-0.2.0.md) for setup, diagnostics, lifecycle, resource budgets, API methods and limitations. The [AI implementation specification](docs/plugin-system-v1.1.md) includes later extension requirements; the guide distinguishes what this release implements.

## Verification and development

[Current verification](docs/VERIFICATION-0.2.0.md) separates executed tests from Ubuntu/systemd/Qt/physical acceptance. Historical reports in application subdirectories describe the earlier release, not proof of current hardware validation.

Each application is independently packaged. Run `./scripts/test-all.sh` in a development environment containing the locked dependencies. Set `QT_QPA_PLATFORM=offscreen` for headless Qt tests. Shared `sdl_core` copies must stay byte-identical; the Configurator source checker verifies their recorded provenance.

## License

GPL-3.0-only; see [LICENSE](LICENSE). No fonts are bundled.
