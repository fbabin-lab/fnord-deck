# Implementation rules

Read `docs/PLUGIN-HOST-0.2.0.md`, `docs/plugin-system-v1.1.md` and each application's AGENTS.md. The release guide identifies the implemented polling/display subset; do not claim push/commands/secrets are available.

The Controller alone owns hardware, applied state, approvals and native plugin workers. The Configurator only edits drafts and calls the local API. Keep sampling code in plugin packages. Native plugins are trusted same-UID code, not a security sandbox.

Never execute button commands during import, preview, Apply, startup or recovery. Approved visible display plugins may collect data after Apply: do not confuse this with executing a button action. Preserve literal arguments and never substitute plugin output into executable actions.

Visibility, refresh floors, capped output, cancellation, approval identity, no hidden retry, failure isolation, and metric repaints preserving button presses are regression requirements. Extend schemas, both identical shared-core copies, API documentation and EN/FR settings together. Recompute package hashes and use a new package version when changing shipped package content.

Run `scripts/test-all.sh` and both source checkers. Record real passes/skips. Never label Qt, Ubuntu HID, production cgroup enforcement, or hardware performance verified without executing the corresponding test. Do not bundle fonts or secrets. Do not modify users' installed/applied configuration during development tests.
