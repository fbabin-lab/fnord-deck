# Stream Deck Linux — AI Implementation Specification

**Specification version:** 1.0  
**Date:** September 28, 2026  
**Target hardware:** Elgato Stream Deck XL, model **20GAT9901**  
**Initial platform:** Ubuntu 24.04 LTS desktop, x86-64; GNOME on X11 and Wayland  
**Working product name:** Stream Deck Linux (SDL)  
**Status:** Implementation specification; no application implementation accompanies this document.

## 1. Purpose, authority, and scope

Build two independently runnable applications: a visual **Configurator** for editing settings, and a **Controller** that owns the physical Stream Deck, renders buttons, receives input, and dispatches actions. Closing the Configurator must not interrupt the Controller or the deck.

The initial product must support icon-only, text-only, and combined buttons; automatic image conversion; nested sections with reliable Back navigation; launching local applications and scripts by path; and a working, documented extension host for future plugins.

**Do not implement CPU, storage, web-service, or other production plugins in this project.** Implement their extension mechanism, configuration forms, lifecycle, messaging, failure handling, and tests. Deterministic protocol fixtures are permitted only under the test suite.

“MUST” is mandatory, “SHOULD” is a default that may be changed only with a recorded rationale, and “MAY” is optional. Numbered requirements and interface definitions are authoritative; examples illustrate them. Hardware/library facts are identified by source references. Architecture choices and numeric budgets are product decisions, not claims about measured performance.

### 1.1 In scope

| Area | Required result |
|---|---|
| Configurator | Native desktop editor, device-sized grid, section tree, button properties, previews, validation, save/apply, import/export, diagnostics |
| Controller | Exclusive logical device ownership, input handling, rendering, navigation, execution, persistence, reconnect, local API |
| Images and text | Managed image assets, supported-format conversion, typography, scaling/cropping, consistent preview and device rendering |
| Navigation | Root section, deeply nested child sections, automatic Back, optional user-assigned Home action |
| Execution | Absolute application/script paths, argument arrays, optional interpreter, working directory, environment overrides, managed-task and application modes |
| Extensions | Versioned manifests and wire protocol, language-independent plugin subprocesses, generic settings forms, dynamic displays, plugin actions, scoped secret access |
| Delivery | Repeatable installation, user-session startup, simulator, automated tests, hardware-verification instructions |

### 1.2 Explicit exclusions

No compatibility with Elgato Marketplace plugins or Elgato profile files is promised. Do not implement a marketplace, plugin dependency downloader, macro recorder, keyboard/mouse injection, window manipulation, cloud service, remote control over TCP, firmware updates, arbitrary HTML/JavaScript button content, video playback, production monitoring plugins, or user-supplied custom GUI code.

Only one selected physical deck and one active configuration are required in v1. Multiple-device sessions, more hardware families, automatic application-specific profiles, additional action types, and stronger plugin sandboxing are future extensions. Preserve suitable interfaces without implementing those features prematurely.

## 2. Verified device baseline

Elgato identifies model 20GAT9901 as the Stream Deck XL, with the following characteristics. [S01]

| Property | Baseline |
|---|---|
| USB vendor/product | `0x0FD9` / `0x006C` |
| Physical keys | 32 |
| Logical layout | 4 rows × 8 columns |
| Per-key image | 96 × 96 pixels |
| Native image upload | JPEG, with the device-specific orientation transform |
| Native orientation | Elgato documents a 180° rotation before upload |

The selected Python device library documents device enumeration, image-format discovery, key-state callbacks, and global brightness control. Its device API reports layout and image transformation information. [S02][S03]

**HW-01.** Put all model-specific details behind `DeviceAdapter`. Do not distribute constants such as 32, 8, or 96 across the editor, navigation engine, plugin host, and renderer.

**HW-02.** Query actual capabilities after connection and compare them with the configured layout. Reject a mismatching layout rather than truncating or silently rearranging buttons. Other product IDs must not be treated as this model merely because their marketing names resemble it.

**HW-03.** Logical key indexes are zero-based and row-major: `index = row × columns + column`; index 0 is top-left and index 31 is bottom-right. Verify this physically during acceptance testing.

**HW-04.** The shared renderer produces upright logical images. Only the device adapter applies native encoding/orientation, preferably through the library's image helpers. Never rotate twice.

**HW-05.** Use per-key image updates and brightness only. Full-screen images, stored hardware backgrounds, and firmware operations are outside v1.

## 3. Architecture and technology decisions

### 3.1 Two applications, one shared core

| Component | Responsibility | Explicit prohibition |
|---|---|---|
| `sdl-configurator` | Edit local drafts, manage assets, show previews, configure plugins, submit revisions, display runtime state | Must not open USB devices, execute button commands, or load plugin executable code |
| `sdl-controller` | Own device sessions, active configuration, navigation, rendering, actions, plugin processes, and local API | Must not require the Configurator or its GUI event loop |
| `sdl-core` library | Domain models, validation, schemas, asset conversion, rendering, message types | Must not depend on GUI widgets or own a device/service singleton |
| Optional `sdlctl` utility | Diagnostics, status, pause/resume, validation and test commands over the same API | Must not introduce another device owner or independent configuration writer |

The communication path is:

```text
Configurator ── local Unix-domain socket ── Controller ── DeviceAdapter ── USB deck
      │                                      │
      └── shared models/rendering             ├── ActionExecutor ── local programs
                                             └── PluginSupervisor ── plugin subprocesses
```

**ARC-01.** Use Python 3.12 as the baseline, `asyncio` for the Controller, `python-elgato-streamdeck` through an adapter, Pillow for raster composition, a restricted SVG renderer, and PySide6 for the Configurator. PySide6 is Qt's Python binding; Pillow and CairoSVG document the relevant image-processing facilities. [S02][S04][S05][S06]

**ARC-02.** Pin tested dependency versions in a lockfile. Record Python, image codec, Qt, HID backend, and library versions in diagnostics. Do not select an unverified development release because its documentation appears newer.

**ARC-03.** Provide typed interfaces for `DeviceAdapter`, `Renderer`, `AssetStore`, `ConfigurationRepository`, `ActionExecutor`, `PluginSupervisor`, `SecretStore`, `SessionContextProvider`, and `Clock`. Supply fake implementations where needed for tests.

**ARC-04.** Rendering and input processing must not depend on network availability. Network activity belongs to future plugins or explicitly launched user programs, not the Controller's basic operation.

**ARC-05.** Implement registered action handlers by namespaced type, not a switch statement that grows across unrelated modules. Core types are `core.none`, `core.navigate`, `core.home`, `core.execute`, and `core.plugin.invoke`. Reserve `core.*` for the product.

**ARC-06.** The Controller must work without a display server for configuration validation, simulation, and non-GUI tasks. Launching graphical applications requires an appropriate logged-in desktop session.

## 4. Domain model and configuration contract

### 4.1 Persistent objects

| Object | Required fields / meaning |
|---|---|
| Configuration | `schemaVersion`, `configurationId`, `name`, `target`, `layout`, `rootPageId`, `settings`, `pages`, `extensions` |
| Target | `adapterId`, `vendorId`, `productId`, nullable `serialNumber`; serial pinning becomes explicit on device selection |
| Layout | `rows`, `columns`, `keyWidth`, `keyHeight`; validated against the adapter |
| Settings | `brightnessPercent`, `locale`; v1 starts at root on Controller startup |
| Page | `id`, `name`, nullable `parentPageId`, `buttons` |
| Button | `id`, `keyIndex`, `enabled`, `appearance`, `action`, nullable `pluginBinding`, `extensions` |
| Appearance | nullable `iconAssetId`, `text`, `layout`, `backgroundColor`, `textColor`, `fontFamily`, `fontSizePx`, `fontWeight`, `textAlign`, `imageFit`, `paddingPx`, nullable `dynamic` |
| Asset | SHA-256 content identifier, decoded media type, original bytes, canonical raster derivative, dimensions, conversion metadata |
| Execution record | `runId`, button/configuration identity, origin, timestamps, state, exit information, bounded diagnostic output |

