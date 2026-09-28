# Configurator architecture and AI handoff

## Boundaries

```text
Qt main thread
  MainWindow / Inspector / dialogs / KeyButton / section tree
       |
       +-- Draft: pure transactional document editing + bounded undo/redo
       |
       +-- Bridge: queued Qt signals <-> one asyncio worker thread
                    |
                    +-- Workspace: editor-only drafts, assets, backups
                    +-- Backend: review/apply, test safeguards, pull, rendering
                           |
                           +-- unchanged sdl_core: schema, assets, renderer, bundles
                           +-- Client: same-UID Unix socket, API 1.0 framing
                                          |
                              existing Controller process -> USB / action executor
```

The Configurator package deliberately contains **no `sdl_controller` or StreamDeck dependency**. Its installer does not change Controller deployment. Shared `sdl_core` is an exact source snapshot from Controller 0.1.0, not an alternative implementation of the rendering/schema rules. Every copied file is hashed in `shared-core-origin.json`; `scripts/check-source.py` checks it. The separately installed private virtual environments avoid package-module conflicts. A later release should extract and version this shared core as a dedicated distributable, with explicit compatibility negotiation.

The controller's image converter launches a bounded helper process; this is asset processing, not execution of configured button actions. The Configurator source itself never invokes a shell, imports an action launcher, or opens USB.

## State model

`Draft.document` is the editable document. `saved_hash` tracks explicit local saving independently from `base_revision` and `base_hash`, which track the last confirmed applied Controller document. `source_socket` binds that baseline to a Controller endpoint. Local edits do not adjust the baseline. Applying changes does not imply an explicit Save draft. Reopening a saved workspace file retains its baseline; importing external JSON/bundles does not.

A change clones the document, modifies it, validates the complete candidate, and atomically replaces the in-memory document only on success. Undo/redo keeps serialized prior states, with a 100-entry/64 MiB undo cap and short-time-window coalescing for appearance typing. Structural edits are not coalesced. The Controller/core schema additionally enforces document bounds and page topology.

Section topology remains a tree: one parent, one direct-parent navigation button per nonroot page, depth cap 64, generated Back at key index 0. Page IDs and button IDs are stable on moves and regenerated on copies. Copying a subtree clones its internal links; moving under a descendant fails before modifying anything. Deletion of a navigation button removes its subtree in one undoable edit. The root is not moved/copied as a child.

The UI uses one-based labels; JSON remains zero-based. Browsing a section changes only `MainWindow.page_id`. `runtime.navigate` is sent only through the explicit **Show this applied section on deck** command and only when the draft matches the Controller baseline.

## Threading and previews

All Qt objects and widgets remain on the main thread. One asyncio worker thread handles API requests, image import, bundle I/O, and serialized file operations. Rendering uses the unchanged Pillow renderer through `asyncio.to_thread` with a backend lock. Images arrive at the UI as PNG bytes. Qt loads those bytes into pixmaps; it does not implement a second text/layout algorithm.

Preview updates are debounced and tagged with document identity, generation, and page. Stale completed renders cannot replace newer previews. Recovery saves are coalesced and serialized; a final recovery write completes before an orderly window close. Local requests are canceled on exit, but the Controller continues independently. Disk-full/permission failures are surfaced, not silently marked as saves.

Large structural validation still occurs synchronously in the draft model. The maximum-size document and high-DPI desktop performance were not benchmarked in this release. Do not claim constant UI latency for all allowed documents.

## Apply sequence

1. Freeze a deep copy of the draft. Validate its structure and local asset references.
2. Read current Controller revision. Reject a stale known baseline; leave the draft unchanged.
3. Verify cached originals; upload missing originals through bounded public asset APIs.
4. Ask the Controller to validate and issue a content-bound review token. Check its content hash against the frozen draft.
5. Display configuration replacement context, executable paths/arguments/interpreters, environment variable **names**, and warnings. Checkbox starts unchecked.
6. Only after user confirmation, persist an operation record locally, then issue `configuration.apply` with expected revision, unique operation ID, review token and confirmation.
7. On success update the applied baseline and display synchronization status separately. On known noncommit rejection clear the local pending record. On ambiguous loss keep it.

No Apply or asset upload executes button programs. Asset import can leave an unused original in Controller storage when review is canceled; this is intentional staging, not an applied layout change. There is no cleanup API in Controller 0.1.0.

Stale revision replacement requires explicit conflict consent and then a **fresh review** bound to the newest revision. No automatic merge or invisible force-overwrite exists. A race between review and Apply is still protected by the Controller's optimistic revision check.

Recovery preserves document/revision/operation ID/socket but not review tokens or `confirmed`. Replaying that ID is an explicit user action. The Controller's durable deduplication precedes revision/approval checks, so an already-committed Apply returns its previous result. Uncommitted executable changes cannot gain approval from the recovery path. A non-executable configuration may be committed by that explicit retry, which the recovery dialog explains.

## Execution sequence

Grid clicks, double-clicks, editor navigation, image conversion, saving, validation, import/export, and Apply never call `execution.test`. The explicit run control is gated on an enabled executable button, live matching baseline, unpaused Controller, and execution availability. Backend preparation rereads the applied configuration and compares its complete document hash, even if a stale UI status looked acceptable. A confirmation lists the exact action; environment values are hidden in the prompt. Only then is `execution.test` issued with a unique operation ID. It is never automatically retried. The Controller independently checks revision and execution approval.

## Extension seams: deferred plugins

Existing `pluginBinding`, `appearance.dynamic`, and namespaced `extensions` survive ordinary edits. Clipboard copying renews plugin instance IDs along with button/page IDs. External import clears secret references using the existing shared bundle logic. Dynamic data is neither fetched nor rendered as live plugin output; the normal static fallback remains.

Future work should add a plugin settings panel/manifest-driven forms and Controller API lifecycle support, not load third-party code into the editor process. Plugin manifest validation, capability declarations, secrets, polling/push, versioning, timeout handling and process isolation belong to the later plugin milestone. This release provides no new host and no CPU/storage/web-service plugins.

The UI currently targets an 8×4 XL. A later `DeviceLayoutView` can replace the fixed grid; the document/schema/core and transport are already distinct. Do not claim additional models without adapting and testing both applications.

## Layout of source

- `document.py`: transactional model and import/copy identity changes.
- `storage.py`: XDG locations, workspace lock, bounded file reads, local persistence and assets.
- `transport.py`: independent same-UID framed JSON-RPC client, chunked asset transfer.
- `backend.py`: Controller use cases and identical core preview rendering.
- `bridge.py`: worker thread/Qt signal adapter.
- `widgets.py`, `inspector.py`, `dialogs.py`, `window.py`: presentation and explicit gestures.
- `i18n.py`, `locales/*.json`: English/French messages.
- `main.py`: startup, recovery, language, private workspace and CLI diagnostics.
- `tests/test_integration.py`: unchanged external Controller subprocess, isolated XDG/runtime, no USB.

## Change requirements for subsequent AI implementation

Read this document and the Controller API before editing. Preserve the separation of Save, Apply and Run. Do not add implicit command execution, unreviewed imports, direct Controller-file writes, USB ownership, automatic write retries, or executable retry loops. Maintain core-renderer parity and backward-compatible API 1.0 semantics unless changing and releasing both applications together. Add tests for every bug fix; run Qt tests on an actual dependency-equipped environment before reporting desktop validation.
