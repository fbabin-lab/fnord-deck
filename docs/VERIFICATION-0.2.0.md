# Verification — polling/display plugin release 0.2.0

## Ubuntu 24.04 automated verification

**250 tests passed on Ubuntu 24.04.5, Python 3.12.3 and Qt 6.11.2**, including all 12 Qt GUI tests using the offscreen platform. No tests were skipped in that run.

- Verified application commit: `c9e3f3afb80edb1ae7a053510a0b1e58db9d4ec1`.
- Verified application tree: `5bfeb52a6d0579e2835aa7bc3cdc695ec3f72f95`.
- [Final verification workflow](https://github.com/fbabin-lab/fnord-deck/actions/runs/36505242263).
- [Ubuntu test job and logs](https://github.com/fbabin-lab/fnord-deck/actions/runs/36505242263/job/109205088717).
- Tests completed September 29, 2026 at 00:54 UTC (September 28 in Montreal).

The workflow event commit is a temporary verification-workflow commit, **not** the application commit. The job fetched the exact application SHA above, checked its Git tree before testing, and confirmed no tracked files changed during verification. This report was added afterward as a documentation-only change. Application source, tests, package manifests and installers remain byte-identical to the verified application commit.

| Suite | Ubuntu result |
|---|---:|
| Controller, including plugin host, real development-worker and fault-injection tests | 109 passed |
| Configurator, including real Controller/CPU integration and all 12 Qt GUI tests | 92 passed |
| CPU package and reference protocol | 49 passed |
| **Total** | **250 passed** |

The job installed the locked dependencies, built and installed both 0.2.0 Python packages, started a Qt offscreen application, ran `scripts/test-all.sh`, and executed the installed `sdl-configurator --check`. The diagnostic reports Qt/font availability, polling-display integration and API 1.1, while explicitly reporting no USB ownership or plugin execution in the editor. The diagnostic itself does not connect to or certify the user's Controller.

Both source checkers passed: Python 3.12 grammar, schema/launch boundaries, byte-identical shared-core provenance, 244 matching EN/FR messages, and no bundled fonts. Shell syntax checks and CPU package inventory verification also passed.

An earlier [Ubuntu run](https://github.com/fbabin-lab/fnord-deck/actions/runs/36504787333) also passed 250 tests on commit `2135e569f1aa0a3a89d0f973f69a4da30ddeb34b`. It exposed an obsolete diagnostics label saying plugins were deferred; that label and its assertions were corrected before the final run. Neither one-time verification workflow is included in the application branch.

## Earlier local execution

Debian 13 / Python 3.13.5 local execution passed **238 tests plus 17 unittest subtests**. Qt was unavailable in that environment, so the module containing 12 GUI tests was skipped there. The subsequent Ubuntu run above executed those tests; the earlier local skip is not the current GUI-verification status. Local batches were separate processes because tool execution durations were limited.

## Coverage and boundaries

The 31 new Controller tests exercise a real approved CPU worker in explicit development mode, independent buttons sharing one process, 32-instance scheduling, minimum intervals, fair/coalesced image updates, unchanged-value deduplication, demand revocation on page changes/pause/blank/brightness-zero/disconnection, simulator opt-in, approval and package-tamper checks, bounded installation, stale contexts, suspend-baseline invalidation and button releases surviving metric updates.

Fault fixtures exercise hanging workers, malformed/non-finite data, excessive stderr, invalid/oversized PNG data and protocol violations. A hanging development worker is reaped after its last visible instance disappears while Controller status/navigation remain responsive. The production whole-unit launcher is covered by argument and cgroup-file validation fixtures, **not by a real systemd user-manager test**.

Configurator integration starts the actual Controller and CPU worker as separate processes in an isolated simulator profile. Draft editing, previews and catalog reads do not start a worker. Explicit approval and Apply to a visible simulator button do; navigating to a page without that binding stops it. GUI tests cover widget construction, local navigation/editing, independent plugin settings and preserving missing bindings. Offscreen Qt tests are not physical-desktop or USB tests.

The complete CPU package verifies as `org.fnord.cpu` **0.2.0**, fingerprint:

```text
9534ae314a84dfb837763653c723486c3752b5241a6185ffc6428696e16c8790
```

## Still required on the user's Ubuntu workstation

Follow [PLUGIN-HOST-0.2.0.md](PLUGIN-HOST-0.2.0.md). Remaining acceptance includes the real per-user installers and graphical login session, physical XL images/input, actual enforced worker/aggregate cgroup limits, hidden-page shutdown timing, service crash/descendant cleanup, unplug/reconnect and suspend/resume behavior, Wayland/X11/high-DPI interaction, and warmed-up Controller/plugin CPU and memory measurements.

Opening the editor is not hardware visibility. Zero brightness and explicit blank/pause stop plugin demand. Screen-lock detection and pre-suspend inhibition are not implemented; after resume the host detects changed suspend time and invalidates baselines before accepting new work.

No hardware frame-rate guarantee or measured sub-percent production CPU claim is made. Production launch fails closed when required resource enforcement cannot be verified. Explicit development mode is visibly **unenforced**. Approved native plugins are trusted same-user code, not a security sandbox.