Configuration/page/button/instance IDs MUST be UUIDs. Human-readable names are not identities. Asset IDs are `sha256:<64 lowercase hexadecimal characters>` computed from original imported bytes. Native JPEGs and intermediate render caches are derivatives, not the source of truth.

`schemaVersion` starts at `"1.0"`. The runtime revision is a separate monotonically increasing integer maintained by the Controller; do not confuse it with the document schema or plugin API version.

`extensions` is a size-limited map of namespaced JSON objects. Unknown extension data may round-trip but has no execution privileges. Reject unknown core action types and unsupported document versions for Apply. Preserve such documents in the editor as read-only/import-recovery data rather than silently deleting fields.

### 4.2 Button appearance and action are independent

**MOD-01.** Every user-assignable button can have an icon, text, both, or a dynamic plugin display. An action can exist without a plugin and a plugin display can exist without an action.

**MOD-02.** A button has exactly one activation action in v1. Choosing “Open section” does not also execute a command. Multiple operations/macros require a future explicit action type.

**MOD-03.** A button displaying a future metric may still use `core.execute` to open a local application. It is not forced to use the plugin's action handler.

**MOD-04.** A missing button entry is a black, inactive key. An assigned action with no user-supplied appearance receives a visible core fallback symbol. Disabled buttons remain visible with a disabled indicator and cannot activate.

**MOD-05.** Validate duplicate IDs, duplicate key positions, bounds, required fields, references, graph invariants, and configured resource limits. Do not rely solely on GUI validation.

### 4.3 Appearance values

`layout` is `iconOnly`, `textOnly`, `iconAboveText`, or `textOverlay`. `imageFit` is `contain`, `cover`, or `stretch`; default `contain`. Colors use `#RRGGBB`; imported icon transparency is composited onto the configured background. `fontWeight` is `normal` or `bold`; alignment is `left`, `center`, or `right`.

At the XL baseline, defaults are 4 pixels padding, 16-pixel text, centered alignment, normal weight, black background, and white text. Text auto-shrinks to a minimum of 8 pixels, then wraps/ellipsizes within the chosen layout. Preserve the full string in configuration. Support explicit line breaks and at least Latin text with French accents. Do not promise that every emoji or script has an available glyph.

## 5. Sections, nesting, and navigation

**NAV-01.** Model sections as a rooted tree. The root has no parent. Every non-root page has exactly one parent and exactly one incoming `core.navigate` button in that parent. Navigation targets must be direct children; dangling links, cycles, shared child pages, and unreachable pages are invalid in v1.

**NAV-02.** Root has 32 available keys on this model. Every non-root page reserves **key 0, top-left, as a generated Back button**, leaving 31 assignable keys. The user cannot replace, move, disable, or delete this generated button. It is not stored as a user button.

**NAV-03.** Back always navigates to the direct parent. It must work independently of plugin health, program execution, and the Configurator. At root there is no generated Back key. A user-assigned `core.home` action may return directly to root.

**NAV-04.** There is no fixed two- or three-level assumption in data structures or UI. The default safety limit is 64 levels, counting root as level 1, and 1,024 pages. Keep limits centralized and expose violations clearly. Support a 64-level test tree without recursive stack dependence.

**NAV-05.** Creating a section creates both its page and parent navigation button as one draft operation. Deleting a navigation button requires explicit confirmation to delete its subtree, or an explicit move operation first. Moving a subtree must update its parent/link atomically and must not create a cycle. Root cannot be deleted.

**NAV-06.** Copying an ordinary button creates a new button ID and, when applicable, a new plugin instance ID. Copying a section duplicates the subtree with new IDs and remapped links; it must not create shared mutable pages.

**NAV-07.** Applying a revision keeps the current page only when its ID and valid ancestry still exist. Otherwise return to root. A Controller restart starts at root. A temporary USB disconnect/reconnect retains the current page in that running session.

**NAV-08.** Editor browsing does not navigate the physical deck. An explicit “Show this section on deck” control may call the live navigation API after confirming that the selected page belongs to the applied revision. Draft previews never execute actions.

## 6. Configurator requirements

**UI-01.** Provide a main window with a device/status selector, section tree and breadcrumb, 8 × 4 preview grid for the selected model, selected-button properties, and clear Draft/Applied revision indicators.

**UI-02.** Button properties must expose appearance, action type, executable/script selection, arguments as separate rows, interpreter settings, working directory, execution mode, and plugin settings when applicable. Show the resolved command as a read-only summary, never as an implicitly executable shell string.

**UI-03.** Support file selection and drag/drop image import, copy/paste, drag/drop reordering, delete, undo/redo, section creation/move/duplicate, and text editing. Do not allow dropping a user button onto a reserved Back slot.

**UI-04.** Offer enlarged previews and actual-size previews. Use the shared renderer, not a visually similar independent Qt layout. Live plugin display values are optional subscriptions to the Controller and must be identified as applied/live rather than draft values.

**UI-05.** Save Draft writes only editor-owned draft data. Apply submits a validated revision to the Controller. Autosave must not silently Apply. Warn about unsaved changes when closing or switching configurations.

**UI-06.** The editor must open and save drafts while the Controller or device is unavailable. Offline editing uses the selected model descriptor. Imported draft assets are staged in an editor-owned content-addressed draft directory. Before Apply, upload any missing original assets through the Controller API and obtain their validated IDs; the Controller recomputes/validates derivatives rather than trusting editor-generated bytes. Apply requires a Controller connection but does not require a connected deck. Local preview must never run plugins or commands.

**UI-07.** Provide statuses including Controller unavailable, device disconnected, permission denied, device in use, layout mismatch, applied/not yet displayed, plugin unavailable, invalid image, and invalid executable. Errors must identify the page/button and suggest a concrete corrective step.

**UI-08.** Include import/export of this application's configuration bundle. Import is a draft operation only; it cannot activate commands, approve plugins, or overwrite active settings automatically.

**UI-09.** Include a plugin registry/settings screen showing package identity, version, declared capabilities, trust state, health, and contribution availability. Only parse manifests in the Configurator; executable plugin code runs solely under the Controller.

**UI-10.** All product UI strings must use message catalogs. Deliver English and French UI strings. User-entered button text is preserved verbatim and is not automatically translated. Core Back/Home/error indicators use the selected locale.

**UI-11.** Include Pause/Resume, reconnect/retry, brightness, a bounded execution history, and diagnostics export. A “Test action” control is explicitly labeled as executing a program; it requires confirmation and identifies the executable and argument list. Normal grid clicks only select/edit.

## 7. Asset ingestion and rendering

**IMG-01.** Required imported formats are PNG, JPEG, WebP, BMP, GIF, and a restricted self-contained SVG subset. Decode by content, not extension. Animated inputs use their first displayable frame and show an explanatory warning. Animation playback is not part of v1. Pillow documents raster format support; SVG requires its own rasterizer. [S05][S06]

**IMG-02.** Conversion must correct EXIF orientation, normalize to RGB/RGBA and sRGB when valid color metadata is present, preserve alpha until composition, apply fit/crop and padding, combine text and icon, and render at the adapter's logical key dimensions. Corrupt color profiles must produce a controlled warning/fallback, not a crash.

**IMG-03.** Keep imported originals in a managed content-addressed store and generate a canonical PNG derivative with maximum dimension 1,024 pixels. Future rerendering must not require the original filesystem path. Unreferenced assets are eligible for garbage collection only after all retained revisions and editor drafts have been considered.

**IMG-04.** The final path is original → canonical raster → logical button composition → optional core status overlay → adapter-native encoding. For this model the last stage produces the correctly oriented JPEG. The editor displays the upright logical composition. Native JPEG encoding may introduce small visual differences; tests should distinguish pre-encoding pixel equality from hardware/JPEG tolerance.

