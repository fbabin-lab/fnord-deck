# Plugin host 0.2.0 — implementation and Ubuntu handoff

This release implements the visibility-aware **polling display** portion of
[plugin API 1.1](plugin-system-v1.1.md), including a CPU plugin and Configurator
controls. It supersedes the earlier “plugins deferred” milestone. The original
specification remains the design contract; this document records the implemented
subset, limits, installation, and remaining acceptance work.

## Install / upgrade on Ubuntu 24.04

Close the Configurator before upgrading. From the repository root, as your normal
desktop user (not sudo):

```bash
./controller/scripts/install-ubuntu.sh
./configurator/scripts/install-ubuntu.sh
export PATH="$HOME/.local/bin:$PATH"
sdl-session --start
sdlctl plugins
sdl-configurator
```

The Controller installer installs the `app-sdlplugins.slice` user unit and the
bundled `org.fnord.cpu` **0.2.0** package. It does not grant approval, create button
bindings, change your applied layout, or execute plugin code during installation.
Existing configurations, imported icons, drafts, and executable-action approvals
are retained. The two applications remain separately installed and independently
running. Existing deployments should install **both** 0.2.0 packages.

In the Configurator:

1. Open **Plugins**, select **CPU Usage 0.2.0**, and choose **Review / approve…**.
   Review its fingerprint, interpreter identity, and declared `/proc/stat` access.
2. Select a button and open **Plugin display…**. Choose CPU Usage, set `scope` to
   `total` or `logicalCpu`, set the logical CPU index when applicable, and set a
   label. Save the dialog, then **Review & apply…**.
3. Navigate to a subsection without CPU buttons. In **Plugins → Rescan / refresh
   status**, `activeWorkers` and `instances` should return to zero. Returning to
   the CPU page creates fresh measurement baselines; it does not measure the
   interval spent hidden.

The button's normal application/script or navigation action is independent of
its display plugin. Editing a plugin binding never changes that action. Static
icon/text remains fallback content. Normal editor previews are intentionally
static and never create plugin demand. Cached catalog metadata allows offline
settings editing after the catalog has been loaded once; it conveys no approval.
A missing package binding can be preserved unchanged or explicitly removed.

The CPU plugin's `decimals` setting controls measurement rounding. The separate
**Display decimal places** field controls formatting; increase both for a decimal
CPU display. Templates accept only `{{label}}`, `{{value}}`, `{{unit}}`, and
`{{status}}` literal substitutions; no expressions, scripts, paths, or commands.

### CLI equivalents

```bash
sdlctl plugin-rescan
sdlctl plugin-approve org.fnord.cpu 0.2.0   # interactive, exact-fingerprint review
sdlctl plugin-disable org.fnord.cpu 0.2.0
sdlctl plugin-enable org.fnord.cpu 0.2.0   # only a still-matching prior approval
sdlctl plugin-restart org.fnord.cpu 0.2.0  # explicit retry; hidden instances stay off
sdlctl blank
sdlctl unblank
```

`--yes` on `plugin-approve` is an explicit approval, not a diagnostic option.
To install another **trusted** package without executing or approving it:

```bash
~/.local/share/streamdeck-linux/app/venv/bin/python \
  -m sdl_controller.plugins.install /absolute/path/to/package
sdlctl plugin-rescan
```

With a custom `XDG_DATA_HOME`, use the corresponding installed virtual environment.
Installation is data-only, bounded, serialized, and atomic. Reinstalling identical
files is a no-op. A different fingerprint at the same installed ID/version is
rejected; publish a new version instead. New versions and changed interpreters
require fresh approval. There is no automatic download/update mechanism.

## Runtime lifecycle and efficiency

An instance is eligible only when all these conditions hold: its button belongs
to the applied current device page; the device is ready and synchronized; the
button and its dynamic display are enabled; at least one override is requested;
the Controller is not paused/blanked; brightness is nonzero; and its exact package
and interpreter approval still match. The editor's selected page is irrelevant.
The simulator requires explicit `--allow-plugins`.

Navigation/configuration changes revoke old activation IDs synchronously, before
any awaited work. No in-flight old response can paint a new page. Already-running
workers needed by a destination page may be retained for at most 500 ms during
static page painting, with old instances hidden and **no collection** in that gap.
Otherwise the last disappearing instance cancels work and terminates the worker;
there is no idle retention period. Shutdown has bounded grace/kill escalation;
the Ubuntu acceptance checklist must verify actual timing under systemd.

