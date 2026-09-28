# Configuration contract

Portable document schema: `1.0`. Runtime revision: an independently increasing integer owned by the Controller. Use `examples/demo.json` and `examples/execute-demo.json` as complete examples; do not edit Controller-owned revision files directly.

## Appearance

Every button has a UUID, `keyIndex`, `enabled`, `appearance`, one `action`, optional `pluginBinding`, and `extensions`. Index = `row * columns + column`; for this model rows 0–3, columns 0–7, keys 0–31.

`appearance` includes:

```json
{
  "iconAssetId": null,
  "text": "My button",
  "layout": "iconAboveText",
  "backgroundColor": "#000000",
  "textColor": "#FFFFFF",
  "fontFamily": "DejaVu Sans",
  "fontSizePx": 16,
  "fontWeight": "normal",
  "textAlign": "center",
  "imageFit": "contain",
  "paddingPx": 4,
  "dynamic": null
}
```

Layouts are `iconOnly`, `textOnly`, `iconAboveText`, `textOverlay`. Fit is `contain`, `cover` or `stretch`. Text aligns left/center/right, preserves line breaks and French accents, shrinks to 8 pixels and then wraps/ellipsizes. This release resolves DejaVu Sans normal/bold, falling back to Liberation Sans; other family names produce a validation warning and the same deterministic fallback. It does not promise arbitrary emoji/font/script coverage.

Import an image using `sdlctl asset-import /absolute/path/image`. Copy the returned `sha256:...` ID into `iconAssetId`. Imports are decoded by content, orientation corrected, alpha preserved through composition, and stored as originals plus bounded canonical PNGs. Native JPEG encoding/rotation occurs only at the device adapter. Restricted SVG supports common shapes, paths, groups, transformations, local gradients and clipping; it rejects text, images, scripts, DTDs/entities and external resources.

An absent button is black and inactive. Disabled buttons remain visible with a diagonal indicator. An action without icon/text receives a visible core fallback symbol. Core execution state is represented by a small blue/green/red status dot. `dynamic` and `pluginBinding` are preserved but are not evaluated in this release.

## Navigation

Pages form a rooted tree. The root has `parentPageId: null`. Every other page needs exactly one `core.navigate` button in its direct parent:

```json
{"type": "core.navigate", "pageId": "CHILD_PAGE_UUID"}
```

The snippet above is illustrative: replace `CHILD_PAGE_UUID` with the child's actual UUID. Every child page reserves key 0 for generated Back. Do not store a user button at that key. Back goes to the direct parent. `{"type":"core.home"}` goes directly to the root. The root has 32 user slots; child pages have 31 plus Back. Up to 1,024 pages and 64 levels (root included) are supported.

Changing the page invalidates held presses and waits until the new display frame has been written before accepting fresh clicks. Applying a configuration retains the current page if it still exists in a valid tree; otherwise it selects root. Reconnect retains the current page; a process restart starts at root.

## Execute a program or script

A complete example action:

```json
{
  "type": "core.execute",
  "path": "/usr/bin/printf",
  "arguments": ["Stream Deck task completed\n"],
  "interpreter": null,
  "workingDirectory": "/tmp",
  "environment": {},
  "mode": "task",
  "timeoutMs": 5000,
  "concurrency": "ignoreWhileRunning",
  "maxParallel": 1
}
```

For a script without executable permission, specify an explicit interpreter:

```json
{"path": "/usr/bin/python3", "arguments": ["-u"]}
```

Place that object in `interpreter`, set the action's `path` to the absolute script path, and keep script arguments in the outer `arguments` list. For Bash scripts use the actual interpreter path explicitly; there is no automatic choice based on extension. Direct execution needs executable permission; interpreted scripts need read permission. The Controller does not chmod scripts or prompt for sudo.

No implicit shell, glob expansion, `$HOME` replacement, token substitution or argument-string splitting is performed. An argument containing spaces, `;`, `*` or `$` remains one literal argument. Stored paths are absolute; explicitly expand `~` before saving.

### Task mode

`mode: task`, timeout 1,000–86,400,000 ms. Stdin is noninteractive. Exit code, cancellation and timeout are tracked. Stdout/stderr are continuously drained while retaining at most 64 KiB each plus a truncation marker. Use `sdlctl executions --include-output` to opt into displaying these buffers; normal events/logs/persistent history omit the output.

A supervisor observes a private Controller-lifetime pipe and terminates the task's process group on shutdown/crash, allowing two seconds before force termination. The installed user service adds cgroup cleanup. Programs that deliberately detach into unrelated sessions/managers are outside the managed-task contract; use application mode for independent long-lived applications.

### Application mode

Set `mode: application` and `timeoutMs: null`. A separate transient **systemd user-service** executes the program with explicit session context. It is not bound to the Controller unit and is intended to survive Controller restart. The result is `launched`, not “the application's work succeeded.” It confirms acceptance of the launcher service; later application failures are not task completion events. An unavailable user manager gives an error, never a fallback with different lifetime semantics.

GUI applications need a usable graphical session. Start with `sdl-session --start` or refresh via `sdl-session`. Window focus/keyboard injection are not implemented. There is no guarantee that a program will create another window when it already has its own single-instance behavior.

### Concurrency and review

Default `ignoreWhileRunning` uses `maxParallel: 1`. `parallel` allows 1–4 in-flight activations per button; the global ceiling is 16. Application-mode suppression ends after launch acknowledgement, not when a window eventually closes.

Before enabling new/changed executable actions, review the displayed paths/arguments and explicitly approve. Editing appearance alone does not need another approval for an unchanged action. Applying or importing never executes the action. Imported bundles receive new IDs, so prior approvals do not transfer. Program contents can still change at the approved filesystem path; approval is not a code signature or sandbox.

## Portable backups

Export includes configuration and the exact original bytes of referenced images, with a SHA-256 manifest. Import checks paths, duplicates, symlinks, checksums and expansion limits; it does not extract arbitrary paths. It creates a new draft, regenerates identities, clears selected serial/secret references, imports validated images and does not Apply.

Raw JSON Apply is a local authoring operation, not an archive import. Use bundle import for untrusted/shared profiles to ensure identities and approval context are renewed.