**IMG-05.** Reject files over 20 MiB, rasters exceeding 40 million decoded pixels, unsupported formats, decompression bombs, and invalid images. Conversion occurs in a bounded worker, not the USB callback or GUI thread; default wall timeout 5 seconds, memory budget 512 MiB, and one conversion at a time.

**IMG-06.** SVG import must reject DTDs/entities, scripts, `foreignObject`, external stylesheets, external URLs, embedded images, and external fonts. V1 accepts paths, basic shapes, groups, transforms, local gradients, and local clip/mask references. Reject SVG text elements rather than attempting arbitrary font/resource resolution. Restrict CSS to an allowlist and local fragment references; deny all resource fetching. CairoSVG's documented safety behavior is not, by itself, a complete application sandbox. [S06]

**IMG-07.** Do not invoke arbitrary desktop image viewers, shells, document converters, or unrestricted browser engines to normalize images. Package/install the required codecs and test their availability.

**IMG-08.** Use a deterministic default font family available through declared system dependencies, with a documented fallback chain. Do not load fonts embedded in imported SVGs. Record actual font/renderer versions in golden-image test metadata.

**IMG-09.** Cache composition and native output by asset hash, appearance, dynamic state, target capabilities, renderer version, and font identity. Send only changed images. Use bounded latest-frame queues; outdated images for obsolete pages must never overwrite the current page.

## 8. Application and script execution

### 8.1 `core.execute` data contract

| Field | Definition |
|---|---|
| `path` | Absolute path to executable or script; required |
| `arguments` | Array of literal strings; default empty |
| `interpreter` | Null for direct execution, otherwise `{path, arguments}` with an absolute interpreter path |
| `workingDirectory` | Absolute existing directory; default user's home |
| `environment` | Explicit non-secret string overrides; default empty |
| `mode` | `application` or `task` |
| `timeoutMs` | Null in application mode; task default 120000, allowed 1000–86400000 |
| `concurrency` | `ignoreWhileRunning` by default, or `parallel`; maximum parallel count 4 per button |
| `maxParallel` | Integer 1–4; 1 for ignoreWhileRunning |

For direct execution, argv is `[path] + arguments`. With an interpreter, argv is `[interpreter.path] + interpreter.arguments + [path] + arguments`.

**EXE-01.** Never implicitly invoke a shell, split a command string, expand wildcards, evaluate templates, or substitute plugin output into executable paths/arguments. Paths containing spaces are single values. `~` may be expanded explicitly by the editor before saving; stored paths must be absolute. Python documents argument-list execution and the different behavior of shell execution. [S07]

**EXE-02.** Validate path existence, file type, access, and working directory when configuring and immediately before launch. Directly executed files require execute permission. Interpreted scripts require read permission and an executable interpreter. Do not guess an interpreter from a filename extension or automatically chmod a file.

**EXE-03.** Missing/moved paths produce an unavailable-action warning; they do not prevent unrelated buttons from working. A path that becomes invalid after Apply must fail safely at activation. Executables are local mutable files: approval of a path is not a promise that its contents can never change.

**EXE-04.** Applications/scripts run as the logged-in user, never automatically as root. Do not embed sudo/password handling or silently escalate. A user may intentionally select an interpreter or terminal as an executable, but that is explicit user configuration, not Controller-generated shell code.

**EXE-05.** Execution is asynchronous. Page changes, USB events, plugin traffic, and editor operations continue while tasks run. Programs receive noninteractive stdin by default; terminal interaction requires launching a terminal application explicitly.

**EXE-06.** In `task` mode, track exit status, support cancel/timeout, and stop the task's managed process group/cgroup on Controller shutdown. Send graceful termination, allow 2 seconds, then force termination. A task leaving unmanaged detached descendants is outside the task contract and must be documented as such. Bind managed task units to the Controller unit where supported and verify cleanup after an abrupt Controller failure; do not merely handle the graceful exit path.

**EXE-07.** In `application` mode, launch through a separate transient user-service/unit or equivalent independently owned session scope, so closing or restarting the Controller does not kill a normally running GUI application. The operation reports “launched,” not “application finished successfully.” Do not rely solely on `start_new_session` when the Controller runs in a systemd service cgroup. Systemd documents transient units and managed process groups. [S08]

**EXE-08.** Use a dedicated launcher adapter for both modes, with a tested systemd-user implementation on the target platform. Preserve argv exactly: systemd's own argument/environment expansion must be disabled or avoided through the appropriate API. If independent application launch is unavailable, report that limitation; do not silently use different lifetime semantics.

**EXE-09.** Obtain desktop environment variables from the actual graphical session, not guessed `DISPLAY=:0` values. Handle at least `DISPLAY`, `WAYLAND_DISPLAY`, `XAUTHORITY`, `XDG_RUNTIME_DIR`, and `DBUS_SESSION_BUS_ADDRESS`, plus locale and normal user context. Never copy the entire editor environment or log its contents. Absence of a required desktop session is a visible launch error.

**EXE-10.** Runtime states are `starting`, `running`, `launched`, `succeeded`, `failed`, `timedOut`, `cancelled`, or `unknownAfterRestart`. Busy/success/error overlays are core-owned. Execution completion must not overwrite a different page or a newer button definition.

**EXE-11.** No automatic retries of an action after process failure, Controller restart, lost IPC response, or USB reconnect. The operation may have side effects. Application-mode concurrency suppression covers the outstanding launch request only; it does not guarantee a single application window after the application detaches.

**EXE-12.** Capture task output only into bounded diagnostic buffers, with 64 KiB each for stdout/stderr and a visible truncation marker. Continue draining/discarding after the limit so a child cannot block on a full pipe. Keep outputs out of ordinary logs and routine exports. Never log secret values or complete environments.

## 9. Controller state, input, and USB lifecycle

**RUN-01.** Use one Controller process per user and one logical device owner. Hold a process lock before publishing its socket. Use serial identity, not transient USB path, for a selected physical device. A second Controller must exit with an actionable message.

**RUN-02.** With no saved serial, a single matching device may be proposed for selection. Multiple matching devices require explicit selection. Do not silently switch to another device after the selected one disappears. Unknown devices remain untouched.

**RUN-03.** Device states are `disconnected`, `connecting`, `ready`, `permissionDenied`, `inUse`, `layoutMismatch`, and `error`. Retry transient reconnects with bounded backoff, distinguish permission failures, and preserve configuration while disconnected.

**RUN-04.** HID callbacks must enqueue small input records and return immediately. Encoding, disk access, plugin calls, and program launches must not run inside callbacks. The selected library documents reader-thread callbacks, so thread-safe dispatch is required. [S03]

**RUN-05.** A click is one matching key-down/key-up pair. Activation occurs on release. Repeated down reports while held do not retrigger. Double-clicks are two ordinary clicks; long-press and chord-specific actions are outside v1. Maintain independent state for each key.

**RUN-06.** Capture `deviceEpoch`, `pageGeneration`, button ID, and revision on key-down. If disconnect, pause, navigation, or Apply invalidates that context before release, cancel the press. Never dispatch a release against the new page's button at the same index.

**RUN-07.** On initial connection/reconnection and after resuming, observe a released baseline before accepting a key. A key already held must be released and pressed again. Lost/overflowed input must cause safe cancellation and resynchronization, never an inferred click.

**RUN-08.** A page change increments its generation, cancels old queued writes/presses, and queues the entire destination page including black unused keys. Serialize USB writes through one worker. Hardware updates are not physically atomic: inhibit activations during the page redraw, then require a fresh press after the new frame completes. Do not permit an old visible icon to launch a new hidden action.

**RUN-09.** Reconnect reapplies brightness and all current-page keys. Device I/O failure cannot crash the editor or delete configuration. On orderly exit, stop input and attempt to clear the device; this is best-effort if USB is already unavailable.

**RUN-10.** Pause cancels pending presses and blocks new actions; Resume requires a fresh press. Do not claim that v1 Pause is an authentication boundary or that it automatically follows every desktop lock-screen implementation. Document that buttons remain usable while the Controller is active unless paused.

## 10. Persistence, import/export, and atomic Apply

