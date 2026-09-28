# AI implementation guidelines

This repository is the Configurator, not the USB Controller.

Read README.md, docs/ARCHITECTURE.md, docs/CONTROLLER-API.md and docs/SECURITY.md before edits. Controller 0.1.0 API 1.0 and `src/sdl_core` are the integration baseline. `docs/shared-core-origin.json` records exact copied core checksums. Do not modify the core snapshot casually; change and test both applications together when shared semantics must change.

Keep local Save separate from Controller Apply and explicit Run. No execution on imports, previews, grid clicks, page browsing, save, Apply, start/reconnect, or pending-recovery startup. Use literal argument arrays; no implicit shell. Execution requests must never be automatically retried. Only the explicit run backend method calls execution.test.

Never open USB, write Controller-owned files, restart its service, or import sdl_controller into production editor code. The unchanged image-converter helper is a bounded asset-processing subprocess, not an action launcher. Preserve future plugin/dynamic/extension data without activating it; plugin implementation is out of scope.

Keep Qt calls on the main thread and I/O on Bridge's asyncio worker. Add regression tests and update both English/French catalogs. Source grammar targets Python 3.12; no bundled fonts. Run scripts/test.sh and record passes/skips with the actual environment. Qt GUI tests and Ubuntu/physical acceptance are NOT considered passed merely because model/API tests pass.
