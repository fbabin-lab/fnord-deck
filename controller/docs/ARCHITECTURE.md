> Version 0.2.0 adds approved polling/display plugins and API 1.1. The [current plugin guide](../../docs/PLUGIN-HOST-0.2.0.md) supersedes earlier plugin-deferred statements below. Run both this document's baseline acceptance checks and the plugin guide's additional checks.

# Architecture

```text
sdlctl / future Configurator
       | same-UID Unix socket, framed JSON-RPC 1.0 application API
       v
Controller (asyncio)
  |-- ConfigurationRepository: revisions, durable pointer, idempotency, approvals
  |-- Renderer + AssetStore: shared upright compositions and bounded conversion workers
  |-- InputGate: release-based activation, epoch/generation/revision/identity guards
  |-- action_handlers registry: none / navigate / home / execute / deferred plugin invoke
  |-- ActionExecutor -> managed task supervisor OR independent systemd application unit
  |-- DeferredExtensions: honest unavailable status, reserved future extension seam
  `-- DeviceAdapter -> serialized USB worker -> python-elgato-streamdeck -> XL
```

## Concurrency

One asyncio event loop owns navigation and applied runtime state. A single USB executor serializes open/write/brightness/close operations. A single rendering executor keeps raster work out of HID callbacks. Image imports use a short-lived process with memory/CPU/file-size/wall limits and an application-level one-at-a-time conversion lock.

The HID adapter wraps the pinned library's full control-report reader to observe actual complete key state. This narrow use of `_read_control_states`/`ControlType.KEY` is intentionally isolated in `device.py` and covered by fake-library contract tests. Public changed-key callbacks alone do not establish a reliable initial released baseline. Upgrading `streamdeck` must recheck this private adapter seam against the release source and run physical reconnect/held-key tests.

The hardware thread puts small timestamped snapshots into a bounded mailbox, with only one scheduled event-loop drain. Overflow cancels presses and uses the newest state only as a baseline. Action launches/rendering/filesystem operations never execute in that callback.

A press is matched to epoch, page generation, revision and button identity. Redraw inhibits clicks until the frame is synchronized. Per-key content hashes avoid unchanged USB writes. Status redraws are coalesced to at most four per second; page transitions bypass that background delay. Native encoding is cached in the hardware adapter. A 64 MiB logical render cache is regenerable; this release does not maintain a disk render cache.

## Persistence and transaction boundary

Prepare validates schema, IDs, tree, limits and asset integrity, and renders the candidate current page. Apply requires expected revision + operation UUID. Review tokens are transient and content-hash-bound. New revision data is written/fsynced before atomically replacing/fsyncing the current pointer. The pointer also contains the last 100 Apply outcomes and active action approvals. Only after durable commit is the runtime updated and repaint requested. No command is launched by this transition.

Each explicit execution receives a durable intent before subprocess dispatch. A restarted unresolved intent becomes `unknownAfterRestart`. Recent test-operation IDs are deduplicated; neither errors nor lost responses trigger retry. This is not a claim of exactly-once execution of arbitrary external programs.

## Future Configurator

Implement another entry point/package, not a GUI inside the Controller. Reuse the core schema, validation, rendering and asset conversion rules. Keep drafts/editor preferences separate. Upload original images via the chunk API and Apply only after explicit user action. The editor must never import/open USB adapters, execute programs on ordinary grid clicks, or write `current.json`/revision documents itself.

Use `system.hello` capability flags rather than assuming plugins are available. Fetch a snapshot after subscribing or after an event-sequence gap. Preserve the document and expected revision in the draft. A stale Apply must leave the draft intact.

## Deferred plugin extension seam

`ExtensionHost` is a typed boundary; `DeferredExtensions` currently reports `supported: false`. The schema retains `pluginBinding`, `appearance.dynamic` and namespaced `extensions`. A reserved `core.plugin.invoke` handler fails with `PLUGIN_DEFERRED`, while an independent `core.execute` action on the same button remains usable. No plugin package discovery, subprocesses, networking, secret storage, metric acquisition or arbitrary imports occur.

The future implementation should follow the original specification's separate plugin processes, manifests/approval fingerprints, JSON-RPC lifecycle, scoped instance epochs, complete display snapshots and bounded update queues. Route dynamic visuals through the same shared Renderer. Do not grant plugins USB handles, other buttons, navigation mutation, or approval rights. Independent appearance/action is already present; extending the host should not require changing the Configurator's device ownership or the core navigation model.

## Extending hardware/actions

Hardware constants are centralized in `Capabilities`/`XL`, the target schema and the Elgato adapter. Add another adapter/model descriptor and explicitly supported schema target before accepting another device; do not silently match by marketing name. Action implementations are registered by namespaced type. Unknown executable action types remain validation errors until explicitly supported.