One process per approved package/version serves separate button instances with
independent settings, epochs, activation IDs, counters, and freshness state. Copying
a button generates a new instance UUID. Package identity is not an instance ID.
Due instances are batched; each worker has at most one refresh request in flight.
Both per-instance and per-package scheduling enforce the minimum interval.
Overruns skip missed ticks; they never generate catch-up bursts. Hidden workers
have no polling, refresh, retry, accounting, or keepalive timer.

| Budget | Implemented policy |
|---|---|
| CPU default refresh | 2,000 ms |
| Minimum requested/effective refresh | Host floor 1,000 ms; larger contribution floors also apply |
| Longest configured refresh | 3,600,000 ms |
| Dynamic key writes | At most 1 per second per key; shared 8/s token bucket, burst 8 |
| Pending dynamic paints | Latest value only, at most one entry per visible key; fair queue |
| No visual change | Refresh freshness but skip image rendering and USB writes |
| Message frame | At most 256 KiB, strict finite UTF-8 JSON, duplicate fields rejected |
| Worker stdout | 64 messages/s, burst 128; 512 KiB/s byte budget |
| Worker stderr | 64 KiB retained ring, 4 KiB/s with bounded 64 KiB burst; floods stop the worker |
| Live package workers | At most 8, including stopping workers when admitting new work |
| Package inventory | At most 128 payload files, 2 MiB/file, 8 MiB/package, manifest 64 KiB; bounded discovery |
| Default stale threshold | At least 10 s and three effective refresh/fair-paint periods |
| Automatic crash retries | Bounded backoff, at most three restarts in 60 s before quarantine |

The fair-paint interval is approximately `max(1, visibleInstances / 8)` seconds
when every value keeps changing. Therefore 32 busy displays can update about once
every four seconds each, while collecting shared CPU data every two seconds.
There is no claim that all 32 changing keys can refresh simultaneously each second.
Ordinary status repaints use the last **painted** dynamic state, not the newest
queued measurement, so they cannot bypass the plugin write budget.

Suspend/resume detection uses the difference between boot time and monotonic time.
After resume it invalidates old input and plugin activations before processing
new input/refreshes and creates fresh baselines. This is post-resume detection,
not a desktop screen-lock integration. Screen locking alone is not a hidden-page
signal in this release; use pause/blank or zero brightness when needed.

## Resource enforcement and trust

Production launches use transient **systemd user services**. The Controller must
itself be running as `sdl-controller.service` (`sdl-session --start`). A trusted
stdlib launch gate waits while the host verifies the live PID, dependencies, and
actual cgroup v2 files; plugin bytes execute only after that verification succeeds.

Each worker has `CPUQuota=5%` (of one logical CPU), `MemoryHigh=48M`,
`MemoryMax=64M`, `TasksMax=8`, `LimitNOFILE=64`, `Nice=10`, `NoNewPrivileges=yes`,
`KillMode=control-group`, and a bounded stop timeout. The aggregate
`app-sdlplugins.slice` has `CPUQuota=15%`, `MemoryHigh=384M`, `MemoryMax=512M`, and
`TasksMax=64`. Missing/unlimited cgroup controllers cause a **closed failure**;
there is no automatic unrestricted fallback. Persistent near-ceiling CPU/memory
usage also results in quarantine. Accounting is checked only for live workers.

Workers bind their lifetime to the Controller's user service. A Controller stop
or crash must stop the complete worker unit, including descendants. Same-user
native code remains **trusted code**, not hostile-code containment. In particular,
permission declarations disclose intended access; they are not a filesystem or
network security sandbox, and arbitrary approved code could try to escape its
resource group. Do not install untrusted plugins.

If status reports `RESOURCE_LIMIT_UNAVAILABLE`, inspect rather than disable limits:

```bash
systemctl --user status sdl-controller.service
systemctl --user cat app-sdlplugins.slice
systemctl --user show app-sdlplugins.slice -p ControlGroup -p CPUQuotaPerSecUSec -p MemoryMax -p TasksMax
journalctl --user -u sdl-controller.service -n 80 --no-pager
sdlctl plugins
```

### Explicit simulator-only development check

The `development` launcher uses parent-death protection and process-group cleanup,
but **does not enforce CPU/memory cgroup quotas or provide descendant containment**.
It exists for tests and local diagnosis and is clearly reported as
`DEVELOPMENT_UNENFORCED`. It is never selected automatically.

```bash
~/.local/share/streamdeck-linux/app/venv/bin/python \
  -m sdl_controller.plugins.install ./plugins/cpu --simulator
sdl-controller --simulate --allow-plugins --plugin-mode development
# In another terminal:
sdlctl --simulator plugin-approve org.fnord.cpu 0.2.0
sdlctl --simulator apply plugins/examples/cpu-two-buttons.json
sdlctl --simulator plugins
```

