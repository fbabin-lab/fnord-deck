> Version 0.2.0 adds approved polling/display plugins and API 1.1. The [current plugin guide](../../docs/PLUGIN-HOST-0.2.0.md) supersedes earlier plugin-deferred statements below. Run both this document's baseline acceptance checks and the plugin guide's additional checks.

# Security boundaries

The Controller runs as the desktop user. It deliberately launches programs chosen and approved by that user. These programs have the user's rights: this is not a sandbox for untrusted scripts. Socket ownership and peer-UID checks separate users, not mutually hostile programs running under one UID. Administrator/root access is outside this boundary.

There is no implicit shell, command interpolation, remote listener, network API client, plugin discovery/loading, root daemon or automatic privilege escalation. The install-time sudo requests are limited to dependencies and the exact USB access rule. Pause is operational, not authentication; screen-lock integration is not implemented.

Untrusted image files are size/pixel bounded and decoded in resource-limited short-lived processes. SVG is explicitly whitelisted and cannot request external resources. These controls reduce exposure but are not proof against a native decoder vulnerability. Keep Ubuntu/Pillow/Cairo dependencies patched; dependency updates require retesting. The exact-version lock is not a publisher signature or cryptographic wheel-hash lock.

Applied revisions and assets are Controller-owned. Apply verifies data before replacing the durable pointer. Imports never automatically approve actions. All command strings remain explicit argument arrays. External program contents can change at a previously approved path; approval is not a content-integrity guarantee.

Execution intent precedes dispatch. Uncertain outcomes are reported rather than retried. Task stdout/stderr is drained with bounded retained buffers, stays out of ordinary logs/durable history, and is returned only when explicitly requested. An explicitly configured argument/environment value can contain sensitive material; the application cannot infer and remove secrets from arbitrary user configuration. Do not put secrets in portable configuration or command-line arguments. No secret-store feature exists in this release.

Task supervisors clean their process groups when the Controller's lifetime pipe closes; the installed user unit additionally uses `KillMode=control-group`. Intentionally detached tasks are outside the managed-task contract. Application-mode services have separate lifetimes and receive explicit allowlisted desktop context plus deliberate non-secret overrides.

Private launch requests live in the user's runtime directory and are removed when consumed. An application launch with an unknown acknowledgement can leave a request pending; it is not automatically replayed. Runtime directories are normally removed at logout. Image assets are not automatically garbage-collected in this initial release, to avoid deleting data referenced by retained drafts/revisions.

Logs rotate at 2 MiB with two backups. Do not send diagnostic outputs or portable configuration to third parties without reviewing local paths/names. Hardware/backend errors are translated without including tracebacks or complete environments.