Follow XDG configuration, data, cache, state, and runtime locations. [S09]

| Location | Ownership / contents |
|---|---|
| `$XDG_CONFIG_HOME/streamdeck-linux/` | Controller-owned applied revision metadata and revision documents; editor preferences/drafts in a separate editor-owned subdirectory |
| `$XDG_DATA_HOME/streamdeck-linux/assets/` | Immutable managed assets and canonical derivatives |
| `$XDG_DATA_HOME/streamdeck-linux/plugins/` | Explicitly installed local plugin packages |
| `$XDG_STATE_HOME/streamdeck-linux/` | Trust records, bounded execution history, recovery metadata |
| `$XDG_CACHE_HOME/streamdeck-linux/` | Regenerable rendered/native image cache |
| `$XDG_RUNTIME_DIR/streamdeck-linux/` | Process lock, Unix socket, transient transfer state |

Respect XDG defaults when variables are unset, except that an absent/unsafe runtime directory must cause a diagnostic error rather than using a predictable insecure `/tmp` socket.

**CFG-01.** The Controller is the sole writer of applied configuration and trust state. The Configurator only writes its own drafts/caches. All applied changes go through the local API.

**CFG-02.** Apply includes `expectedRevision` and a UUID `operationId`. Reject stale revisions with `REVISION_CONFLICT`; retain the draft. Prepare/validate all references, asset integrity, topology, limits, and current-page rendering before committing.

**CFG-03.** Persist assets/revision data to temporary locations, fsync as required, then atomically replace the current-revision pointer on the same filesystem. Only after successful durable commit install the new in-memory configuration and start/reconfigure plugins. A crash after commit is recovered from that pointer. No action executes merely because Apply occurred.

**CFG-04.** Repeat requests with the same operation ID and identical payload return the original result; the same ID with different content is rejected. Persist recent operation outcomes with their revisions so the client can resolve an interrupted Apply by reading current state. Retain at least the last 100 outcomes and 10 revisions.

**CFG-05.** Separate `configurationApplied` from `deviceSynchronized` in responses/events. Applying while USB is absent succeeds as a configuration transaction; display synchronization remains pending. A plugin failure after commit does not roll back unrelated configuration.

**CFG-06.** On corrupt startup data, preserve the damaged file and offer the last valid revision. If none is valid, enter an empty safe configuration with no actions. Do not overwrite evidence silently. Restoring a historical revision creates a new revision number.

**CFG-07.** Export a ZIP bundle containing a manifest, configuration JSON, and referenced asset originals/metadata; omit caches, executable plugins, trust approvals, command output, and secret values. Include source hashes and format version. Paths to local programs may need rebinding on another computer.

**CFG-08.** Reject path traversal, absolute archive members, symlinks, duplicate/conflicting entries, checksum failures, and archive expansion beyond 256 MiB or 10,000 files. Imported active content is unapproved: executable actions require an explicit review before first Apply, and plugins require local trust approval. Import cannot confer trust through a JSON flag.

**CFG-09.** Never alter an existing configuration merely because its referenced plugin is missing. Preserve the binding and settings, show fallback visuals, and disable only the missing plugin-dependent behavior. A separately configured local execution action may remain available if it has passed the normal approval rules.

## 11. Configurator–Controller local API

### 11.1 Transport and envelope

**IPC-01.** Use a local Unix-domain stream socket at `$XDG_RUNTIME_DIR/streamdeck-linux/controller.sock`, directory mode 0700 and socket mode 0600. Verify ownership, reject symlinks, and verify peer UID. Do not listen on TCP, including loopback.

**IPC-02.** Use JSON-RPC 2.0 message envelopes [S10], framed by a 4-byte unsigned big-endian payload length followed by UTF-8 JSON. Maximum frame is 16 MiB. Reject duplicate JSON object keys, non-finite numeric literals, and JSON nesting beyond 64 levels on both local and plugin channels. V1 supports one request/response/notification object per frame and no batch arrays. Request IDs are unique strings per connection. No arbitrary Python objects, pickle, executable expressions, or shared memory addresses are permitted.

**IPC-03.** First call is `system.hello` with supported application API major/minor versions and client identity. Reply includes negotiated version, runtime version, capabilities, and frame limits. API major mismatch blocks mutation. A version number alone is not an authorization credential.

**IPC-04.** Mutations require request IDs. Server events are notifications with `runtimeEpoch`, monotonically increasing `eventSequence`, revision, event type, and payload. On reconnect or an event gap, fetch a fresh snapshot. Slow clients are disconnected rather than allowed unbounded queues; image telemetry may be coalesced.

### 11.2 Required methods

All parameters/results must be expressed in shipped JSON schemas. The following names and semantics are normative; common results use `{ok:true}` when no additional value is specified.

| Method | Parameters / result |
|---|---|
| `system.hello` | Client/API versions → negotiated version and capabilities |
| `system.snapshot` | No parameters → device, revision, current page, pause state, plugin health, recent job summaries |
| `events.subscribe` | Event types → subscription ID; stream begins after response |
| `configuration.get` | Optional revision → document, revision, validation warnings |
| `configuration.validate` | Document → errors/warnings with JSON paths, page/button IDs; no side effects |
| `configuration.apply` | Document, expectedRevision, operationId, applicable review token → revision, warnings, synchronization state |
| `configuration.review` | Document → executable-action summary, content hash, expiring review token requiring explicit GUI confirmation on Apply |
| `configuration.history` | Cursor/limit → retained revisions and next cursor |
| `asset.beginImport` | Name, byte count, SHA-256 → opaque upload ID |
| `asset.writeChunk` | Upload ID, sequence, base64 chunk → acknowledged sequence; raw chunks ≤256 KiB |
| `asset.finishImport` | Upload ID → validated asset ID and metadata, or structured error |
| `asset.readChunk` | Asset ID, offset, length → bounded original-byte chunk and total size |
| `device.list` | No parameters → detected descriptors without changing selected device |
| `device.retry` | Device ID → reconnect requested; no force takeover |
| `runtime.navigate` | Applied page ID and expectedRevision → new page/generation |
| `runtime.pause` | Boolean → effective pause state |
| `session.update` | Explicit allowlisted desktop/session variables → validation result; same-user client only, never persist secrets |
| `execution.test` | Applied button ID, expectedRevision, confirmation flag, operationId → run ID; only after an explicit Test UI action |
| `execution.list` | Cursor/limit → bounded history |
| `execution.cancel` | Run ID → cancellation requested or unsupported/not-running error |
| `plugins.list` | No parameters → manifests, contribution schemas, fingerprints, availability, approvals, health |
| `plugins.rescan` | No parameters → registry changes; never executes unapproved packages |
| `plugins.approve` | Plugin ID/version/fingerprint, confirmation → trust record |
| `plugins.setEnabled` | Plugin identity and Boolean → availability state |
| `plugins.restart` | Plugin identity → restart request; approval must remain valid |
| `secret.put` | Purpose, secret value → opaque reference; sensitive request body never logged |
| `secret.delete` | Opaque reference → deletion result; refuse while referenced by the active configuration |

Revision checks protect mutating operations from stale UI state. Changing the selected device is an edit to `target` followed by Apply; brightness is an applied configuration setting, not a second hidden store.

`configuration.review` does not authorize by itself. A successful Apply must carry the matching document hash/token and explicit confirmation when new/changed/imported executable actions are present. Persist approval separately from the portable document. This protects against accidental activation, not malicious programs already running as the same user.

`execution.test` uses a deduplicated operation ID persisted before dispatch as an intent record; after a restart, an unresolved intent is unknown and must not be dispatched again automatically. Crash recovery cannot provide exactly-once execution of arbitrary programs. Return an unknown outcome when uncertain and never retry automatically.

### 11.3 Errors and transfers

