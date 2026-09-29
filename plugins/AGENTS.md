# Plugin work instructions

Read `../docs/plugin-system-v1.1.md` before changing this directory. This supersedes
the old original-spec exclusion of plugins. The production poll/display integration
is documented in `../docs/PLUGIN-HOST-0.2.0.md`; reserved features are not implemented.

Keep CPU metric code out of both host applications. No USB, controller-file writes,
GUI imports, shell execution, autonomous polling, or network in the CPU reference.
Create/show/hide/ping/destroy must not sample. Refresh only while the exact instance
epoch/activation is visible. Preserve per-instance settings and baselines; shared
source caches must not include hidden time or bypass the minimum interval.

Run `python3 -B -m unittest discover -s plugins/tests -v` and the Controller/Configurator regression suites. Do not generate bytecode
inside `cpu/`. After package edits regenerate its `manifest.json` file hashes and
verify with `python3 -B plugins/tools/verify_package.py plugins/cpu`. Hash changes
invalidate approval; they are not publisher signatures. Do not bundle fonts.

State precisely whether testing covered reference protocol, production host,
systemd limits, GUI, Ubuntu or physical hardware. A terminal demo is not a host.
