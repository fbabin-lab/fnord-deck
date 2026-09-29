> Implementation status: Controller/Configurator and CPU package 0.2.0 implement the polling/display subset. See [the release guide](PLUGIN-HOST-0.2.0.md) for exact supported features, defaults and remaining extension work. Historical sample package versions below do not pin the current CPU release.

# Fnord Deck plugin system — AI implementation specification

**Document:** 1.1.0 · **Plugin wire API:** 1.1 · **Date:** 2026-09-28

**Repository:** `fbabin-lab/fnord-deck` · **Baseline:** `72717708b6df5f53de514f470e85da0136f61078`

**Target:** Ubuntu 24.04; existing Stream Deck XL adapter; separate Controller and Configurator.

## 1. Scope, authority, and delivery status

The user requires plugins to work independently on multiple buttons with different
parameters, avoid unnecessary background work while not displayed, and avoid
excessive refresh rates or expensive repeated operations. A simple CPU display
plugin is required now.

This document **supersedes the plugin sections 12–13 and plugin acceptance items
of `controller/docs/original-spec-v1.0.md`**, including its older 250 ms refresh
floor, 60-second hidden-process retention, and original exclusion of a CPU plugin.
Unrelated navigation, execution approvals, persistence, device ownership and
Configurator safeguards remain in force. No previously released plugin API needs
binary compatibility: both application 0.1.0 releases explicitly deferred plugins.

**This change delivers a specification and a tested reference CPU plugin, not the
production host.** `plugins/cpu` speaks the proposed 1.1 protocol; the finite demo
runs it through stdin/stdout and prints its display states. Controller 0.1.0 still
advertises `plugins: false`; Configurator 0.1.0 still preserves bindings without
loading them. Neither application is modified to claim support. Host lifecycle,
resource enforcement, dynamic rendering, plugin approval, and GUI integration are
implementation work defined below. Installing the reference package or applying
its example layout to application 0.1.0 **cannot display live CPU data on the deck**.

The production host milestone must deliver a real extension system, not hard-code
CPU collection inside the Controller. Storage and HTTP-value plugins, Elgato
Marketplace compatibility, marketplace installation, arbitrary custom GUI code,
continuous monitoring, and autonomous background jobs are outside this milestone.

`MUST` identifies acceptance requirements. `SHOULD` permits a documented reasoned
exception. Timing/CPU figures below are chosen product policies or test targets,
not claims about measured performance on the user's workstation.

## 2. Architecture and ownership

**ARC-01.** The Controller is the only USB owner and the only production plugin
host. The Configurator edits data, reads manifests/status and requests explicit
approval through the local API; it MUST NOT import or execute plugin code. Closing
the Configurator does not stop a visible plugin, and opening/browsing it does not
start an otherwise hidden plugin.

**ARC-02.** One worker process per **plugin ID + exact version + approved package
fingerprint**, serving many instances. Never create a process or repeating timer
per button. Different versions are independent workers. Instances have independent
settings, sample baselines, sequence counters and lifecycle state. Immutable source
snapshots may be shared inside one worker where safe.

**ARC-03.** Preserve these integration seams:

| Component | Responsibility |
|---|---|
| `PluginRegistry` | Data-only discovery, manifest/settings validation, package inventory and version selection |
| `PluginTrustStore` | Explicit fingerprint-bound approvals and disable/revoke state |
| `PluginSupervisor` | Process launch, bounded IPC, limits, deadlines, shutdown, failure accounting |
| `VisibilityResolver` | Authoritative physical-layout demand and revocable activation identities |
| `RefreshScheduler` | Deadline-driven fair polling, batching, no hidden work or catch-up queues |
| `DisplayStateStore` | One newest validated state per binding/activation; freshness and fallback |
| Shared `Renderer` | Literal value formatting, safe image composition, pixel cache |
| Existing action dispatcher | Independent `core.execute` or declared plugin invocation; no implicit execution |

Inject monotonic `Clock`, launcher, transport, source fixtures, renderer and USB
adapter for tests. None of the generic components imports the CPU implementation.
Run plugin work, image decode, hashing and I/O off the Controller event loop. Keep
all Qt widget interaction on the Configurator main thread.

## 3. What counts as visible

**VIS-01.** A display instance is eligible only when **all** conditions hold:

- Its enabled button belongs to the active, applied physical page, with an enabled
  `appearance.dynamic` and at least one permitted visible output field.
- Its package/version/fingerprint is approved, enabled and compatible; settings
  and required secrets are valid.
- The selected device is connected and its current logical layout has completed
  its initial static paint. The Controller is running and not paused.
- The display is not blanked/asleep and brightness is greater than zero.

This is operational visibility, not eye tracking. Do not infer that the user is
looking at the deck. A desktop lock by itself does not prove the deck is hidden;
use actual deck blanking/explicit pause. On system suspend, revoke all activations;
on resume resynchronize the layout and create fresh activations/baselines.