Use standard JSON-RPC parse/method/parameter errors. Product errors use `error.data.code` such as `REVISION_CONFLICT`, `VALIDATION_FAILED`, `DEVICE_UNAVAILABLE`, `DEVICE_PERMISSION_DENIED`, `UNSUPPORTED_VERSION`, `ASSET_INVALID`, `PATH_UNAVAILABLE`, `PLUGIN_UNAPPROVED`, `PLUGIN_UNAVAILABLE`, `SECRET_UNAVAILABLE`, `LIMIT_EXCEEDED`, or `OUTCOME_UNKNOWN`.

Include a stable message key, safe human-readable message, optional field/page/button identity, and retryability. Do not send tracebacks, tokens, or full environments to ordinary clients. Expire abandoned uploads after 10 minutes and enforce two concurrent imports per client. Asset IDs and upload IDs must not be interpreted as arbitrary filesystem paths.

## 12. Plugin extension architecture

### 12.1 Scope and isolation

**PLG-01.** Implement a functioning external-process plugin host, not merely an empty abstract class. A future developer must be able to add a conforming local package without modifying core rendering, USB, navigation, or GUI code.

**PLG-02.** A plugin contribution may provide `display`, `invoke`, or both. Display means producing bounded declarative values/images for its assigned button. Invoke means handling declared commands when that button activates. No CPU, disk, or HTTP collection logic belongs in the core to demonstrate this.

**PLG-03.** Launch one subprocess per approved plugin package/version, capable of serving multiple button instances. Do not import plugin modules into either application. Communicate over stdin/stdout; reserve stderr for bounded diagnostics. Support any implementation language able to follow the protocol.

**PLG-04.** Plugin failures must not terminate the Controller. A failed package may affect its own instances, but Back, navigation, other plugins, and ordinary buttons remain functional.

**PLG-05.** Separate-process operation is crash containment, **not a security sandbox**. V1 plugins run as the same user and may have that user's filesystem/network privileges. Declared OS permissions are informative approval disclosures, not enforceable restrictions. Protocol-scoped display and secret access are enforced by the host, but cannot neutralize a malicious same-user native program.

**PLG-06.** Plugins are not given device handles or an API to rewrite the deck configuration, arbitrary buttons, navigation, or executable paths. This describes the supported protocol, not a claim that unsandboxed code is technically unable to attack the host through other means.

### 12.2 Package discovery and manifest

Directory convention: `$XDG_DATA_HOME/streamdeck-linux/plugins/<pluginId>/<version>/manifest.json`. Scan metadata only. Reject escaping paths, unexpected symlinks, duplicate/conflicting identities, invalid schema, and unsupported API versions. No automatic installation scripts, dependency downloads, or package execution on discovery.

A manifest contains:

| Field | Meaning |
|---|---|
| `manifestVersion` | `"1.0"` |
| `id` | Reverse-DNS identifier; `core.*` prohibited |
| `version` | Semantic version string |
| `name`, `description` | Localized maps with required `en` fallback |
| `api` | Minimum/maximum supported plugin API versions; v1 host implements `1.0` |
| `entrypoint` | Executable path inside package + literal arguments, or explicit absolute interpreter + package-relative script + literal argument arrays |
| `files` | Map of package-relative regular-file paths to SHA-256 values used to compute the approval fingerprint; excludes the manifest itself |
| `assets` | Map of asset keys to declared package-relative PNG paths included in `files`; may be empty |
| `permissions` | Human-readable/declarative filesystem, network, process and secret needs; advisory except host-mediated access |
| `contributions` | Contribution IDs, capabilities, update mode, declared commands, settings schema, secret fields, default/minimum refresh intervals |

Entry-point kinds are `executable` and `interpreted`. An executable entrypoint resolves its relative `path` under the package root. An interpreted entrypoint has `interpreterPath`, `interpreterArguments`, relative `scriptPath`, and `arguments`. Neither supports shell strings or implicit variable expansion. The runtime sets the package root as working directory.

Define the package fingerprint as SHA-256 over the exact UTF-8 manifest bytes and a path-sorted list of verified file hashes, with unambiguous length-prefixed fields. Validate every installed package file against `files` before approval and launch, except the manifest itself; reject unlisted executable/content files. Code/content or manifest changes invalidate approval. Keep mutable data/caches and interpreter-generated bytecode outside the approved package tree. External interpreters are declared separately and remain managed by the user's operating system. A package fingerprint is a change detector, not a publisher signature or proof of safety.

### 12.3 Button binding and settings forms

Each button may have one `pluginBinding` in v1:

```json
{
  "instanceId": "24a1a95b-c181-4b1c-977c-2e3d657480e0",
  "pluginId": "org.example.fixture",
  "pluginVersion": "1.0.0",
  "contributionId": "sample",
  "settingsVersion": 1,
  "settings": {"label": "Example"},
  "secretRefs": {},
  "refreshIntervalMs": 1000
}
```

This is an illustrative binding, not a production plugin requirement. A `core.plugin.invoke` action references a `commandId` declared by this same contribution. A `core.execute` action remains independent of the binding.

Each contribution uses exact field names `id`, `name`, `capabilities`, `settingsVersion`, `settingsSchema`, `secrets`, `updateMode`, `minRefreshIntervalMs`, `defaultRefreshIntervalMs`, and `commands`. `name` is a localized map. `capabilities` is a nonempty subset of `["display", "invoke"]`; contribution IDs are unique within the manifest. `commands` is an array of `{id, name, description}` with localized name/description maps; it must be empty without `invoke`. There are no per-command parameter expressions in v1: command behavior reads the instance settings. Display-specific update fields are required only for display-capable contributions; update mode is absent otherwise.

Use inline JSON Schema Draft 2020-12 for non-secret settings validation. [S11] The generic GUI must support objects, primitive properties, required fields, enums, bounded numbers/strings, and arrays of primitives. Support field title, description, and default annotations. Reject external `$ref`, executable validators, and arbitrary custom UI. A contribution requiring unsupported form constructs is unavailable with an explanation; do not drop settings silently.

Secret inputs are a separate manifest `secrets` array of `{name, label, required}`; they are not embedded in the ordinary settings schema or JSON. Store only opaque references in `secretRefs`. A missing required secret marks that instance unavailable, without breaking the rest of the deck.

Settings have an integer `settingsVersion`. Do not silently reinterpret stored settings after an incompatible plugin update. Preserve the old binding and require an explicit reset/reconfiguration; automatic third-party settings migrations are not required in v1.

## 13. Plugin wire protocol — version 1.0

### 13.1 Framing, identities, and lifecycle

**API-01.** Use JSON-RPC 2.0 envelopes with one UTF-8 JSON object per newline on stdin/stdout. A message is at most 256 KiB including the newline. JSON string newlines must be escaped. No batches or non-protocol stdout. Both sides may issue requests; use `h-...` and `p-...` string request ID prefixes respectively. Stderr is drained with a bounded ring buffer.

**API-02.** Process lifecycle: discovered → approved/disabled → starting → initialized → serving → stopping, or failed/quarantined. Bootstrap timeout is 5 seconds. No instance requests precede a successful `plugin.initialize` response.

**API-03.** The stored `instanceId` identifies the button binding. A fresh `instanceEpoch` UUID identifies each creation/recreation. Each visible activation receives a fresh `activationId`. Include these values in instance messages and display updates. They prevent late responses from an old configuration/page activation from affecting a new one.

**API-04.** V1 creates visible instances on demand, initially hidden, then makes them visible. On leaving the page, mark them hidden and suspend polling. Hidden display updates are discarded. Keep an instance alive while an accepted invocation is pending; otherwise it may be destroyed after 60 seconds hidden. An idle plugin process may stop after 60 seconds with no instances/invocations.

**API-05.** A successful Apply destroys/recreates affected bindings, with new epochs. It may retain an unchanged instance only when its full binding fingerprint and target identity remain unchanged; in either case a new visible activation ID is required after page redraw. Removed/changed instances cannot receive subsequent button activations.

### 13.2 Host → plugin methods

Common instance context is `{instanceId, instanceEpoch}`. Visible operations add `activationId`. IDs must exactly match an existing instance; otherwise return an invalid-instance error.