## API, schema, and implementation boundaries

Local API major remains 1, advertised minor is 1. `system.hello` reports
`pluginApiVersion=1.1`, `pluginDisplay=true`, `pluginInvoke=false`,
`pluginPush=false`, `secrets=false`. New exact parameter/result contracts are in
`controller/schemas/local-api-{parameters,envelopes}-v1.json` and `sdl_core.api_contract`.

Methods: `plugins.list`, `plugins.rescan`, `plugins.approve`,
`plugins.setEnabled`, `plugins.restart`, and `runtime.blank`. Approvals require
plugin ID/version, exact package fingerprint, interpreter identity, and
`confirmed=true`. Catalog responses include data-only manifests. Runtime snapshots
include eligibility failures, phase/PID, enforced-mode status, per-instance
requested/effective cadence, freshness, refresh counts/duration, stale markers,
paint ages, skipped identical output, and aggregate queue/write counters.

`core.plugin.invoke`, push subscriptions, secrets, plugin settings migrations,
package marketplaces, screen-lock integration, and live editor simulation leases
remain **unimplemented** and are advertised as unavailable. The command interface
is reserved; a display plugin can already coexist with an independent application
or script action. Future disk/web-service display plugins can use this same polling
contract, independent settings, bounded output, and lifecycle without changing the
Controller's scheduler. They must implement their own bounded I/O/cancellation.

New dynamic fields are validated. Legacy opaque `appearance.dynamic` objects
without an `enabled` member remain preserved but inert. Existing `extensions`
objects remain round-tripped. The Configurator and Controller share byte-identical
`sdl_core` snapshots, verified by a checksum manifest; no GUI imports the Controller,
StreamDeck library, or plugin Python modules.

## Architecture map

- `controller/.../plugins/registry.py`: data-only validation, hashes, approvals,
  verified immutable launch snapshots, interpreter identity.
- `install.py`: explicit atomic installation, no approval/execution.
- `launcher.py`, `bootstrap.py`: process lifetime and verified resource launch gate.
- `transport.py`: bounded JSONL, deadlines, output budgets, no retries.
- `host.py`: eligibility, batching, activation IDs, backoff, fairness, rendering queue.
- `sdl_core/dynamic.py`: pure state/template/image validation and visual deduplication.
- `configurator/.../plugin_configuration.py`, `plugin_dialogs.py`: pure bindings,
  schema forms, package review/controls, cached metadata, no worker execution.
- `plugins/cpu`: dependency-free reference package reading `/proc/stat` only on demand.

## Required Ubuntu / physical acceptance

Automated simulator tests do not establish these checks:

1. Install both applications and launch under the real graphical user service;
   approve CPU and apply a visible CPU button. Confirm `resourceLimitsEnforced=true`.
2. Inspect actual worker and aggregate `cpu.max`, `memory.high`, `memory.max`, and
   `pids.max`. Confirm a deliberately missing slice prevents plugin startup.
3. Navigate away, pause, blank, reduce brightness to zero, and unplug the deck.
   Verify no worker remains when the last eligible instance disappears; measure
   the stop/kill deadline under systemd and verify no plugin samples occur hidden.
4. Test held-key release during metric repaint, nested Back navigation, reconnect,
   suspend/resume, and independently launched application behavior.
5. Kill the Controller process. Confirm all worker cgroups/descendants stop, then
   confirm the service restarts without re-executing any configured button command.
6. Add 32 independently configured CPU buttons; inspect fair display cadence and
   actual process/Controller CPU usage on the Ubuntu workstation. Device USB timing
   and low-CPU goals require this measurement, not a claim based only on tests.
7. Exercise EN/FR plugin dialogs and high-DPI Wayland/X11 interaction. No Qt desktop
   or physical hardware behavior should be labeled verified unless actually run.

Authoritative external references: systemd v255 `systemd-run` and
`systemd.resource-control` manuals, Linux cgroup v2 documentation, and the kernel
`/proc/stat` documentation. Implementation verifies actual runtime ceilings instead
of assuming an Ubuntu installation exposes every controller.

- https://github.com/systemd/systemd/blob/v255/man/systemd-run.xml
- https://github.com/systemd/systemd/blob/v255/man/systemd.resource-control.xml
- https://docs.kernel.org/admin-guide/cgroup-v2.html
- https://docs.kernel.org/filesystems/proc.html