**VIS-02.** Editor selection, an open section tree, an ordinary preview, a saved
configuration, a disabled button and an inactive section NEVER create demand.
The first production milestone provides only cached/static editor previews. A
future explicit simulator live-preview lease must be opt-in, have a lifetime,
and cancel on closure/minimization/disconnection; merely running the simulator
is not sufficient to start all configured plugins.

**VIS-03.** An invoke-only contribution may have a visible actionable instance
without a display subscription. It receives no periodic refreshes. A binding with
both capabilities has independent display and invocation demand. A button with
`core.execute` remains independent of its display plugin.

**VIS-04.** Recompute the desired set on navigation, Apply, device lifecycle,
pause, enable/disable, blanking/brightness changes and suspend/resume, not in a
busy loop. Use set differences. Invalidate obsolete activation IDs before awaiting
any worker acknowledgement; discard their queued refreshes, retries and images.
Do not scan or initialize every nested page at startup.

**VIS-05.** Hidden means zero sampling, network polling, expensive collection,
image production, refresh retries, ping timers and scheduled display callbacks
for that instance. Hiding one instance must not stop others that remain visible.
The worker may continue a shared collector only because another visible instance
still requires it, and at the fastest interval of the remaining visible users.

**VIS-06.** When the final visible/actionable instance disappears and there are no
accepted invocations, stop the worker; there is **no 60-second idle-retention
period**. Stop scheduling synchronously, allow 500 ms for protocol shutdown, then
terminate the complete worker unit/process group, with a further 500 ms before
SIGKILL. Never block the Controller waiting for this. Aim for no runnable worker
within one second; report inability to reap an uninterruptible kernel task rather
than falsely claiming success.

**VIS-07.** A previously accepted user invocation is useful work and may finish
while its button is hidden, within its explicit completion deadline. In this
`invocation-only` state there is no display collection, refresh or keepalive
unrelated to that invocation. Removal/reconfiguration, revocation, Controller stop
or explicit cancellation terminates it according to invocation policy. Independent
applications launched through `core.execute` are not plugin children and retain
the existing application lifecycle.

**VIS-08.** No plugin-owned unbounded polling thread/task is permitted. Poll-only
plugins block on IPC when idle. An in-progress I/O request must be cancellable or
bounded by the refresh deadline. If a worker cannot acknowledge hide within 500
ms, terminate that package worker, discard its responses and mark its still-visible
instances unavailable. Fault isolation is per worker, not per button.

**VIS-09.** Debounce starting newly needed workers by 150 ms to avoid work during
rapid section browsing; immediately revoke hidden demand. A worker still needed
by the final reconciled page can be reused without a stop/start between old and
new bindings. Limit normal launch churn to one start per package per second;
intermediate hidden requests are cancelled, not queued for later launch.

## 4. Refresh policy and bounded scheduling

**SCH-01.** API 1.1 supports **host-driven `poll`** contributions. `push`/`both`
remain reserved, unavailable capabilities until a later protocol explicitly
implements visibility leases, cancellation and equivalent budgets. Do not accept
unsolicited `display.update` notifications in 1.1. CPU, storage and HTTP metrics
can all be implemented through polling without adding autonomous loops.

**SCH-02.** Effective interval:

```text
effectiveIntervalMs = max(1000,
                          hostConfiguredFloorMs,
                          contribution.minRefreshIntervalMs,
                          binding.refreshIntervalMs)
```

The default binding interval is the manifest default, **2000 ms for CPU**. Positive
stored requests below the floor are preserved but clamped with a visible warning.
The effective value is supplied at `instance.create`. A user/plugin cannot bypass
the floor by choosing 0, rapidly navigating, repeatedly reconnecting, manually
refreshing, or changing only the label. Enforce limits in the host; the CPU plugin
also defensively rejects early requests.

**SCH-03.** One deadline queue, sleeping until the next deadline or lifecycle
event. No 10/60 Hz scheduler tick and no per-instance sleeping threads. At most
one collection request in flight per worker, including a `refreshMany` batch.
Group due instances of the same worker, up to 128, into one request. Keep a single
pending refresh intent per instance; never accumulate missed ticks.

**SCH-04.** On completion, next due is no earlier than dispatch start + effective
interval and no earlier than completion. After an overrun, schedule from completion
+ effective interval; skip missed intervals. A slow response never triggers an
immediate catch-up burst. Normal control acknowledgements use 500 ms; bootstrap
and create use 5 seconds; refresh uses `min(5000,max(1000,effectiveIntervalMs))`
ms. A batch deadline is the largest deadline of its due members, capped at 5000.
The Controller enforces deadlines; an uncooperative worker can be terminated.