| Method | Parameters | Result / semantics |
|---|---|---|
| `plugin.initialize` | Selected API version, host version, plugin identity/fingerprint, locale, protocol/resource limits | Chosen API version, plugin version, acknowledged capabilities |
| `instance.create` | Instance context, contribution ID, settingsVersion, non-secret settings, available secret names, target descriptor, effective refresh interval | `{ready:true}`; initially hidden |
| `instance.visibility` | Instance context, `visible`, activationId or null, target descriptor | `{ok:true}`; false stops autonomous display work where possible |
| `instance.refresh` | Visible instance context, deadlineMs | `{sequence, state: DisplayState}`; only for polling/both contributions |
| `instance.invoke` | Instance context, activationId, invocationId, commandId, monotonic event duration, wall-clock event time | `{accepted:true}`; completion is reported separately |
| `instance.cancel` | Instance context, invocationId, reason | `{accepted:true|false}`; cooperative cancellation, not undo |
| `instance.destroy` | Instance context, reason | `{ok:true}`; release resources; no further updates accepted |
| `plugin.ping` | Empty object | `{ok:true}` |
| `plugin.shutdown` | Reason | `{ok:true}` followed by orderly exit |

`target` includes deviceId, logical keyIndex, logical pixel width/height, locale, and supported display fields. It contains no USB handle or native device encoding instructions.

All normal method acknowledgements must arrive within 2 seconds except initialize/create/invoke (5 seconds) and refresh (effective interval, bounded to 1–5 seconds). Cancellation/destroy deadlines are not allowed to block the Controller.

`instance.invoke` accepts only a declared command. The host issues a unique invocationId once and never automatically retries it. An acknowledgement means accepted, not completed. The plugin sends `invocation.finished`; default completion timeout is 60 seconds. On timeout, mark unknown/failed as appropriate and request cancellation. Do not claim to reverse external side effects.

A page becoming hidden does not cancel an accepted invocation. Removing/changing its binding, disabling its package, or stopping the Controller requests cancellation and then tears down the instance. Late completion may update the bounded execution record, but never a different button's image.

### 13.3 Plugin → host messages

| Method / notification | Payload | Host behavior |
|---|---|---|
| `display.update` notification | Visible instance context, monotonically increasing sequence, DisplayState | Validate, coalesce, and render only the matching visible binding |
| `invocation.finished` notification | Instance context, invocationId, outcome `succeeded`/`failed`/`cancelled`, safe message | Complete existing invocation once; ignore duplicates/unknown IDs |
| `host.log` notification | Level, stable code, safe message, optional instance context | Rate-limit/redact and store bounded diagnostics |
| `host.secret.read` request | Instance context and declared secret name | Resolve only that instance's approved reference or return `SECRET_UNAVAILABLE` |

There is no host HTTP client, generic file-read API, command-launch API, or navigation/configuration mutation API on this channel in v1. A future web-service plugin performs its own I/O and must handle its own network deadlines; no such plugin is part of this implementation.

### 13.4 DisplayState and composition

A display update is a **complete snapshot**, not a patch. Omitted optional fields reset to absence. Sequence numbers increase within an instance epoch across both push updates and refresh responses; older/duplicate states are discarded. A polling response must contain its sequence along with its state. Metadata timestamps from the plugin are informational; freshness uses the host's monotonic receipt time.

```json
{
  "value": 42.5,
  "unit": "%",
  "label": "Example",
  "status": "ok",
  "message": null,
  "progress": 0.425,
  "image": null
}
```

| Field | Constraints |
|---|---|
| `value` | Finite number, string ≤256 characters, or null |
| `unit` | String ≤32 characters or null |
| `label` | String ≤128 characters or null |
| `status` | `ok`, `warning`, `error`, or `unavailable` |
| `message` | Diagnostic string ≤512 characters or null; never used as executable content |
| `progress` | Number 0–1 or null; rendered as an optional core progress bar |
| `image` | Null; a manifest-registered package asset key; or a PNG image object with base64 bytes |

The image union is explicitly `null`, `{"kind":"asset","assetKey":"registered-key"}`, or `{"kind":"png","dataBase64":"..."}`. No other shape is valid.

PNG updates are limited to 128 KiB decoded file bytes and 1 million decoded pixels, then normalized through the safe image pipeline. Package asset keys resolve only to declared package assets. Plugin-supplied filesystem paths, external image URLs, SVG, HTML, or drawing code are not accepted on this channel.

`appearance.dynamic` controls user-approved display binding:

```json
{
  "enabled": true,
  "textTemplate": "{{label}}\n{{value}} {{unit}}",
  "allowedOverrides": ["text", "progress"],
  "numberDecimals": 1,
  "staleAfterMs": 10000
}
```

Allowed overrides are `text`, `icon`, and `progress`; default is `text` only. The host substitutes only `{{label}}`, `{{value}}`, `{{unit}}`, and `{{status}}` as literal formatted strings. Unknown tokens fail validation. There are no expressions, property traversal, shell expansion, or code execution. Static user appearance is the fallback, and static icon stays unless `icon` override is explicitly allowed.

The composition order is background → static/permitted dynamic icon → static/permitted dynamic text → permitted progress bar → core busy/error/stale/disabled indicator. Plugin output cannot replace core safety indicators or the generated Back button.

The host declares data stale after `staleAfterMs`; default is the greater of 10 seconds and three effective refresh intervals. Valid but unchanged snapshots refresh freshness without causing USB writes. Preserve the last valid value with a visible stale/error marker rather than displaying it as current. Before the first valid update use static fallback plus a loading/unavailable marker.

### 13.5 Refresh, failures, and limits

Contributions declare `updateMode` as `poll`, `push`, or `both`. Polling defaults to 1,000 ms; effective intervals cannot be below 250 ms or the contribution's declared minimum. Do not overlap refresh calls for the same instance. Skip missed ticks rather than building a backlog. Hidden instances receive no polls.

Use a display-update token bucket of 4 updates/second per instance with a burst of 8; coalesce excess valid display states. Limit a process to 128 protocol messages/second with a short bounded burst. Repeated flooding for 10 seconds, oversized frames, malformed framing, or sustained unreadable output stops/quarantines the process.

Ping every 10 seconds when running; allow 2 seconds to respond. Three consecutive ping failures restart the process. Automatic restart delays are 1, 2, and 4 seconds, at most three restarts in 60 seconds. Then open a circuit breaker until explicit retry. Stable operation for 60 seconds resets the failure counter.

On shutdown give 2 seconds for the protocol shutdown, 2 seconds for graceful process termination, then force termination of the managed process group/unit. A plugin crash never retries a prior invocation automatically. Missing plugins remain missing; do not download replacements.

### 13.6 Secret storage

Implement `SecretStore` with the Linux desktop Secret Service/keyring backend and an in-memory test double. The Python keyring project documents Secret Service integration. [S12] An absent/locked backend returns a visible unavailable state; there must be no plaintext-file fallback.

The Configurator can set/replace a secret but should not redisplay stored plaintext by default. The Controller releases a value only on a matching `host.secret.read` request from the approved package/instance and declared secret name. Keep secrets out of configuration bundles, manifest files, CLI arguments, general logs, and diagnostics. Exported references must be rebound locally after import.

These rules limit accidental exposure and host-mediated access. They do not make untrusted same-user plugins safe.

## 14. Platform installation and operations

**OPS-01.** Install both applications with separate entry points and independently testable packages. Use a locked virtual environment/package installation, desktop launcher, and documented start/stop/status commands. Do not require Docker, Kubernetes, a browser server, PostgreSQL, or Elgato's application.

**OPS-02.** Install the minimum USB-access rule needed for the selected model/backend. The documented library backend requires suitable Linux device permissions. [S13] Prefer active-seat `uaccess` or a narrowly scoped group rule, rather than world-writable mode 0666 or running the application as root. Rule matching must cover the actual chosen libusb/hidraw path, not an unrelated node.

