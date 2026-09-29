# Verification — polling/display plugin release 0.2.0

## Executed environment and scope

Source was built and tested in a Debian 13 container with Python 3.13.5. Python 3.12 syntax was checked separately. No physical Stream Deck was connected. PySide6 was unavailable. These results do **not** certify the Ubuntu desktop, actual USB output, real systemd user/cgroup enforcement or production CPU overhead on the user's machine.

## Automated results

**238 tests passed**, plus 17 unittest subtests. The Qt test module was skipped (12 contained GUI tests were not executed).

| Suite / batch | Result |
|---|---:|
| Controller assets, bundles, schema contracts, device adapter, input and persistence | 40 passed |
| Controller execution and crash recovery | 9 passed |
| Controller model and runtime | 29 passed |
| Controller new plugin host and fault-injection tests | 31 passed |
| Configurator non-integration, non-Qt | 71 passed; Qt module skipped |
| Configurator existing real-Controller integration | 8 passed |
| Configurator new real-CPU plugin integration | 1 passed |
| CPU package/reference protocol | 49 passed + 17 subtests |
| **Total executed tests** | **238 passed** |

Batches were separate processes because the execution environment limits the duration of each tool command. Full test commands are available in `scripts/test-all.sh`; no failed test was relabeled as skipped. Individual source tests include intentionally enabled real subprocess execution inside isolated temporary simulator profiles, never the installed Controller or physical hardware.

The 31 new Controller tests cover a real approved CPU worker, multiple independent buttons sharing one worker, 32-instance scheduling, minimum intervals, fair/coalesced image updates, unchanged-value deduplication, instant demand revocation on page changes/pause/blank/brightness-zero/disconnection, explicit simulator opt-in, approvals, package tampering/symlinks, installation immutability, gate/cgroup validation fixtures, stale contexts, suspend-baseline invalidation and button releases surviving metric updates.

Fault fixtures exercise a hanging worker, malformed/non-finite data, excessive stderr, invalid/oversized PNG data and protocol violations. A hanging development worker is reaped after its last visible button disappears while Controller status/navigation remain available. The production whole-unit launcher is covered by argument and cgroup-file validation fixtures, **not a real user manager**.

The new Configurator integration starts the actual Controller and CPU worker as separate processes. Draft edits/previews/catalog reads do not start the worker. Explicit approval and Apply to the visible simulator page do; moving to a page without the binding stops it. Missing/offline metadata handling and per-instance settings have pure-model tests. GUI widget construction/interaction remains unexecuted locally.

## Additional checks

Both source checkers validate Python 3.12 grammar and their declared boundaries. Shared-core copies and recorded SHA-256 provenance match. English and French message keys/format placeholders match. Shell scripts pass `bash -n`. The CPU package verifies its complete file inventory and fingerprints as `org.fnord.cpu` **0.2.0**:

```text
9534ae314a84dfb837763653c723486c3752b5241a6185ffc6428696e16c8790
```

## Required local acceptance

Follow [PLUGIN-HOST-0.2.0.md](PLUGIN-HOST-0.2.0.md). In particular: install/update both applications; review the CPU package approval; observe rendered values on the real XL; inspect enforced resource diagnostics and cgroup limits; verify no worker on a hidden page; hold/release a button during CPU updates; test unplug/reconnect, suspend/resume and service stop; measure warmed-up idle/visible CPU and memory under the actual hardware and desktop.

Opening the editor is not hardware visibility. Zero-brightness and explicit blank/pause stop demand. Desktop screen-lock detection and pre-suspend inhibition are not implemented; after actual resume the host detects changed suspend time and invalidates baselines before accepting further work.

No hardware frame-rate guarantee or measured sub-percent production CPU claim is made. Resource ceilings are enforced only in verified production mode. Explicit development mode is visibly **unenforced** and must not be treated as a security or resource sandbox.