**SCH-05.** Source acquisition and per-button presentation are separate. A plugin
SHOULD group work by a safe source key and read once per due batch/interval. CPU
uses one `/proc/stat` source for aggregate and per-logical-CPU instances. Each
instance calculates against its own baseline so different intervals produce the
correct independent windows. A new visible instance must not use a sample from
before its activation as a fresh baseline.

For future storage plugins, cache by normalized mount/device identity; do not walk
a directory tree to find free space. For HTTP plugins, group only truly equivalent
requests, including method, endpoint, headers, account/secret identity and relevant
parameters. Never share cached private responses across different credentials.
Do not include secret plaintext in cache keys, logs or exports.

**SCH-06.** Success with an unchanged value is still a fresh sample, but is not a
reason to repaint. Failed/blocked/hidden or replayed cached responses do not renew
freshness. Cache source errors for the same source cadence so 32 buttons do not
cause 32 failing reads. On recoverable collection failures back off 2, 4, 8… up to
60 seconds, never faster than the effective interval; bound HTTP timeouts, body
size and redirects, and respect Retry-After. Cancel retry deadlines when hidden.
No catch-up history or invisible prefetching is allowed.

## 5. Rendering policy — avoid both CPU and USB waste

**REN-01.** Store only the newest complete accepted state per active instance.
Check approved worker identity, instance epoch, activation ID, configuration/page
and target before accepting a state, before decoding, before rendering and again
before queuing a hardware write. Late results cannot paint a different page.

**REN-02.** Validate cheap scalar/size limits first. Compare the effective visual
state after number rounding, allowed overrides, template formatting and progress
quantization. Update freshness without rerendering equal content. Cache the upright
image and skip USB writes when its pixel hash is unchanged. Do not rehash assets,
reload fonts, spawn a converter or render the entire page on every CPU sample.
CPU publishes only values; it never rasterizes PNG/JPEG frames.

**REN-03.** Default limits are **one dynamic repaint per key per second**, and
**eight dynamic key writes per second per physical device**, with a burst of eight
for batching. Use a fair round-robin queue with one newest pending state per key.
Do not let a fast-changing key starve another. Initial/static page paint and the
generated Back key are priority control work, not counted as routine metric
traffic. Metric frames must not fill the USB queue ahead of navigation.

At 32 continuously changing keys, a complete dynamic refresh can take about four
seconds at this budget, even if sampling is every two seconds. Show requested and
effective refresh/paint cadence in diagnostics; do not claim every key is painted
at its sampling rate. Maximum pending key count is the device key count, not the
number of configurations. Set stale policy to at least three times the larger of
effective sampling interval and fair-share paint interval, and at least 10 seconds.

**REN-04. Important baseline fix.** `sdl_controller/controller.py:render_loop`
currently calls `InputGate.reset` during every repaint. The implementing AI MUST
split **layout/input invalidation** from **dynamic visual invalidation**. A CPU
percentage change must not reset a held press, disable click processing, increment
page generation, revoke plugin visibility or clear a synchronized-layout latch.
Use distinct `layoutGeneration` and visual versions. Navigation/Apply/disconnect
still revoke pending presses under the existing safety rules. This also prevents
a cycle where painting a plugin makes it “not synchronized”, hides it, and starts
it again indefinitely.

**REN-05.** Serialize physical writes through the existing adapter. A queued metric
write checks its layout generation before dispatch; page changes invalidate the
queue, and the new complete static page paint follows any already-dispatched
uncancellable USB call. Plugins get no USB handles, native JPEG/orientation knobs,
or permission to replace Back or core status/approval overlays.

## 6. Resource enforcement and trust

**RES-01.** Throttling image output alone is insufficient: computation, network,
logs, allocations and descendant processes must also be bounded. Run production
workers as systemd **user** units under a dedicated plugin slice, separate from
the Controller and from independently launched applications. No root runtime.
Configure unit control-group termination and a Controller-lifetime dependency.
A plugin unit must not outlive a Controller crash. See systemd sources [S3–S4].

| Policy | Lightweight package (CPU) | Aggregate plugin slice |
|---|---:|---:|
| CPUQuota | 5% of one logical CPU | 15% of one logical CPU |
| MemoryHigh / MemoryMax | 48 MiB / 64 MiB | 384 MiB / 512 MiB |
| TasksMax | 8 | 64 |
| Maximum live workers | one per package identity | 8 |
| Maximum registered live instances | 128 per worker | 128 initially |
| Open-file limit | 64 | n/a |
| Nice level | +10 | n/a |

These are ceilings, not budgets to consume. Resource profiles needing higher
limits require explicit approval, not a plugin-declared escape. CPUQuota is
relative to **one CPU**, not a percentage of all cores [S3]. Verify cgroup controls
are actually available/enforced before claiming a protected mode. If they are
unavailable, default to `RESOURCE_LIMIT_UNAVAILABLE` and leave plugins disabled;
an explicitly selected development mode may run trusted packages with a prominent
“resource limits not enforced” diagnostic. The shipped terminal demo is that sort
of test harness, not production enforcement.