**OPS-03.** The only privileged installation step is installing system dependencies/USB rules when needed. Application state belongs to the user. Explain replug/relogin requirements and diagnose permission problems distinctly from a busy device.

**OPS-04.** Offer opt-in start at graphical login through a systemd user service and session integration. Import/refresh only allowlisted graphical-session variables. Do not enable linger or system-wide boot execution by default. Closing the Configurator leaves the Controller running; explicitly stopping the Controller stops its managed tasks and plugins.

**OPS-05.** The service must not apply cgroup shutdown policies that accidentally kill independently launched application-mode programs. Validate actual lifecycle behavior under the installed service, not just foreground development runs.

**OPS-06.** Provide an idempotent installer, dependency/device diagnostics, and uninstall instructions. Uninstall preserves configurations/assets by default; deletion requires an explicit separate operation. Do not modify unrelated udev rules or disable another controller automatically.

## 15. Reliability, performance, and security budgets

These are implementation limits/acceptance targets, not measured promises about the user's hardware.

| Area | Default requirement / target |
|---|---|
| Layout transition | Full-page redraw target ≤500 ms after assets are cached; measure on the real XL |
| Input dispatch | Host-side p95 release-to-dispatch ≤50 ms in simulator tests under normal workload; exclude external program startup |
| Reconnect | Rediscover/repaint target ≤5 seconds after usable USB access returns |
| Image writes | Background maximum 4 changes/second/key; coalesce to newest, prioritize page transitions |
| Queues | Bounded input/control queues; at most one pending replacement image per key |
| Rendering cache | 64 MiB default in-memory limit; 256 MiB default on-disk cache limit |
| Configuration | Maximum 8 MiB JSON, 1,024 pages, 64 levels; text ≤1,024 characters/button |
| Active plugins | Maximum 16 worker processes and 32 visible instances for this model |
| Tasks | Global maximum 16 running managed tasks; per-button limit from execution configuration |
| History | Latest 200 execution records; outputs follow the separate per-run byte limits |
| Idle resource use | Measure/report Controller CPU and memory; investigate sustained idle CPU above 1% of one core |

**SEC-01.** Treat imported configuration, images, manifests, plugin messages, and output strings as untrusted data. Apply schema validation and bounds at every process boundary.

**SEC-02.** Restrict local files/sockets to the current user. Neither Unix-socket permissions nor plugin manifests isolate mutually untrusted applications under the same UID. Make this threat-model boundary explicit in documentation.

**SEC-03.** No executable action is triggered by startup, image import, editing, previewing, configuration validation, ordinary Apply, navigation alone, or reconnect. An approved plugin may start when its visible binding is applied; that code execution is covered by separate plugin trust approval.

**SEC-04.** Review new/changed/imported executable actions before enabling them. Do not attempt to determine that an arbitrary user script is harmless. A click intentionally authorizes the configured local command, which may itself perform privileged or destructive work according to its own permissions.

**SEC-05.** Use structured, bounded logs with event codes and correlation IDs. Redact secrets, authentication headers, full environment values, and sensitive plugin payloads. Diagnostic export is opt-in and must state what it contains.

## 16. Automated acceptance tests

Implement a traceability matrix linking requirements to tests. Tests requiring USB hardware must be separate from simulator/CI tests. Never report an unperformed hardware test as passed.

| ID | Acceptance scenario |
|---|---|
| AT-01 | Start Controller alone; a saved configuration remains usable after Configurator exits. |
| AT-02 | Configurator opens/saves an offline draft; no USB device is opened by its process. |
| AT-03 | All 32 key indexes map correctly; XL geometry/orientation is verified separately on hardware. |
| AT-04 | Icon-only, text-only, combined, disabled, blank, and fallback buttons render correctly. |
| AT-05 | PNG/JPEG/WebP/BMP/GIF-first-frame/SVG-subset imports normalize correctly; EXIF rotation and alpha are covered. |
| AT-06 | Malformed images, pixel bombs, oversize files, external SVG references and dangerous XML are rejected without crashing either application. |
| AT-07 | Shared-renderer preview equals the upright pre-JPEG runtime composition; JPEG/native orientation tests use suitable tolerances. |
| AT-08 | A 64-level section tree traverses forward/back correctly; every child reserves key 0; root retains 32 available positions. |
| AT-09 | Cycles, duplicate IDs/slots, orphan pages, invalid parents, dangling targets, and excess limits fail Apply. |
| AT-10 | Move/duplicate/delete subtree updates all identities and references without affecting the original unexpectedly. |
| AT-11 | Navigation redraws unused keys black; no previous-page icon remains accidentally active. |
| AT-12 | Key-down before Apply/navigation and key-up afterward does not execute the new binding. |
| AT-13 | Held/repeated reports, simultaneous keys, disconnect while held, queue overflow, and resumed state do not cause duplicate/phantom clicks. |
| AT-14 | An executable path containing spaces and literal arguments including `;`, `$()`, and `*` arrive unchanged; nothing is shell-expanded by the Controller/launcher. |
| AT-15 | Direct programs and explicit-interpreter scripts run; unreadable/non-executable/missing paths produce useful errors. |
| AT-16 | Managed tasks time out/cancel without freezing rendering, and noisy stdout/stderr remain bounded without deadlock. |
| AT-17 | Application-mode launch preserves the graphical session and survives Controller restart under the installed user service. |
| AT-18 | Application launch acceptance is not falsely presented as completion of the application's business operation. |
| AT-19 | Concurrency limits work; restart/reconnect/lost acknowledgement never automatically repeats an action. |
| AT-20 | Interrupted/failed Apply retains the old revision or recovers the fully committed new one; there is no half-written active document. |
| AT-21 | Two editors applying the same expected revision produce one success and one conflict. |
| AT-22 | Apply while USB is disconnected persists successfully and reports pending synchronization; reconnect draws the correct page. |
| AT-23 | Corrupt current configuration recovers without overwriting evidence or executing any actions. |
| AT-24 | Export/import preserves assets/text/tree; rejects traversal/expansion attacks and never transfers secret values or trust approval. |
| AT-25 | Socket refuses another UID and unsafe path ownership; malformed/oversized frames are handled safely. |
| AT-26 | A deterministic test-only plugin fixture completes initialize/create/visibility/refresh/display/shutdown without core modifications. |
| AT-27 | Two instances of one fixture have independent settings/epochs and cannot overwrite each other's display. |
| AT-28 | Plugin push/poll stale data, invalid values/images, late responses, old activations and out-of-order sequences are handled as specified. |
| AT-29 | A metric-style fixture display and an independent local execution action coexist on one button. |
| AT-30 | Plugin crash/hang/flood affects only its instances; Back and unrelated actions remain responsive; restart limits are enforced. |
| AT-31 | Plugin invocation accepts/completes/cancels as specified; hidden-page completion cannot corrupt another key; no automatic retry follows a crash. |
| AT-32 | Unapproved/changed/missing/incompatible plugins never execute; bindings and fallback appearance are preserved. |
| AT-33 | Generic settings forms handle supported schemas; invalid settings and unsupported constructs give controlled errors. |
| AT-34 | Secret requests are scoped by instance/package; absent/locked keyring gives no plaintext fallback; exports/logs do not contain secrets. |
| AT-35 | English/French UI, accented paths/text, fallback fonts, and localized Back labels work. |
| AT-36 | Installer is repeatable; permissions/replug behavior and uninstall data preservation are verified on clean Ubuntu. |

Use synthetic temporary scripts and static protocol fixtures in tests; they must not inspect actual CPU usage, disks, remote APIs, or user secrets. Simulator action execution is disabled by default; enabling real subprocess tests requires an explicit test option.

## 17. Repository structure and required deliverables

A single repository is appropriate; the two applications remain separate executables and packages. Suggested structure:

