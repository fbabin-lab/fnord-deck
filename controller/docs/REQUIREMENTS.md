> Historical baseline record. For the implemented 0.2.0 plugin release, see the repository root `docs/PLUGIN-HOST-0.2.0.md` and `docs/VERIFICATION-0.2.0.md`. Earlier plugin exclusions and test counts below describe the earlier milestone.

# Version 0.1.0 scope and traceability

Authority: the user's follow-up request to implement **only the Controller**, on Ubuntu 24, with plugins deferred. The original v1.0 two-application specification is retained in `original-spec-v1.0.md` for future implementation. This release is not a claim that the original complete two-application definition of done has been satisfied.

## Delivered

| Area | Implementation | Automated evidence |
|---|---|---|
| Typed core configuration, strict JSON, UUIDs, bounds, tree validation | `sdl_core/model.py`, schemas, `jsonutil.py` | `test_model.py` |
| Icon/text rendering, format conversion, managed assets, safe SVG | `render.py`, `assets.py`, `convert_worker.py` | `test_assets.py` |
| Release-based buttons, stale press protection, generated Back, 64 levels | `input.py`, Controller navigation | `test_input.py`, model/runtime tests |
| Single-owner Controller + USB adapter + simulator | `controller.py`, `device.py`, `paths.py` | Runtime/adapter contract tests; physical checks pending |
| Atomic revisions, idempotency, corruption recovery | `repository.py`, Apply | `test_persistence.py`, runtime tests |
| Program/script execution, literal argv, asynchronous tasks, output budgets | `execution.py`, `task_worker.py` | `test_execution.py` |
| Foreground crash cleanup and no retry on restart | Lifetime pipe + durable intents | `test_crash_recovery.py` uses a real SIGKILL |
| Independent application units | `application_worker.py`, systemd launcher | Command-contract test only; real user-manager acceptance pending |
| Same-UID local API, hello, schema validation, bounded events/uploads | `ipc.py`, `api_contract.py`, Controller handlers | Runtime tests |
| Data-only backup/import, archive defenses, fresh imported identities | `bundle.py`, CLI | `test_bundle.py` |
| CLI, installer, user service, autostart hook, narrow udev rule | `sdl_cli`, scripts/packaging | CLI smoke/source/shell syntax; clean Ubuntu acceptance pending |
| Future extension preservation | `ExtensionHost`, `DeferredExtensions`, schema fields/action registry | Deferred action/host behavior tests |

## Explicitly deferred or limited

1. **Graphical Configurator and all GUI work**: outside this user-requested milestone. The CLI is a real separate configuration client, not a placeholder GUI.
2. **Plugin host, manifests, trust workflow, dynamic metrics, plugin wire protocol and secrets**: deferred by the user. No production or test metric plugins are shipped. Reserved fields are round-tripped; capability flags say unavailable.
3. **Hardware and Ubuntu-desktop validation**: no physical deck, libusb backend or systemd user session was available in the build environment. Fake-library tests do not certify real firmware, permissions, native JPEG transfer or application survival. Follow `HARDWARE-ACCEPTANCE.md` locally.
4. **Python/platform verification**: code targets Python 3.12/Ubuntu 24.04; automated execution here used Python 3.13.5 on Debian 13. A separate AST check verifies 3.12 syntax, not the entire 3.12 runtime/dependency combination.
5. **Font families**: the implementation deliberately supports the declared DejaVu Sans normal/bold font with Liberation fallback, not arbitrary installed-font discovery. Other configured families warn and fall back.
6. **Rendering cache**: bounded memory/native caches are implemented. No persistent disk render cache or automatic asset garbage collection is shipped. Originals/derivatives are retained safely.
7. **Execution output retention**: stdout/stderr buffers are bounded and available live, but intentionally not persisted or included in routine exports/events. Durable history preserves intent/outcome, not output.
8. **Bundle import identities**: imported documents get new configuration/page/button/plugin-instance identities and cleared device/secret bindings. This intentional hardening ensures archives cannot transfer existing action approval context.
9. **Device selection**: only VID/PID `0fd9:006c` is supported. One unpinned matching device is remembered locally and can be explicitly pinned into the document by `select-device`; multiple devices need selection. Other models/revisions require an explicit adapter/schema extension.
10. **Performance certification**: no claimed physical redraw/reconnect/frame-rate/CPU targets. Functional simulator tests passed; production timing and resource baselines remain measurable acceptance items.
11. **Full lint/type checking**: typed interfaces, Ruff configuration and dependency-free syntax/safety checks are provided. Ruff/mypy were unavailable here; no full type-check pass is claimed. Exact runtime dependencies are version-pinned, not wheel-hash-locked.
12. **USB call stalls**: HID work is off the event loop and serialized in a worker thread. A native backend call that never returns cannot be forcibly cancelled as a Python thread; installed-service stop timeout is the final process-level safeguard. This should be evaluated under real unplug/suspend/failure testing.

## Next implementation work

First execute the local hardware/Ubuntu acceptance checklist and address any observed backend/session issues. Then build the separate Configurator against the shipped contracts, sharing the renderer. Add the plugin subprocess host only after that core experience is stable, using the original specification without implementing CPU/storage/web-service plugins as part of the interface milestone.