**RES-02.** Check worker CPU/memory counters at most once every five seconds while
workers are active; no separate sampling loop per button. Use accounting to
throttle/quarantine persistent overruns and expose diagnostics. Do not repeatedly
scan every process or use an expensive resource monitor to supervise this feature.
Host-side rendering and parsing are outside plugin cgroups: enforce their queue,
frame, image and frequency limits independently.

**RES-03.** Per process: 256 KiB maximum JSONL frame including LF; no JSON-RPC
batches; at most 64 incoming messages/s, burst 128; 512 KiB/s incoming bytes, burst
512 KiB. Stop malformed/oversized framing immediately. Stop sustained flood after
one second, and reject/coalesce over-budget states before image decode. Bounded
stderr ring 64 KiB; logging maximum 5 messages/s, burst 10, messages at most 512
characters, counters instead of recursive warning floods. No unbounded lines,
queues, histories or per-sample log files.

**RES-04.** Protocol scoping, subprocess separation, quotas and package hashes are
**not a hostile-code security sandbox**. Native code running as the same desktop
user has that user's privileges and may access files/network or attempt to escape
its unit. Declare permissions accurately and require trusted-code approval. A
future stronger sandbox needs a separate security design; do not claim OS-level
permission denial merely because a manifest says `network: false`.

## 7. Package, manifest and approval

**PKG-01.** Packages reside under
`$XDG_DATA_HOME/streamdeck-linux/plugins/<id>/<version>/manifest.json`. Registry
scans are data-only: never execute an import, install hook or dependency downloader.
Reject path escape, symlinks/special files, conflicting identities, unsupported
versions, invalid schemas and unlisted package files. Do not silently upgrade.

**PKG-02.** Manifest version **1.1** fields are:

| Field | Contract |
|---|---|
| `id`, `version` | Reverse-DNS ID, no `core.*`; exact SemVer release |
| `name`, `description` | Locale maps, required English fallback |
| `api` | `{min:"1.1",max:"1.1"}` for this first worker API |
| `protocolFeatures` | Supported feature names; this reference declares `["refreshMany"]` |
| `resourceProfile` | `lightweight`; future profiles require host support/approval |
| `entrypoint` | Package-relative executable, or explicit interpreter/script plus literal argument arrays |
| `files` | Map of every regular package file except manifest to lowercase SHA-256 |
| `assets` | Registered image asset key → package-relative PNG path, included in `files` |
| `permissions` | Disclosed filesystem reads, network/process needs and secrets; not a sandbox |
| `contributions` | IDs, localized names, display/invoke capabilities, settings and update policies |

Contributions retain the prior fields `id`, `name`, `capabilities`,
`settingsVersion`, `settingsSchema`, `secrets`, `commands`, `updateMode`,
`minRefreshIntervalMs`, `defaultRefreshIntervalMs`, and add
`backgroundPolicy: "visibleOnly"`. Commands have `{id,name,description}`; display-
only contributions have an empty command list. Settings use inline JSON Schema
Draft 2020-12, simple typed objects, primitives, enums, bounds and arrays of
primitives. No remote `$ref`, script validator or custom executable form. Unknown
required capability means unavailable, never “partially enabled”.

**PKG-03.** Exact fingerprint construction:

```text
SHA256(b"fnord-deck-plugin-v1.1\0"
       + uint64_be(len(raw_manifest_utf8)) + raw_manifest_utf8
       + concat_for_path_sorted_files(
           uint64_be(len(path_utf8)) + path_utf8 + raw_sha256_file_32_bytes))
```

Verify file bytes against the manifest. Inventory/path ordering is lexical UTF-8
package-relative POSIX path order. No whitespace normalization of the manifest.
`plugins/tools/verify_package.py` demonstrates this calculation without importing
plugin code; full production schema/ownership/race checks remain host work.

**PKG-04.** Approval binds ID, version, fingerprint, interpreter identity/path,
permissions and resource profile. Reverify before launch and invalidate approvals
on package changes. An OS-managed interpreter is disclosed separately, outside
package file hashes. Keep caches/bytecode outside the verified package; the CPU
entrypoint uses `/usr/bin/python3 -I -B`. Hold a stable verified copy or equivalent
race-resistant file handles during launch; do not approve one path then execute a
replaced one. File hashes detect modification, not authorship or safety.

**PKG-05.** Explicit installation/approval may enable future visible execution,
but scanning/importing/saving does not execute packages. Applying an already
approved binding can start its **display collection if physically visible**;
it never invokes a command. Make that distinction explicit in the Apply dialog.
Revoking approval stops the worker and leaves data/static fallbacks intact.

## 8. Binding and independent instances

