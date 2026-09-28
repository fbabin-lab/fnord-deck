# Security and data handling

## Trust boundary

The Configurator and Controller run as the normal desktop user. A Unix socket authenticates its peer using Linux `SO_PEERCRED`; both sides must have the same UID. There is no TCP listener, remote API, telemetry, automatic update downloader, web browser component, or credential service in the Configurator.

This is not a defense against other malicious programs already running as that user. They can generally access the same files, process environment and Controller socket. Review dialogs prevent accidental execution/activation through the editor; they are not an OS security sandbox.

## Intentional program execution

Configured programs run with the Controller user's permissions. The Configurator does not execute them itself. No shell is inserted implicitly; literal argument rows remain arrays. Choosing an interpreter such as `/bin/bash` or supplying `-c` is an explicit request to use that interpreter; its own expansion semantics then apply. Untrusted scripts remain untrusted even when stored behind a button.

The exact applied configuration must be reviewed before activation of new executable actions. Tests need another explicit confirmation. Import, preview, navigation, saving, reconnect and Apply do not trigger execution. Ambiguous test responses are not retried.

## Images and bundles

Image import uses the unchanged bounded Controller/core pipeline: bounded original size, restricted SVG with external resources rejected, conversion worker with timeout/resource bounds, canonical pixel output, managed original and content hash. Conversion is not an arbitrary-code plugin facility. These controls reduce exposure, but decoder vulnerabilities remain possible; keep dependencies reviewed and updated. No image path is executed as a script.

Portable bundle parsing uses the shared archive/checksum/path validation, size bounds and identifier renewal. External bundles carry no execution approvals or Controller revisions. The imported draft remains inactive. Only user-selected regular files are accepted as image/configuration inputs.

The application installs no fonts of its own. Rendering uses system fonts and the same renderer as Controller 0.1.0. Installer Python dependencies are pinned but not a fully offline or cryptographically locked supply-chain bundle; no dependency wheels are included. Downloads come from the configured pip index and Ubuntu repositories. Review the installer and dependency policy before deploying in a restricted environment.

## Private files and recovery

Editor directories use mode 0700 and generated private documents use 0600. Atomic writes use replacement, not in-place partial mutation. Existing application directories with unsafe ownership or permissions are rejected, rather than silently modified. A per-workspace lock prevents concurrent editors racing recovery files. An invalid recovery draft is renamed and preserved before a new draft is created.

Environment values, executable arguments, paths and image content are plaintext on disk. Private Unix permissions are not encryption. They may be included in exports. The review UI lists environment variable names only, but a user can inspect values in the action editor. Do not treat these fields as a secret manager.

Pending Apply recovery stores the reviewed configuration and operation ID, not transient review tokens/approval flags. A stale, malformed or foreign-socket pending record cannot silently be used for another Controller. An explicit discard removes only the local recovery record and never rolls back the Controller.

Diagnostics and recent-history data may reveal personal paths and program names. Review exported diagnostics before sharing. The GUI requests execution history without output capture by default.

## Remaining limitations

No malware scanning, encryption at rest, OS sandboxing, signed plugin marketplace, secret vault, asset garbage collection or automatic security updates are included. File permissions and decoder containment are inherited/tested boundaries, not proof against every attack. The Ubuntu installer and Qt desktop integration require local verification. Report actual tests and their environment separately from intended behavior.
