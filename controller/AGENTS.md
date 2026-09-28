# Instructions for implementation agents

## Boundaries
- `sdl_core`: configuration contracts, strict JSON, validation, rendering, assets, bundles, protocols. No GUI widgets and no USB ownership.
- `sdl_controller`: single hardware owner, asyncio orchestration, persistence, framed local API, input state machine, launches and session lifecycle.
- `sdl_cli`: a client of the same local API; never open the deck or write Controller-owned applied state directly.
- Future `sdl_configurator`: a separate executable, sharing renderer/models; edits drafts and applies through IPC.
- Plugins are explicitly deferred in version 0.1.0. Preserve binding/extension fields, display fallback content, and advertise capabilities honestly. Do not implement CPU/storage/web-service plugins while working on this milestone.

## Non-negotiable safety/correctness rules
- No implicit shell, `eval`, executable templates, splitting command strings or substituting future plugin output into commands.
- Preserve argument arrays literally. Image ingestion, preview, validation, Apply, startup and reconnect cannot launch user actions.
- New/changed/imported executable actions require explicit review. Persist execution intent before dispatch; uncertain outcomes after crashes are not retried.
- Key activation is on release of a matching press. Capture epoch/generation/revision/identity; invalidate on page changes, Apply, pause, disconnect and input loss. Never use cached library startup false states as proof of released hardware.
- Only the device adapter applies JPEG/orientation transforms, through the pinned library's helper. Shared renderings are upright.
- Every child page has generated Back at key 0. Plugins or user buttons cannot replace it. Trees are validated iteratively to 64 levels.
- Keep task process cleanup separate from independently launched application units. Do not apply Controller `PartOf`/`BindsTo` relationships to application units.
- No root runtime. No world-writable USB rules. Do not silently change another controller's configuration or force USB takeover.
- A process boundary is not a security sandbox. Same-UID clients and approved local programs are trusted within the documented boundary.

## Changes and verification
- Update schemas, implementation, tests and API documentation together.
- Follow `docs/REQUIREMENTS.md` for deliberate version-0.1 scope changes to the original specification.
- `scripts/test.sh` runs tests and source safety/syntax checks. Ruff configuration is provided, but a full Ruff/type-check run was not available in the original execution environment.
- Keep hardware tests separate from simulator/contract tests. Never report Ubuntu install, real HID behavior or systemd user-manager behavior as passed without actually performing those checks.
- Do not ship fonts; declare system font dependencies.