**CFG-01.** Retain the existing document schema 1.0 binding shape. Example:

```json
{
  "instanceId": "be746fb8-07b7-48ed-adab-a7e187e9c386",
  "pluginId": "org.fnord.cpu",
  "pluginVersion": "0.1.0",
  "contributionId": "usage",
  "settingsVersion": 1,
  "settings": {"scope":"total","logicalCpu":0,"label":"CPU","decimals":0,"warningPercent":85},
  "secretRefs": {},
  "refreshIntervalMs": 2000
}
```

**CFG-02.** Every bound button has a unique stable UUID `instanceId`, not the
package ID. A second button uses a different instance ID and may choose logical
CPU 0, a different label or a longer interval. Copying a button/subtree creates
new instance IDs; moving retains IDs but changes activation identity. Validate
uniqueness across the complete applied configuration. No mutable global “current
settings” variable may be shared between instances.

**CFG-03.** `appearance.dynamic` is a separately validated object:

```json
{"enabled":true,"textTemplate":"{{label}}\n{{value}}{{unit}}","allowedOverrides":["text","progress"],"numberDecimals":0,"staleAfterMs":10000}
```

Allowed substitutions are literal `{{label}}`, `{{value}}`, `{{unit}}`,
`{{status}}`; no expressions or traversal. Overrides are text, icon, progress;
static appearance is fallback. Plugin output cannot become a script path,
argument, environment override, navigation target or approval token. A CPU display
may coexist with `core.none`, `core.navigate`, `core.home` or `core.execute`.

**CFG-04.** Do not conflate config schema version, API version, package version,
settingsVersion and runtime revision. Incompatible settings need explicit
reconfiguration, not automatic code-run migrations. Imported secrets/approvals
are not transferable. The CPU plugin requires no secrets. A host without a secure
Secret Service backend must reject secret-requiring contributions rather than
fall back to plaintext storage. Never export secret values in layout bundles.

## 9. Wire API 1.1

JSON-RPC 2.0 envelopes [S5], one UTF-8 object + LF per frame on stdin/stdout.
No binary frames, JSON-RPC batches, arbitrary stdout, duplicate JSON keys or
non-finite numbers (including overflowing exponent forms). Host request IDs are
`h-` followed by 1–80 ASCII letters/digits/underscore/hyphen; plugin-originated
requests use `p-`. Lifecycle and refresh methods require request IDs; notifications
must not accidentally execute lifecycle work. Stdout is protocol-only; diagnostics
go through a bounded channel. EOF means host lost: release resources and exit.

**RPC-01.** Initialize before any instance operation. Parameters:

```json
{"apiVersion":"1.1","hostVersion":"future-host","pluginId":"org.fnord.cpu","pluginVersion":"0.1.0","fingerprint":"0000000000000000000000000000000000000000000000000000000000000000","locale":"en","limits":{"minRefreshIntervalMs":1000,"maxInstances":128,"maxMessageBytes":262144}}
```

The all-zero fingerprint is illustrative only; production sends the verified hash.
Response: `{apiVersion:"1.1",pluginVersion:"0.1.0",capabilities:["display","refreshMany"]}`.
Duplicate initialization or unsupported API fails without creating instances.

**RPC-02.** Common instance context is `{instanceId,instanceEpoch}`; both canonical
UUIDs. A new epoch is generated on creation/recreation, settings change or worker
restart. Visibility adds a fresh UUID `activationId`. Sequence increases within
the instance epoch, including successive activations. The host maps these identities
to configuration revision, layout generation, page, device and button. Plugins
cannot select a target by sending an unrelated identity.

Target descriptor: `{deviceId,keyIndex,width,height,locale,displayFields}`;
indices zero-based and dimensions logical/upright. It has no native device handle.
For the XL the current adapter supplies 96×96; do not embed that in generic workers.

| Host method | Parameters / result |
|---|---|
| `plugin.initialize` | As above; no source collection |
| `instance.create` | Context + contributionId, settingsVersion, settings, secretNames, target, effectiveRefreshIntervalMs → `{ready:true}`; initially hidden |
| `instance.visibility` | Context + visible Boolean, activationId (null when hidden), target → `{ok:true}`; no implicit sampling |
| `instance.refresh` | Visible context + deadlineMs → complete state result |
| `instance.refreshMany` | `{instances:[visibleContext...],deadlineMs}` → `{results:[stateResult or contextual error...]}` |
| `instance.destroy` | Context + reason (≤128 characters) → `{ok:true}`; free all per-instance state |
| `instance.invoke` | Visible context + invocationId, commandId, eventTime, pressDurationMs → `{accepted:true}`; invoke-capable contributions only |
| `instance.cancel` | Context + invocationId, reason → `{accepted:true|false}`; cooperative, not undo |
| `plugin.ping` | `{}` → `{ok:true}`; only after a demanded operation appears stalled, never periodic idle traffic |
| `plugin.shutdown` | `{reason}` → `{ok:true}` then exit |

