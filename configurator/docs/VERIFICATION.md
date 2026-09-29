> Historical baseline record. For the implemented 0.2.0 plugin release, see the repository root `docs/PLUGIN-HOST-0.2.0.md` and `docs/VERIFICATION-0.2.0.md`. Earlier plugin exclusions and test counts below describe the earlier milestone.

# Configurator 0.1.0 verification report

**Verification date:** September 28, 2026.

## Results actually obtained

**74 tests passed; one Qt test module was skipped during collection.** The skipped module contains ten desktop tests; none of those ten is counted as passed. The complete run took 38.46 seconds in the build environment. See `test-results.xml` for the machine-readable result, and `test-results.txt` for the captured output.

| Group | Passed | Scope |
|---|---:|---|
| Backend use cases | 11 | Frozen review content, confirmation gates, expected revisions, pending-operation persistence, no automatic execution retries, extension preservation, real preview dimensions |
| CLI / localization / installer syntax | 4 | Read-only diagnostic CLI, absolute workspace validation, English/French catalog parity, shell syntax |
| Draft model | 24 | Section creation/copy/move/deletion, generated Back protection, cycle/depth checks, undo/redo/coalescing, opaque field preservation, identity renewal, independent saved/applied state |
| Real Controller integration | 8 | Unchanged Controller 0.1.0 subprocess using real Unix IPC, assets, revision conflicts, review/Apply, recovery deduplication, nested navigation, explicit harmless execution |
| Local storage / images | 19 | Private directory/file modes, lock ownership, recovery and named drafts, real image conversions, restricted SVG, bundle integrity/interoperability, missing assets, no token persistence |
| Transport | 8 | Handshake/envelopes, frame limits, request IDs, strict JSON, structured errors, unavailable socket, interrupted request without retry |
| **Total** | **74** | **No Qt desktop or physical-device pass is included in this count** |

The actual Controller simulator was launched as a separate process for each integration test with isolated XDG data/runtime directories, not mocked. The Configurator's uploaded-image preview was compared pixel-for-pixel with the Controller API's rendered preview; both produced identical 768 × 384 output. A second editor workspace downloaded and verified the original image via the API.

The `real_execution` test explicitly enabled simulator execution and used a Python task that wrote a marker inside its test directory. It verified that editing, local persistence, rendering, applying, Controller navigation, and reloading **did not** create the marker. Rejecting the explicit test confirmation also did not create it. A confirmed test created exactly one execution record and the expected marker. The GUI grid gesture boundary has a separate Qt test which remains unrun here.

## Additional completed checks

- Source parses with Python 3.12 grammar; the actual interpreter for tests was Python 3.13.5. This is **not** execution under Python 3.12.
- All copied shared-core file hashes match the Controller 0.1.0 baseline.
- 221 English/French message keys and formatting placeholders match.
- Production editor source does not import USB/Controller/action-launcher modules or use `eval`, `exec`, `os.system`, or `popen`.
- Installer, uninstaller and test scripts pass `bash -n`.
- Package wheel built successfully with setuptools 82.0.1.
- The wheel was installed into a separate test virtual environment. With the build environment's preinstalled **non-Qt** dependencies made available, its installed entry point, version/check commands, packaged schema/catalog/style resources, offline model, and 32-key renderer passed a smoke test. No source-tree PYTHONPATH was used for that check. This was not a clean online installation of the full Qt dependency set.
- No font files or third-party dependency wheels are included in the source archive.

## Environment

| Item | Value |
|---|---|
| Build/test OS | Debian GNU/Linux 13.3, x86-64 |
| Python | 3.13.5 |
| Pillow | 12.3.0 |
| CairoSVG | 2.8.2 |
| defusedxml | 0.7.1 |
| jsonschema | 4.26.0 |
| pytest / pytest-asyncio | 9.0.2 / 1.3.0 |
| Controller integration target | Supplied Controller 0.1.0, API 1.0, simulator adapter |
| GUI dependency specified | PySide6-Essentials 6.11.2 / shiboken6 6.11.2 |
| Qt available in build environment | **No** |
| Physical Stream Deck connected | **No** |

PySide6 could not be installed because dependency download/network access was unavailable in this environment. Therefore no Qt window was launched, no GUI screenshot is presented as verification, and no desktop interaction is claimed as tested here.

## Not yet verified

The Ubuntu 24.04 apt/pip installer against actual repositories, Python 3.12 execution, Qt Widgets/platform-plugin loading, real Wayland/X11/HiDPI behavior, GUI interaction tests, GUI close/recovery interactions, real device mapping and images, and real graphical-application launching remain local checks. The non-Qt backend/model paths for recovery and editing have automated coverage; the corresponding desktop gestures do not yet have a passing result.

Use `DESKTOP-ACCEPTANCE.md` and run the included Qt tests after installing on Ubuntu. Model/API success does not establish desktop runtime success. No claim of full production or hardware validation is made.

## Commands used

```bash
PYTHONPATH=src python -m pytest -vv -o faulthandler_timeout=10 --junitxml=docs/test-results.xml
python scripts/check-source.py
bash -n scripts/install-ubuntu.sh scripts/uninstall.sh scripts/test.sh
python -m pip wheel --no-deps --no-build-isolation . -w /mnt/data/configurator-built-wheel
```

The complete suite can be run using `./scripts/test.sh -q` in the installed dependency-equipped environment. Set `SDL_CONTROLLER_SOURCE` when the Controller source is not a sibling directory. An environment without it skips the eight integration tests explicitly.
