# Stream Deck Linux Configurator 0.2.0

A separate desktop application for configuring the **Elgato Stream Deck XL 20GAT9901** on **Ubuntu 24.04**. Compatible with the supplied **Stream Deck Linux Controller 0.2.0 / API 1.1**.

The Configurator edits local drafts and talks to the Controller over a same-user Unix socket. **It never opens USB devices, starts/stops the Controller, or directly executes button commands.** Closing it leaves the Controller running.

**Current release:** [plugin host guide](../docs/PLUGIN-HOST-0.2.0.md) and [verification](../docs/VERIFICATION-0.2.0.md). The older report under `docs/VERIFICATION.md` is historical. Native plugin workers belong exclusively to the Controller; the editor provides package review, per-button settings, offline cached metadata and draft-only previews.

## Install on Ubuntu

Update **both** applications to 0.2.0 for plugin support. From the repository root, run the installer **as your normal desktop user, without sudo**:

```bash
./controller/scripts/install-ubuntu.sh
./configurator/scripts/install-ubuntu.sh
export PATH="$HOME/.local/bin:$PATH"
sdl-configurator
```

The installer requests sudo only for Ubuntu packages. It installs Python/Qt dependencies into a **Configurator-only virtual environment** and adds **Stream Deck Configurator** to the Applications menu. Internet access is required during dependency installation. It does not change the Controller service, its configuration, its Python environment, or USB permission rules.

Start the existing Controller from a terminal in your graphical login session when hardware access is needed:

```bash
sdl-session --start
```

The Configurator works offline when the Controller is unavailable. On the first launch with no recovered local draft, it attempts to load the Controller configuration. A recovered draft takes precedence and is never silently replaced on reconnection.

### First configuration

1. Click **Load from Controller** to edit the current configuration, or **File → New draft** to start empty. Select one of the 32 buttons.
2. Enter text, import an image, and choose the layout, image fit, colors, font size, alignment, and padding. A normal grid click only selects the button; it does not run anything.
3. Choose **Application / script…** to set an absolute path and arguments, or select an empty button and choose **Create subsection…**. Double-click section buttons or the generated Back key to browse the draft locally.
4. **Save draft** keeps a local named snapshot. **Review & apply…** validates and reviews the exact configuration before replacing the active Controller configuration. The confirmation is unchecked by default.

Applying a configuration **does not execute its programs**. Physical button presses, or a separately confirmed **Run applied action…**, are required for execution. Do not press a physical executable button until its action is intentional.

## Included features

| Area | Implementation |
|---|---|
| Visual editor | 8 × 4 button grid, section tree, per-button inspector, native 96 × 96 images and enlarged preview, adjustable grid zoom |
| Icons and text | PNG, JPEG, WebP, BMP, first-frame GIF, restricted SVG; automatic conversion; managed originals; icon/text/overlay layouts; colors, sizing, fit/crop, bold, alignment and padding |
| Sections | Create, rename, move, duplicate, and delete; up to 64 nested levels; generated protected Back key; Home action |
| Editing | Undo/redo, button copy/paste, same-page drag swapping, cross-page moves to empty positions, subtree cloning with new IDs |
| Actions | Absolute executable/script path, literal argument rows, optional interpreter, working directory, environment overrides, application or managed-task mode, task timeout and concurrency |
| Persistence | Offline editing, recovery autosave, separate named saved drafts, JSON import/export, portable bundles including original images |
| Controller integration | Connection/device/synchronization status, load, revision-aware Apply, explicit conflict replacement, interrupted Apply recovery, pause/resume, retry connection, show applied section |
| Execution tools | Separately confirmed testing of the exact applied action, recent 50 execution records, cancellation of managed tasks |
| Localization | English/French application labels; interface language takes effect after restart; deck Back/Home language configured separately |
| Extension readiness | Plugin bindings, dynamic appearance settings, and extension fields preserved; no plugin discovery, loading, or execution |

**Save and Apply are different operations.** An asterisk means local unsaved changes; the status footer separately indicates whether the draft matches the Controller revision. Apply does not mark a draft as saved. Status refreshes approximately every three seconds.

## Images and portable layouts

Use **Import image…** or drop a local image on a button. The original is copied into editor-owned storage and converted in a bounded worker. Moving or deleting the original selected file does not break the layout. Images are transferred through the public API when preparing Apply. No Controller asset directory is modified directly.

Use **Export portable bundle…** to transfer a complete draft and its images. **Export JSON only…** omits image bytes and is intended for inspection/interoperability. Imports create new configuration/page/button IDs, clear device selection and plugin secret references, and carry no execution approval. Importing never applies or runs anything.

Bundles may contain sensitive program paths, arguments, and environment values. They are not encrypted. Do not use environment overrides as a secret store.

## Applications and scripts

No implicit shell is used. Each argument table row is one literal argument, including spaces or an empty string. `$HOME`, `~`, redirection, and shell operators are not automatically expanded. Browse to an absolute path instead.

For a Python script, select its absolute script path, enable the interpreter, and select `/usr/bin/python3` (or your intended virtual-environment Python). For a shell script, select `/bin/bash` as interpreter when appropriate. An executable script with a valid shebang may instead run directly. Set the working directory explicitly.