**RPC-03.** Refresh batching is a method, not a JSON-RPC batch envelope. Validate
batch size and unique instance IDs before doing any collection. Each item is either
`{instanceId,instanceEpoch,activationId,sequence,state}` or the same three context
fields plus `error:{code,message,data}`. One hidden/stale/failed instance does not
invalidate others. A well-formed rejected instance performs no source work. A
package lacking `refreshMany` is polled sequentially under the same per-worker
concurrency/resource bounds; its source cache must still deduplicate acquisition.

**RPC-04.** Standard errors: -32700 parse, -32600 invalid request, -32601 unsupported
method, -32602 invalid parameters. Reference application errors: -32001 not
initialized; -32002 already initialized; -32003 absent/stale epoch; -32004 hidden
or stale activation; -32005 rate limited (`retryAfterMs`); -32007 instance cap;
-32008 no new source sample; -32009 stopping. Error code is never a script exit
code. Messages expose no secret or arbitrary filesystem contents.

**RPC-05.** The CPU reference supports display methods only. No network, secret,
invoke or push handler is present; undeclared methods return unsupported. Future
invoke-capable workers may issue `invocation.finished` notifications with context,
invocationId and succeeded/failed/cancelled outcome, and declared
`host.secret.read` requests. Validate source identity/scope and exactly-once result
recording. `host.log` is bounded diagnostic output, not a display update.

## 10. Display state and invocation safety

**DSP-01.** Complete snapshot, not a patch:

```json
{"value":25,"unit":"%","label":"CPU","status":"ok","message":null,"progress":0.25,"image":null}
```

Value is finite number, string ≤256 characters or null; unit ≤32, label ≤128,
message ≤512; status ok/warning/error/unavailable; progress 0–1 or null. Image is
null, `{kind:"asset",assetKey:"declared-key"}` or `{kind:"png",dataBase64:"..."}`.
No paths/URLs/SVG/HTML/drawing code. PNG limit: 128 KiB decoded file bytes, one
million decoded pixels, then safe normalization outside the event loop. The
entire encoded message must still fit 256 KiB. CPU always uses image=null.

**DSP-02.** Freshness uses host monotonic receipts of genuine accepted measurements,
not plugin wall-clock timestamps. Sequence/epoch/activation replay does not renew
freshness. Keep last good data with a stale/error marker on failure; before the
first good value use static fallback with unavailable/loading indication, never
pretend the CPU is 0%. Null and zero are different. On hide, discard pending
visuals and clear freshness; on show, obtain a new baseline for delta metrics.

**DSP-03.** Composition remains background → static/allowed dynamic icon →
static/allowed dynamic text → optional progress → core status indicators. Generated
Back overrides all plugin content. Progress is quantized to visible pixels and
user precision so subpixel changes do not repeatedly rasterize a button.

**INV-01.** Invoke only on an accepted release-based activation or separately
confirmed test. Persist unique invocation intent before dispatch. No automatic
retry after lost acknowledgement, process crash, reconnect, Apply or host restart.
Record `unknown` when an external side effect might have occurred. An invocation
has a default 60-second completion deadline, with a declared/approved maximum of
300 seconds; the host—not the plugin—enforces it. Cancellation is not rollback.
Page hiding cannot generate an invocation, and display collection never runs user
commands. Over-limit/unresponsive workers follow package quarantine policy.

## 11. Failure recovery and observability

**FAIL-01.** A crash, malformed frame, resource-limit violation, timed-out shutdown
or repeated overrun affects only the worker's instances. Back, navigation, static
buttons, core executable actions and other packages remain responsive. CPU-source
read errors produce unavailable data and normal bounded retry, not a process
restart loop.

**FAIL-02.** Restart only while visible/action demand still exists and approval is
still valid. Backoff 1, 2, 4 seconds, at most three automatic restarts in 60 seconds,
then quarantine until explicit retry. Hidden periods cancel timers, but do not
reset the package failure counter, so navigation cannot bypass quarantine. Reset
failure accounting after 60 seconds of successful active operation. No invocation
replay. Normal hide shutdown is not a crash.

**OBS-01.** Expose per package/instance: identity, status, visible demand count,
requested/effective interval, last measurement age, last paint age, next due,
refresh count/duration, dropped late states, skipped identical states, throttled
writes, source-read count where voluntarily exposed, restart count and reason,
resource-enforcement status, CPU time/memory totals. No per-sample log spam, secret
values or unrestricted plugin-provided labels as metric label dimensions.

## 12. Controller and Configurator integration requirements

