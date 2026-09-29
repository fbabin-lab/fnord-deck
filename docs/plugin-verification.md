> Historical baseline record. For the implemented 0.2.0 plugin release, see the repository root `docs/PLUGIN-HOST-0.2.0.md` and `docs/VERIFICATION-0.2.0.md`. Earlier plugin exclusions and test counts below describe the earlier milestone.

# Plugin specification/reference verification

Date: 2026-09-28. Baseline GitHub main:
`72717708b6df5f53de514f470e85da0136f61078`.
The reconstructed original source tree hash matched the GitHub tree exactly:
`2d430a79cd4cefe2fad697b335a3b54aa4bec676`.

## Delivered and tested

- Plugin-system specification API 1.1 and CPU reference package API 1.1/version 0.1.0.
- **49 plugin tests passed**: deterministic lifecycle/sampling/error/framing tests,
  package inventory/tamper checks, actual subprocess JSONL interaction, parent pipe
  closure, bounded invalid input handling, and idle-process checks.
- **78 existing Controller tests passed**, plus its Python 3.12 syntax, schemas and
  prohibited-launch-API checks.
- **74 existing Configurator tests passed**, including its existing external
  Controller simulator integration. **One Qt GUI test module skipped** because
  PySide6 is unavailable. Existing editor/shared-core safety checks also passed.
- Every new Python module parses with Python 3.12 grammar. JSON files and all
  specification JSON examples parse successfully. The two-button example validates
  against the current Controller schema (expected PLUGIN_DEFERRED warnings).
- Package file hashes and fingerprint checked with the data-only verifier.
- The packaged ZIP was extracted and its 49 plugin tests rerun successfully.
- The repository patch was applied to a clean baseline; the resulting Git tree
  matched the prepared source tree exactly.
- Finite real `/proc/stat` terminal demo completed: independent aggregate/CPU-0
  states, initial unavailable baseline, subsequent numeric values, then shutdown.

Commands, from the repository root:

```bash
python3 -B -m unittest discover -s plugins/tests -v
python3 -B plugins/tools/verify_package.py plugins/cpu
python3 -B plugins/tools/cpu_demo.py --samples 2 --interval 1 --cpu total --cpu 0
(cd controller && PYTHONDONTWRITEBYTECODE=1 ./scripts/test.sh)
(cd configurator && PYTHONDONTWRITEBYTECODE=1 SDL_CONTROLLER_SOURCE="$PWD/../controller" ./scripts/test.sh -q)
```

## Environment and limits

Execution used **Debian 13, Python 3.13.5**, not Ubuntu 24.04/Python 3.12.
The 3.12 check is syntax compatibility, not a complete 3.12 runtime test.
A first Configurator regression invocation reached its 45-second tool limit near
teardown; a complete rerun passed (74 passed, one skipped module, 29.82 seconds).
The Controller suite passed in 30.56 seconds; plugin suite in 5.309 seconds.
Durations describe these runs only and are not application performance targets.

No new code was installed into the user's computer, and no physical deck was
accessible. Production plugin host, visibility-to-USB integration, systemd user
resource enforcement, Configurator plugin forms, invocation lifecycle and 60-second
Ubuntu performance targets remain unimplemented/unverified. The existing 0.1.0
applications and shared-core code are unchanged and still report plugins unsupported.

The zero-hidden-work tests establish that the reference plugin rejects hidden
refreshes without invoking its source reader, and that it has no autonomous timer.
They do not prove that a future host computes visibility correctly. The idle child
process check permits one CPU accounting tick of noise over 250 ms; it is not a
claim of a guaranteed CPU percentage or of cgroup enforcement.

Use acceptance matrix AT-01 through AT-26 in `plugin-system-v1.1.md` for the actual
host implementation. Never report all those host tests as passed from this suite.
