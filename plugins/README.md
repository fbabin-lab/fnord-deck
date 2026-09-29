# Plugins — polling/display integration

Controller and Configurator 0.2.0 implement a visibility-aware display host. See [setup and implementation scope](../docs/PLUGIN-HOST-0.2.0.md) and [the extension specification](../docs/plugin-system-v1.1.md).

- `cpu/`: CPU usage 0.2.0, standard-library worker and fingerprinted API 1.1 manifest.
- `tools/cpu_demo.py`: finite terminal-only protocol demonstration, independent of hardware.
- `tools/verify_package.py`: data-only package hash checker.
- `tests/`: deterministic sampling, visibility and subprocess protocol tests.
- `examples/cpu-two-buttons.json`: full layout showing independent aggregate/logical CPU bindings. Applying it replaces the current layout; use the Configurator to add CPU buttons without replacing your layout.

From the repository root:

```bash
python3 -B plugins/tools/verify_package.py plugins/cpu
python3 -B plugins/tools/cpu_demo.py --samples 3 --interval 2 --cpu total --cpu 0
python3 -B -m unittest discover -s plugins/tests -v
```

The Controller installer installs CPU package files separately from approval. Changing a shipped package requires a new version and approval of its exact fingerprint. No autonomous polling is used by the CPU worker. Multiple visible instances share reads but retain independent settings and baselines. The default interval is two seconds, minimum one second. All work is GPL-3.0-only; no fonts are bundled.