**INT-01.** Extend, do not bypass, the existing same-UID local API. Required methods:
`plugins.list`, `plugins.rescan`, `plugins.approve`, `plugins.setEnabled`,
`plugins.restart`; status/health events. Listing/rescanning reads metadata only;
restart when not demanded returns dormant without launching. Approval requests
must include exact ID/version/fingerprint and explicit confirmation. Keep applied
binding changes inside normal revision-aware configuration Apply.

**INT-02.** Advertise plugin API/feature/resource support in `hello`. Old Controller
responses keep plugin controls disabled with a clear “host not supported” message.
Do not change the meaning of existing API 1.0 operations or fabricate live data.
Missing plugins preserve settings and static fallbacks. Upgrade both shared-core
copies/schema validators/renderers together; refresh
`configurator/docs/shared-core-origin.json` and parity tests whenever core changes.

**INT-03.** Add a generic contribution selector and schema-driven settings panel,
refresh interval and effective-value explanation, per-instance status, package
approval/disable UI, static fallback editor and visibility explanation. Maintain
English/French application catalogs. No CPU-specific settings widget. Show logical
CPU index explicitly, not “physical core”. Duplication renews instance identity;
undo/redo preserves valid binding semantics.

**INT-04.** Save/import/preview/ordinary grid clicks do not execute plugin commands.
Apply may activate an already trusted visible display, after the normal review;
Run is separate. GUI preview uses static/cached state in this milestone and does
not keep CPU/network work alive. Neither a plugin nor the editor writes the
Controller's applied-state files directly.

## 13. CPU reference plugin behavior

`plugins/cpu/manifest.json` is the concrete manifest and settings schema.
`plugins/cpu/cpu_usage.py` uses only the Python standard library and the JSONL
contract; it imports no Controller, Qt or USB code. No third-party dependency or
root privilege is needed for the reference terminal demo.

It obtains `/proc/stat` CPU rows only on accepted visible refresh requests,
reading the bounded CPU prefix rather than the potentially long interrupt table.
One source snapshot is cached for the fastest visible effective interval, including
failed reads. A `refreshMany` batch shares one timestamp/acquisition window.
Per-instance baselines support different intervals without contamination.

The kernel exposes cumulative counters, including an aggregate CPU row [S1–S2].
The reference computes:

```text
deltaTotal = sum(delta(user,nice,system,idle,iowait,irq,softirq,steal))
deltaBusy  = deltaTotal - deltaIdle - deltaIowait
percentage = 100 * deltaBusy / deltaTotal
```

Guest and guest_nice are already accounted within user/nice and are not added
again [S6]. This convention treats steal as non-idle and iowait as non-busy. Values
are 0–100 for aggregate or one logical CPU, not load average or process usage.
Iowait can decrease [S1]; any first-eight-field regression, zero total delta,
read/parse failure or logical-CPU topology change resets the baseline and produces
unavailable rather than a misleading result. The first post-show fresh sample
establishes a baseline; the next due sample normally yields a percentage. Hidden
periods are not included. It measures the Linux view of `/proc/stat`, not Docker
CPU quota utilization.

Parameters: total versus logicalCpu scope, index 0–4095, label ≤32 printable
characters, decimals 0/1, warningPercent 1–100. Default interval 2000 ms, defensive
floor 1000 ms. The package shares source reads even when instances differ in label,
threshold or display precision. It publishes rounded value/progress, so unchanged
rounded output can skip rendering. Unsupported/absent CPUs show unavailable.

**Reference limits:** stdin blocking means no unsolicited activity when the host
sends no requests. It checks its own cadence/visibility but cannot know which
physical page is shown without the host telling it. It cannot enforce a production
host's cgroups, USB budgets or global multi-package limits; those remain host
requirements. The terminal demo is finite and leaves the installed apps untouched.

## 14. Acceptance tests and measurement

The implementing AI MUST add production host tests with injected clocks and real
subprocess fixtures; reference-plugin unit tests alone do not certify host behavior.

