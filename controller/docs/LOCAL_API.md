# Local API 1.0

Socket: `$XDG_RUNTIME_DIR/streamdeck-linux/controller.sock`; simulator substitutes `streamdeck-linux-simulator`. Directory `0700`, socket `0600`, peer UID checked with Linux `SO_PEERCRED` on both client and server. No TCP listener.

Frames: 4-byte unsigned big-endian JSON byte length, then UTF-8 JSON-RPC 2.0 object. Maximum 16 MiB. Duplicate object keys, invalid Unicode, non-finite numbers, >64 nested containers, batches and client notifications are rejected. All client requests require a unique nonempty string ID, max 128 characters. A connection accepts at most 1,024 request IDs before reconnect; maximum 16 clients. Partial bodies time out after 30 seconds. Slow event writers are disconnected, never buffered without bound.

First request:

```json
{"jsonrpc":"2.0","id":"1","method":"system.hello","params":{"apiMajor":1,"apiMinor":0,"clientName":"my-configurator"}}
```

Major version mismatch blocks API use. Hello reports current capabilities, including `plugins:false` and `secrets:false`. The JSON parameter contracts are exported in `schemas/local-api-parameters-v1.json`; the complete configuration schema is separate. Envelope/result structures are supplied in `schemas/local-api-envelopes-v1.json`. `src/sdl_cli/client.py` is a working reference client.

## Supported methods

| Method | Main parameters/result |
|---|---|
| `system.hello` | `apiMajor`, `apiMinor`, `clientName`; negotiated API/runtime/capabilities |
| `system.snapshot` | Device state, revision, current page/generation, pause, recent job summaries, recovery/error status |
| `system.diagnostics` | Version/codec/backend/font checks; no environment values or program output |
| `events.subscribe` | Optional `types` array; empty = all; subscription/runtime epoch/sequence |
| `configuration.get` | Optional positive `revision`; no revision or 0 = current document |
| `configuration.validate` | `document`; bounded errors/warnings; no mutations or execution |
| `configuration.review` | `document`; executable summary/hash and 300-second review token |
| `configuration.apply` | `document`, `expectedRevision`, `operationId`; new/changed executions also need `reviewToken`, `confirmed:true` |
| `configuration.history` | Optional `cursor`, `limit`; retained revisions and validity |
| `asset.beginImport` | `name`, `byteCount`, `sha256`; upload UUID, chunk budget |
| `asset.writeChunk` | `uploadId`, `sequence`, base64 `data`; acknowledged sequence |
| `asset.finishImport` | `uploadId`; content ID, media type/dimensions/warnings |
| `asset.readChunk` | `assetId`, `offset`, `length`; base64 original bytes, total size |
| `device.list` | Detected descriptors without changing selected device; serial may be unknown until opened |
| `device.retry` | Optional selected `deviceId`; retry only, never force-takeover/change selection |
| `runtime.navigate` | `pageId`, `expectedRevision`; new page/generation |
| `runtime.pause` | `paused`; effective pause state |
| `session.update` | `variables` object, allowlisted names/values; graphical-context status |
| `execution.test` | Applied `buttonId`, `expectedRevision`, `confirmed:true`, `operationId`; run ID/state |
| `execution.list` | `cursor`, `limit`, optional `includeOutput:false`; bounded summaries or explicit live output |
| `execution.cancel` | `runId`; task cancellation; independent applications unsupported |
| `plugins.list` | Honest deferred/unavailable capability status |
| `render.preview` | Optional applied `pageId`; base64 upright PNG grid, page/revision; no execution |
| `simulator.key` | `keyIndex`, `down`; simulation only |
| `simulator.connection` | `connected`; simulation only |

All remaining `plugins.*`/`secret.*` operations return `FEATURE_DEFERRED`. Unknown methods return `METHOD_NOT_FOUND`. Synthetic input is never exposed on the physical Controller. Device/brightness changes go through a new configuration Apply, not a hidden second store.

## Apply and concurrency

`operationId` must be a UUID. Repeating it with the same document + expected revision returns the previous result, even after restart, within the retained 100 outcomes. Reusing it for other content gives `OPERATION_CONFLICT`. Stale expected revision gives `REVISION_CONFLICT`.

An Apply response acknowledges durable configuration, not a completed hardware repaint. It contains `configurationApplied:true`, `deviceSynchronized:false` at the commit boundary. Observe `device.synchronized` or fetch `system.snapshot` for subsequent display status. A disconnected device does not prevent Apply. Saving/applying never invokes a button action.

Review is an accidental-activation guard for same-user clients, not a boundary against malicious software under the same UID. The client must actually show/confirm the summary. A review token alone is insufficient; Apply also requires `confirmed:true`.

## Events

After the subscription response, the server sends `runtime.event` notifications. Params contain `runtimeEpoch`, `eventSequence`, current `revision`, `type`, `payload`. Types include `configuration.applied`, `device.changed`, `device.synchronized`, `runtime.navigated`, `runtime.paused`, `button.activated`, `execution.changed`, `action.error`, `render.error`, and `input.resynchronized`.

Sequences count all runtime events, including types filtered out of a subscription. A filtered subscriber must not treat every numerical gap as loss; reconnect or unexpected state still requires a fresh snapshot. To track every sequence use the unfiltered subscription. Start with a fresh snapshot after subscribing; an event may legitimately describe a state transition already reflected in the snapshot.

## Errors, limits and privacy

Errors use standard JSON-RPC error envelopes plus `error.data.code`, `messageKey`, `details`. Common codes: `REVISION_CONFLICT`, `OPERATION_CONFLICT`, `VALIDATION_FAILED`, `REVIEW_REQUIRED`, `PATH_UNAVAILABLE`, `EXECUTION_DISABLED`, `ALREADY_RUNNING`, `OUTCOME_UNKNOWN`, `FEATURE_DEFERRED`, `ASSET_INVALID`, `LIMIT_EXCEEDED`, `DEVICE_PERMISSION_DENIED`, `DEVICE_IN_USE`, `DEVICE_UNAVAILABLE`. No stack trace or complete environment is returned.

Uploads are scoped to their connection, expire after 10 minutes, permit two active imports/client, and use sequential raw chunks at most 256 KiB. Disconnect removes unfinished uploads. Original images max 20 MiB, decoded rasters max 40 million pixels. Worker conversion is bounded to five seconds and 512 MiB address space.

The last 200 execution records/intents are retained. Output buffers are live memory only, omitted by default, and never returned in routine event payloads. Do not blindly retry `execution.test` after an interrupted connection; query its retained operation/outcome with the same operation ID and parameters.