```text
README.md
AGENTS.md
pyproject.toml
<dependency lockfile>
docs/
  REQUIREMENTS.md
  ARCHITECTURE.md
  CONFIGURATION.md
  LOCAL_API.md
  PLUGIN_API.md
  SECURITY.md
  INSTALLATION.md
  VERIFICATION.md
schemas/
  configuration-v1.schema.json
  plugin-manifest-v1.schema.json
  local-api-v1/
  plugin-api-v1/
src/
  sdl_core/
  sdl_controller/
  sdl_configurator/
  sdl_cli/
tests/
  unit/
  integration/
  gui/
  fixtures/plugins/
  hardware/
packaging/
  systemd-user/
  desktop/
  udev/
scripts/
```

**DEL-01.** Ship actual JSON schemas and valid/invalid contract fixtures for configuration, manifests, API messages, and DisplayState. Examples must be parsed and validated by tests; documentation alone is not a substitute for the extension host.

**DEL-02.** Deliver typed code, formatting/linting, automated tests, reproducible build/test commands, a dependency/license inventory, and a simulator. CI must not require the physical device or access to the user's home files.

**DEL-03.** `AGENTS.md` must state module boundaries, prohibited implicit shell execution, plugin scope exclusions, shared-renderer requirements, and the rule against claiming unperformed hardware verification.

**DEL-04.** `VERIFICATION.md` records exact commands, versions, results, hardware-tested versus simulator-tested status, known limitations, and remaining manual checks. Screenshots may document editor behavior but cannot prove USB correctness.

### 17.1 Implementation milestones

| Milestone | Required exit condition |
|---|---|
| M1 — Contracts and core | Domain/schema validation, tree operations, shared renderer, asset import, simulator; no hardware dependency |
| M2 — Standalone Controller | Device adapter, input/navigation, persistence, execution lifetimes, reconnect, local API and tests |
| M3 — Configurator | Complete draft/apply editor, consistent previews, section management, execution properties, offline support |
| M4 — Extension host | Manifest discovery/trust, generic forms, subprocess protocol, display/action separation, lifecycle, secrets and fixture conformance tests; no production plugins |
| M5 — Packaging and verification | Repeatable installation/startup, English/French UI, security/fault tests, documented physical acceptance procedure |

At each milestone provide changed modules, tests executed/results, unresolved defects, and user-required hardware checks. Do not advance by silently replacing a mandatory requirement with a stub.

### 17.2 Definition of done

Both applications run independently; every in-scope requirement has an implementation and test or an explicitly recorded hardware-verification step; the real deck displays correct images and reports keys when locally tested; navigation cannot strand the user; program-launch behavior matches the selected lifecycle; and a new conforming test plugin can be integrated without editing core code.

No CPU, storage, web-service, or other production plugins are shipped. No physical test is marked passed without evidence. Any deviation from this specification is listed explicitly, with its reason and effect.

## 18. Worked minimal configuration

This example is a complete portable document with no imported image assets. It demonstrates one child section and a direct task. The runtime revision/approvals live outside the document. `/usr/bin/true` is illustrative and must still pass local path validation and normal executable review.

```json
{
  "schemaVersion": "1.0",
  "configurationId": "d3f3ec6b-1282-4a92-8f45-811328c31db4",
  "name": "My Stream Deck",
  "target": {
    "adapterId": "elgato.streamdeck",
    "vendorId": 4057,
    "productId": 108,
    "serialNumber": null
  },
  "layout": {"rows": 4, "columns": 8, "keyWidth": 96, "keyHeight": 96},
  "rootPageId": "76a00fdc-d26b-448d-86f9-8658a5f0a5d7",
  "settings": {"brightnessPercent": 50, "locale": "en"},
  "pages": [
    {
      "id": "76a00fdc-d26b-448d-86f9-8658a5f0a5d7",
      "name": "Home",
      "parentPageId": null,
      "buttons": [
        {
          "id": "172f8618-dbe9-43e0-925e-e1cde7918f23",
          "keyIndex": 0,
          "enabled": true,
          "appearance": {
            "iconAssetId": null,
            "text": "Tools",
            "layout": "textOnly",
            "backgroundColor": "#000000",
            "textColor": "#FFFFFF",
            "fontFamily": "DejaVu Sans",
            "fontSizePx": 16,
            "fontWeight": "normal",
            "textAlign": "center",
            "imageFit": "contain",
            "paddingPx": 4,
            "dynamic": null
          },
          "action": {
            "type": "core.navigate",
            "pageId": "b77f7dd0-314b-4a94-b0cd-39a9d3b8214e"
          },
          "pluginBinding": null,
          "extensions": {}
        }
      ]
    },
    {
      "id": "b77f7dd0-314b-4a94-b0cd-39a9d3b8214e",
      "name": "Tools",
      "parentPageId": "76a00fdc-d26b-448d-86f9-8658a5f0a5d7",
      "buttons": [
        {
          "id": "a915e352-9a7a-4e6e-a734-919f3876e7d1",
          "keyIndex": 1,
          "enabled": true,
          "appearance": {
            "iconAssetId": null,
            "text": "Test task",
            "layout": "textOnly",
            "backgroundColor": "#000000",
            "textColor": "#FFFFFF",
            "fontFamily": "DejaVu Sans",
            "fontSizePx": 16,
            "fontWeight": "normal",
            "textAlign": "center",
            "imageFit": "contain",
            "paddingPx": 4,
            "dynamic": null
          },
          "action": {
            "type": "core.execute",
            "path": "/usr/bin/true",
            "arguments": [],
            "interpreter": null,
            "workingDirectory": "/tmp",
            "environment": {},
            "mode": "task",
            "timeoutMs": 120000,
            "concurrency": "ignoreWhileRunning",
            "maxParallel": 1
          },
          "pluginBinding": null,
          "extensions": {}
        }
      ]
    }
  ],
  "extensions": {}
}
```

The generated Back key occupies key 0 of Tools and is intentionally absent from the JSON. All unassigned positions are blank. No command runs until the approved task button is deliberately activated.

## 19. Primary references

References were consulted on September 28, 2026. They substantiate hardware and library/protocol facts, not the product's chosen requirements or unmeasured performance targets. The implementer must verify APIs against the versions actually pinned in the project.

- **[S01] Elgato — Stream Deck XL HID reference.** Model identification, VID/PID, grid, image size, and native upload orientation. https://docs.elgato.com/streamdeck/hid/stream-deck-xl/
- **[S02] python-elgato-streamdeck — project repository.** Direct device-control library, supported device family, and license information. https://github.com/abcminiuser/python-elgato-streamdeck
- **[S03] python-elgato-streamdeck — device API.** Capabilities, key callbacks, thread context, image formats, and brightness. https://python-elgato-streamdeck.readthedocs.io/en/stable/modules/devices.html
- **[S04] Qt for Python documentation.** PySide6 desktop application framework. https://doc.qt.io/qtforpython-6/
- **[S05] Pillow — image file formats.** Raster decoder/encoder support. https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html
- **[S06] CairoSVG documentation.** SVG conversion facilities and safety considerations. https://cairosvg.org/documentation/
- **[S07] Python 3.12 — subprocess documentation.** Argument-vector process launch, shell behavior, environment and subprocess handling. https://docs.python.org/3.12/library/subprocess.html
- **[S08] systemd — control group interfaces.** Services/scopes and transient unit lifecycle. https://systemd.io/CONTROL_GROUP_INTERFACE/
- **[S09] freedesktop.org — XDG Base Directory Specification.** User configuration/data/state/cache/runtime conventions. https://specifications.freedesktop.org/basedir/latest/
- **[S10] JSON-RPC 2.0 specification.** Request/response/notification envelopes and standard errors. https://www.jsonrpc.org/specification
- **[S11] JSON Schema Draft 2020-12.** Schema vocabulary and validation framework. https://json-schema.org/draft/2020-12
- **[S12] Python keyring documentation.** System credential-store and Secret Service integration. https://keyring.readthedocs.io/en/latest/
- **[S13] python-elgato-streamdeck — Linux HID backend setup.** Linux backend dependencies and USB permission configuration. https://python-elgato-streamdeck.readthedocs.io/en/stable/pages/backend_libusb_hidapi.html