| Test | Required result |
|---|---|
| AT-01 Startup, unbound/inactive pages | Zero plugin workers or source reads |
| AT-02 Editor browse, save, import, preview | No worker launch/collection/invocation |
| AT-03 Show CPU button | One approved worker; baseline then percentage; default two-second cadence |
| AT-04 Hide final instance | Immediate refresh cancellation; worker termination requested within 500 ms, force escalation by one second |
| AT-05 Pause/disconnect/blank/brightness zero | All display demand revoked; no retry or poll until visible again |
| AT-06 One of two instances hidden | Other continues; hidden fast interval stops driving collector |
| AT-07 Thirty-two CPU instances | One worker; one source read per source interval/batch, independent states |
| AT-08 Different scopes/intervals | Aggregate and logical-CPU values correct; each baseline independent |
| AT-09 Rapid hide/show/settings/copy | New identities where required; no stale paint, no rate-limit bypass |
| AT-10 Baseline/reset/hotplug/iowait decrease | Unavailable then recovery, no false 0%/out-of-range result |
| AT-11 Slow/overlapping refresh | One in flight; missed ticks skipped, no catch-up burst |
| AT-12 Old epoch/activation response | Rejected before render/USB, even after page returns |
| AT-13 Identical displayed values | Freshness advances; render count and USB writes do not |
| AT-14 Thirty-two changing keys | Eight dynamic writes/s budget with fairness; bounded pending queue |
| AT-15 Hold key during CPU update | Original matching release activates once; metric does not reset input gate |
| AT-16 Navigate during decode/USB | Old work cannot overwrite final new page; Back remains usable |
| AT-17 CPU spin/alloc/fork/log/image floods | Limits verified; offending worker stopped, Controller remains responsive |
| AT-18 Worker crash/hang/parent SIGKILL | No surviving unmanaged workers/descendants; bounded restart only while demanded |
| AT-19 Bad manifest/hash/path/symlink/version | No code executed; approval invalidated |
| AT-20 Secret unavailable/imported config | No plaintext fallback/automatic authorization |
| AT-21 Invoke + hide | Only accepted bounded invocation continues; zero display work; no automatic retry |
| AT-22 Old application compatibility | Plugins unavailable, preserved bindings, static fallbacks; no false capability flag |
| AT-23 Suspend/resume | Fresh activation and baseline; no synthetic click or catch-up |
| AT-24 Bad JSON, duplicate keys, huge frame, NaN | Bounded rejection/termination; no unbounded parse/log work |
| AT-25 Mixed package versions/settings | Separate workers per fingerprint; no cross-instance settings leakage |
| AT-26 No resource-controller support | Explicit unavailable/default disabled, never silently claim isolation |

Resource acceptance on Ubuntu 24.04: record versions, device, workload, active
instance count, process CPU time divided by wall time **per one CPU**, RSS, sample
count, render count, USB writes and visibility transitions. Compare an empty deck,
one CPU button, 32 CPU buttons, one expensive/failing fixture, then hidden for 60
seconds. Suggested target after warm-up: CPU reference worker below **0.5% of one
CPU** averaged over 60 seconds at the default cadence. Hidden collection count
must be exactly zero; workers should be absent, not merely reporting low CPU.
A target miss must be reported with data, not hand-waved or “passed” from a
simulator. Automated fixture timing is not physical performance certification.

## 15. Implementation sequence / meaningful milestones

**P0 — delivered here:** this specification, versioned CPU manifest, runnable
stdlib worker, finite protocol demo, package hash verifier, deterministic and real
subprocess tests, example two-instance layout. No production host/UI changes.

**P1 — registry and lifecycle:** approvals, visibility resolver, bounded worker
transport, enforced user-unit quotas and cleanup, clock-driven poll scheduler,
status diagnostics. Demonstrate zero workers for hidden pages and no orphan after
Controller crash with test fixtures. Keep UI plugin editing disabled until ready.

**P2 — dynamic display + CPU:** independent visual invalidation, shared-core
DisplayState composition, unchanged-content suppression, fair USB budget. Bind
provided CPU plugin to two buttons through tested configuration; verify navigation,
pause/disconnect, hold/release and fast/slow instance cases in simulator and on XL.

**P3 — Configurator and operational completion:** generic manifest forms, approval
workflow, interval explanations, per-instance health, compatibility gating and
EN/FR updates; no editor-induced background polling. Complete Ubuntu resource and
hardware acceptance, packaging/install/uninstall docs and regression suites.

**P4 — optional later capabilities:** push/subscription visibility leases, storage
and web-service plugins, stronger hostile-code sandboxing, background jobs under
an explicitly different permission/lifecycle contract. None is implicitly enabled
by this specification or by the CPU example.

## 16. Source references

These sources define external API/accounting behavior, not the chosen product
budgets. Accessed 2026-09-28. Kernel/accounting text is summarized, not reproduced.

- [S1] Linux kernel documentation, `/proc`, section 1.7:
  <https://docs.kernel.org/filesystems/proc.html>
- [S2] Linux man-pages project, `proc_stat(5)`:
  <https://man7.org/linux/man-pages/man5/proc_stat.5.html>
- [S3] systemd upstream resource-control manual (man7 rendering), CPUQuota,
  MemoryHigh/MemoryMax, TasksMax:
  <https://man7.org/linux/man-pages/man5/systemd.resource-control.5.html>
- [S4] systemd upstream kill manual (man7 rendering), control-group termination:
  <https://man7.org/linux/man-pages/man5/systemd.kill.5.html>
- [S5] JSON-RPC working group, JSON-RPC 2.0 specification:
  <https://www.jsonrpc.org/specification>
- [S6] Linux v6.8 source, `account_guest_time` in `kernel/sched/cputime.c`:
  <https://github.com/torvalds/linux/blob/v6.8/kernel/sched/cputime.c>