**Independent application** delegates lifetime to the Controller's application-launch mechanism. **Managed task** supports a timeout, bounded concurrency, output/history, and cancellation. These are existing Controller behaviors. Graphical program launching depends on the Controller having your desktop session environment; start it using `sdl-session --start` in that session.

**Run applied action…** is unavailable for unapplied drafts, a disconnected or paused Controller, disabled buttons, and a simulator with execution disabled. Testing requires another confirmation and is never automatically retried after an ambiguous failure.

## Simulator

The simulator uses its own socket and a separate Configurator workspace. With the Controller CLI installed, use two terminals:

```bash
# Terminal 1: existing Controller simulator. Real execution stays disabled.
sdl-controller --simulate

# Terminal 2: Configurator for that isolated simulator.
sdl-configurator --simulator
```

Open `examples/demo.json` from **File → Open / import…**, then review/apply it. The demo contains navigation only. No CPU, storage, web-service, or other production plugins are included.

## Useful commands

```bash
sdl-configurator --check
sdl-configurator --language fr
sdl-configurator /absolute/path/to/my-layout.sdlbundle
sdl-configurator --socket "$XDG_RUNTIME_DIR/streamdeck-linux/controller.sock"
sdl-configurator --workspace "$HOME/.local/share/my-isolated-deck-editor"
```

`--check` checks imports, system font availability, and computed paths; it does not connect to USB or verify the graphical session. `--workspace` must be absolute and affects only the Configurator, not the Controller. One running Configurator may use a particular workspace at a time.

### Local storage

Default locations (XDG overrides respected):

```text
~/.local/share/streamdeck-linux-configurator/
  app/venv/                  Installed application, separate from Controller
  assets/                    Managed original images and canonical previews
~/.local/state/streamdeck-linux-configurator/
  draft.json                 Current recovery draft, including unsaved status
  drafts/<configuration-id>.json  Explicitly saved local drafts
  pending-apply.json          Present only for an unresolved Apply outcome
  preferences.json           Editor language preference
```

**File → Open saved draft…** selects a previously saved snapshot from the private drafts directory. The current recovery file is retained on close. Drafts with missing images remain editable, but affected rendering may fail and Apply/bundle export are blocked until the missing originals are restored or removed. There is no automatic asset garbage collector in this release.

## Tests

After installation, from the extracted source directory:

```bash
source "${XDG_DATA_HOME:-$HOME/.local/share}/streamdeck-linux-configurator/app/venv/bin/activate"
python -m pip install -r requirements-dev.lock
./scripts/test.sh -q
```

Qt tests run offscreen by default. Integration tests locate the sibling `controller/` source directory, or use:

```bash
export SDL_CONTROLLER_SOURCE=/absolute/path/to/fnord-deck/controller
./scripts/test.sh -q
```

The isolated integration test marked `real_execution` intentionally runs a small Python task that writes a test marker inside its temporary directory. It does not use your live Controller or USB device. Exclude that test with `-m 'not real_execution'` if required. Missing optional dependencies cause explicit skips, not simulated passing results.

## Troubleshooting and removal

For connection failure, verify that both applications run as the same user and that `sdlctl status` works. For GUI launch failure, run from a terminal and inspect `sdl-configurator --check`. A missing Qt platform dependency can be diagnosed with `QT_DEBUG_PLUGINS=1 sdl-configurator`; avoid sharing the output without reviewing local paths. On a Wayland desktop with XWayland available, `QT_QPA_PLATFORM=xcb sdl-configurator` is an optional diagnostic, not a default configuration.

An **interrupted Apply** is deliberately not retried automatically. Use **Controller → Resolve interrupted Apply…** to query/replay its original operation ID. A previously committed result is deduplicated. A never-committed non-executable configuration may commit on this explicit retry; never-committed executable changes need a fresh review. Discarding the pending record does not undo anything already applied.

Remove only the Configurator application with:

```bash
./scripts/uninstall.sh
```

Drafts, imported images, and the Controller are retained. Close the Configurator before upgrading or uninstalling it.

## Scope and documentation

Polling/display plugins are supported through Controller 0.2.0. Push/animation, plugin button commands, secret provisioning, dashboards, multi-device editing, automatic executable discovery, a system tray, and automatic update delivery remain outside this release. The visual grid targets XL only; the shared core/backend boundary allows a later model-specific grid without changing the API transport.

The recent-execution viewer is a bounded, read-only JSON detail view with a task-cancel control, not a full searchable log browser. Technical validation/Controller diagnostics retain their original language. Fonts come from system packages; none are bundled.

- [Architecture and extension boundaries](docs/ARCHITECTURE.md)
- [Security and persistence](docs/SECURITY.md)
- [Desktop acceptance checklist](docs/DESKTOP-ACCEPTANCE.md)
- [Verification and known limitations](docs/VERIFICATION.md)
- [Existing Controller API](docs/CONTROLLER-API.md)
- [Existing configuration schema guide](docs/CONFIGURATION.md)

This independent application is not affiliated with Elgato. Dependency licenses remain separate from the project's GPLv3 license. Qt/PySide installation references: https://doc.qt.io/qtforpython-6/gettingstarted.html and https://pypi.org/project/PySide6-Essentials/6.11.2/ .
