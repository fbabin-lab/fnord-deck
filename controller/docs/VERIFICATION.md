# Verification report — version 0.1.0

Date: September 28, 2026.

## Executed automated checks

```text
python -m coverage run --source=src -m pytest -q --junitxml=docs/test-results.xml
78 passed in 27.08s

python scripts/check-source.py
Python 3.12 syntax, JSON schemas and prohibited-launch-API checks: PASS

bash -n scripts/install-ubuntu.sh scripts/uninstall.sh scripts/test.sh
PASS
```

Evidence: `test-results.txt`, `test-results.xml`, and `coverage.txt` in this directory. The test report has no physical-hardware test results hidden among simulator passes.

Tests cover configuration/tree/bounds validation, shipped examples, exported API contracts, raster/SVG conversion, image integrity, alpha/text rendering, nested Back/Home navigation, held-key invalidation, reconnect behavior, Apply revision conflicts and idempotency, atomic-write failure recovery, same-UID IPC, bounded frame/uploads, executor approval and literal argv, output truncation, cancellation, timeout/concurrency, backup import defenses, and the future-plugin deferred state.

One test starts a **real Controller subprocess with isolated simulator state**, starts a synthetic long-running task, sends SIGKILL to that Controller, verifies supervised task cleanup, restarts the Controller and verifies that the same operation is **not** run again. Separate task-supervisor tests exercise Controller-lifetime pipe closure with actual child processes. They do not rely solely on mocked execution.

The Elgato adapter tests use a fake of the pinned library interface. They verify transport selection, capability checking, serial selection, initial-report handling, and a single native-image conversion without pre-rotation. They do **not** communicate with USB or execute the installed HID library.

## Built and installed package smoke test

A project-only wheel was built with setuptools 82.0.1:

```text
streamdeck_linux_controller-0.1.0-py3-none-any.whl
```

It was installed with `pip --no-deps --no-index` into a temporary virtual environment. For this offline check, that environment reused the build environment's already installed third-party libraries through a local `.pth` entry; no project source directory was placed on `PYTHONPATH`. This is an installed-wheel packaging/CLI check, **not** a clean dependency-resolution or Ubuntu-installer test.

The installed entry points were run from a temporary working directory with isolated HOME and XDG directories. The smoke workflow passed: init, validate, Apply, status, three-level navigation, Back/Home, actual image import and icon-plus-text rendering, PNG preview, pause/resume, brightness, export/import with regenerated identities, execution-history inspection, simulator disconnect/reconnect, and orderly process shutdown. No configured program executed. See `installed-wheel-smoke.txt`, the two installed help outputs, and `simulator-preview.png`. The preview was also visually inspected for text/icon layout; it is not evidence of physical display orientation.

The smoke harness initially needed its third-party dependency search path corrected because the host itself runs inside a virtual environment. That harness correction is not part of the shipped Ubuntu installer: the installer installs dependencies into its own environment normally.

## Environment and limitations

Actual test interpreter: **Python 3.13.5**. Build container: **Debian 13**, Linux x86-64. The target remains **Ubuntu 24.04 / Python 3.12**. Python 3.12 syntax was checked using the AST parser's target-version option; a complete Python 3.12 runtime/dependency installation was not available. `build-environment-diagnostics.json` records the actual package/backend/codec findings; missing components are represented as null, not reported as successful.

Pillow 12.3.0, CairoSVG 2.8.2, jsonschema 4.26.0 and defusedxml 0.7.1 were used by executed tests. No physical Stream Deck, installed `streamdeck` package, `libhidapi-libusb` backend, active systemd user manager, or Ubuntu graphical session was available. Network restrictions prevented downloading the HID package into this environment. Adapter integration was checked against upstream documentation/source and fake-library tests only.

The parent-process coverage measurement is **68% of statements**. It does not instrument spawned converter/task/Controller subprocesses, even when tests execute them, and it does not include the separate installed-wheel smoke run. It is not a whole-system coverage score. No Ruff/mypy pass, timing benchmark, prolonged soak test, vulnerability audit, hardware certification, or real systemd application-survival test is claimed.

## Required local acceptance before relying on it

Run `HARDWARE-ACCEPTANCE.md` on Ubuntu with the actual 20GAT9901. It covers clean installation, active-seat udev access, all 32 key positions, orientation/text, Back/Home, held-key safety, unplug/replug, paused input, explicitly reviewed task execution, independent GUI application units, automatic login startup, and recovery diagnostics.

In particular, independently launched application lifetime and native USB image/input behavior remain **implemented but locally unverified**. Recheck the private full-report hook before changing the pinned streamdeck library version.
