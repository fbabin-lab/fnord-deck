"""Visibility-driven generic display host: bounded workers, scheduling and painting.

The CPU implementation is intentionally not imported. All plugin work crosses RPC.
"""
from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict, defaultdict, deque
import contextlib
import copy
from dataclasses import dataclass, field
import hashlib
import math
import logging
import shutil
import time
from uuid import uuid4

from sdl_core.dynamic import DEFAULT_DYNAMIC, validate_dynamic, validate_state, visual
from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest
from sdl_core.model import Capabilities, pages_by_id
from .launcher import PluginLauncher
from .registry import PluginRegistry, read_at, validate_settings
from .transport import Budget


@dataclass
class Live:
    item: dict
    package: object
    contribution: dict
    context: object
    target: dict
    interval: float
    epoch: str = field(default_factory=lambda: str(uuid4()))
    activation: str = field(default_factory=lambda: str(uuid4()))
    due: float = 0.0
    sequence: int = 0
    errors: int = 0
    refreshes: int = 0
    last_measurement: float | None = None
    last_paint: float | None = None
    last_duration_ms: float = 0
    state: dict | None = None
    painted_state: dict | None = None
    good: dict | None = None
    visual_key: str | None = None
    visual_version: int = 0
    skipped_identical: int = 0
    late: int = 0
    stale: bool = False

    @property
    def identifier(self):
        return self.item["pluginBinding"]["instanceId"]

    def wire_context(self):
        return {"instanceId": self.identifier, "instanceEpoch": self.epoch, "activationId": self.activation}

    def instance_context(self):
        return {"instanceId": self.identifier, "instanceEpoch": self.epoch}


@dataclass
class Session:
    package: object
    worker: object = None
    busy: asyncio.Task | None = None
    members: dict = field(default_factory=dict)
    phase: str = "starting"
    started: float = 0
    stats: dict = field(default_factory=dict)
    resource_strikes: int = 0


def next_due(start: float, completed: float, interval: float) -> float:
    return completed + interval if completed - start > interval else max(completed, start + interval)


class PluginHost:
    def __init__(self, controller, *, mode="systemd", allowed=True, launcher=None, clock=time.monotonic):
        self.controller, self.allowed, self.clock = controller, allowed, clock
        paths = controller.paths
        self.registry = PluginRegistry(paths.data / "plugins", paths.config / "plugin-approvals.json", paths.runtime / "plugin-snapshots")
        self.launcher = launcher or PluginLauncher(mode)
        self.mode = mode
        self.instances: dict[str, Live] = {}
        self.sessions: dict[str, Session] = {}
        self.blocked: list[dict] = []
        self.failures = defaultdict(lambda: deque(maxlen=4))
        self.reasons: dict[str, str] = {}
        self.reason_details: dict[str, str] = {}
        self.host_failure = None
        self.launch_after = defaultdict(float)
        self.poll_after = defaultdict(float)
        self.quarantined: set[str] = set()
        self.retiring: dict[str, asyncio.Task] = {}
        self.wake = asyncio.Event()
        self.paint_wake = asyncio.Event()
        self.pending: OrderedDict[int, Live] = OrderedDict()
        self.last_key_paint = defaultdict(lambda: -math.inf)
        self.budget = Budget(8, 8, clock)
        self.tasks = []
        self.holding: set[str] = set()
        self.hold_until = 0.0
        self.closed = False
        self.available_since = 0.0
        self.next_accounting = math.inf
        self.accounting_task = None
        self.paint_count = 0
        self.throttled_count = 0
        self.dropped_late = 0
        self._api_lock = asyncio.Lock()

    async def start(self):
        await asyncio.to_thread(self.registry.scan)
        self.tasks = [asyncio.create_task(self.run(), name="plugin-scheduler"),
                      asyncio.create_task(self.paint_loop(), name="plugin-painter")]
        for task in self.tasks:
            task.add_done_callback(self.task_done)

    def task_done(self, task):
        if self.closed or task.cancelled() or task.exception() is None:
            return
        # A host fault must not leave unsupervised workers alive or crash the Controller.
        logging.getLogger(__name__).error("PLUGIN_HOST_FAILED task=%s", task.get_name())
        self.host_failure = "PLUGIN_HOST_FAILED"
        self.closed = True
        self.hide()
        for other in self.tasks:
            if other is not task:
                other.cancel()
        for session in list(self.sessions.values()):
            self.retire(session)

    def hide(self):
        """Synchronous revocation; no stale reply can survive an await/navigation."""
        self.holding.clear()
        self.instances.clear()
        self.pending.clear()
        self.blocked = []
        self.available_since = self.clock() + .15
        self.wake.set()
        self.paint_wake.set()

    def layout_changing(self):
        self.hide()
        c = self.controller
        if not self.allowed or c.state != "ready" or c.paused or getattr(c, "blanked", False) or c.document["settings"]["brightnessPercent"] <= 0:
            return
        # Briefly keep already-running workers needed by the destination layout.
        # All old activations are revoked; there is no collection before static paint.
        for item in pages_by_id(c.document)[c.page_id]["buttons"]:
            binding, dynamic = item.get("pluginBinding"), item["appearance"].get("dynamic")
            if item["enabled"] and binding and dynamic and dynamic.get("enabled"):
                key = binding["pluginId"] + "@" + binding["pluginVersion"]
                if key in self.sessions:
                    self.holding.add(key)
        self.hold_until = self.clock() + .5

    def reconcile(self):
        c = self.controller
        if self.closed or not self.allowed or c.closing or c.state != "ready" or not c.synchronized or c.paused or getattr(c, "blanked", False) or c.document["settings"]["brightnessPercent"] <= 0:
            self.hide()
            return
        page = pages_by_id(c.document)[c.page_id]
        wanted = {}
        blocked = []
        cap = Capabilities(**c.document["layout"])
        for item in page["buttons"]:
            binding, dynamic = item.get("pluginBinding"), item["appearance"].get("dynamic")
            if not item["enabled"] or not binding or not dynamic or not dynamic.get("enabled") or not dynamic.get("allowedOverrides", ["text", "progress"]):
                continue
            try:
                validate_dynamic(dynamic)
                p = self.registry.select(binding)
                if not self.registry.approved(p):
                    raise SdlError("PLUGIN_APPROVAL_REQUIRED", "Approve this trusted plugin package first.")
                contribution = p.contribution(binding["contributionId"])
                if binding["settingsVersion"] != contribution["settingsVersion"] or binding.get("secretRefs"):
                    raise SdlError("PLUGIN_SETTINGS_INVALID", "Incompatible settings or unsupported secrets.")
                validate_settings(contribution, binding["settings"])
                interval = max(1000, contribution["minRefreshIntervalMs"], binding.get("refreshIntervalMs", contribution["defaultRefreshIntervalMs"])) / 1000
                identifier = binding["instanceId"]
                prior = self.instances.get(identifier)
                if prior and prior.context == c.context and prior.item == item and prior.package.fingerprint == p.fingerprint:
                    wanted[identifier] = prior
                else:
                    target = {"deviceId": c.device_info["deviceId"] if c.device_info else "unknown",
                              "keyIndex": item["keyIndex"], "width": cap.keyWidth, "height": cap.keyHeight,
                              "locale": c.document["settings"]["locale"], "displayFields": dynamic.get("allowedOverrides", ["text", "progress"])}
                    wanted[identifier] = Live(copy.deepcopy(item), p, contribution, c.context, target, interval,
                                              due=max(self.clock(), self.poll_after[p.key]))
            except (SdlError, KeyError, TypeError, ValueError) as exc:
                blocked.append({"buttonId": item["id"], "instanceId": binding.get("instanceId"),
                                "code": getattr(exc, "code", "PLUGIN_SETTINGS_INVALID")})
        if not self.instances and wanted:
            self.available_since = self.clock() + .15
        self.holding.clear()
        self.instances, self.blocked = wanted, blocked
        for index, live in list(self.pending.items()):
            if not self.valid(live):
                del self.pending[index]
        self.wake.set()

    def valid(self, live: Live) -> bool:
        c = self.controller
        return not self.closed and self.instances.get(live.identifier) is live and live.context == c.context and c.state == "ready" and c.synchronized and not c.paused and not getattr(c, "blanked", False) and c.document["settings"]["brightnessPercent"] > 0 and self.registry.approved(live.package)

    def states(self) -> dict:
        return {live.item["id"]: live.painted_state for live in self.instances.values() if live.painted_state and self.valid(live)}

    def launch_task(self, session: Session, coroutine):
        task = asyncio.create_task(coroutine)
        session.busy = task
        def done(finished):
            if session.busy is finished:
                session.busy = None
            if not finished.cancelled():
                exc = finished.exception()
                if exc and self.sessions.get(session.package.key) is session:
                    self.reason_details[session.package.key] = getattr(exc, "message", "Plugin operation failed.")[:512]
                    self.failed(session, getattr(exc, "code", "PLUGIN_FAILED"))
            self.wake.set()
        task.add_done_callback(done)

    def retire(self, session: Session):
        key = session.package.key
        if self.sessions.get(key) is not session:
            return
        del self.sessions[key]
        if session.busy:
            session.busy.cancel()
        async def stop():
            try:
                if session.busy:
                    await asyncio.gather(session.busy, return_exceptions=True)
                if session.worker:
                    await session.worker.close()
                    if not session.worker.reaped:
                        self.reasons[key] = "PLUGIN_PROCESS_NOT_REAPED"
                        self.quarantined.add(key)
            finally:
                self.retiring.pop(key, None)
                self.wake.set()
        self.retiring[key] = asyncio.create_task(stop(), name="plugin-stop")

    def failed(self, session: Session, code: str):
        key, now = session.package.key, self.clock()
        self.reasons[key] = code
        self.failures[key].append(now)
        recent = [t for t in self.failures[key] if now - t < 60]
        if len(recent) >= 4 or code in {"RESOURCE_LIMIT_UNAVAILABLE", "PLUGIN_APPROVAL_STALE", "PLUGIN_OUTPUT_LIMIT", "PLUGIN_LOG_FLOOD", "PLUGIN_RESOURCE_OVERRUN", "PLUGIN_DISPLAY_INVALID"}:
            self.quarantined.add(key)
        self.launch_after[key] = now + min(4, 2 ** max(0, len(recent) - 1))
        for live in self.instances.values():
            if live.package.key == key:
                self.unavailable(live)
        self.retire(session)
        self.controller.emit("plugins.changed", {"pluginId": session.package.manifest["id"], "code": code})

    async def launch(self, session: Session):
        package = session.package
        # Shield data-only staging; cleanup even if navigation cancels an ongoing copy.
        staging = asyncio.create_task(asyncio.to_thread(self.registry.snapshot, package))
        try:
            snapshot = await asyncio.shield(staging)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                snapshot = await staging
                await asyncio.to_thread(shutil.rmtree, snapshot, True)
            raise
        if self.sessions.get(package.key) is not session:
            await asyncio.to_thread(shutil.rmtree, snapshot, True)
            return
        session.worker = await self.launcher.launch(package, snapshot, self.wake.set)
        try:
            result = await session.worker.wire.request("plugin.initialize", {
                "apiVersion": "1.1", "hostVersion": "0.2.0", "pluginId": package.manifest["id"],
                "pluginVersion": package.manifest["version"], "fingerprint": package.fingerprint,
                "locale": self.controller.document["settings"]["locale"],
                "limits": {"minRefreshIntervalMs": 1000, "maxInstances": 128, "maxMessageBytes": 262144}}, 5)
            required = {"display", *package.manifest["protocolFeatures"]}
            if not isinstance(result, dict) or result.get("apiVersion") != "1.1" or result.get("pluginVersion") != package.manifest["version"] or not isinstance(result.get("capabilities"), list) or not required.issubset(result["capabilities"]):
                raise SdlError("PLUGIN_PROTOCOL_INVALID", "Plugin initialization capabilities do not match its manifest.")
            session.phase = "ready"
            session.started = self.clock()
            self.next_accounting = min(self.next_accounting, self.clock() + 5)
        except BaseException:
            await asyncio.shield(session.worker.close())
            raise

    async def sync(self, session: Session, wanted: dict[str, Live]):
        wire = session.worker.wire
        for identifier, live in list(session.members.items()):
            if wanted.get(identifier) is live:
                continue
            await wire.request("instance.visibility", {**live.instance_context(), "visible": False,
                               "activationId": None, "target": live.target})
            await wire.request("instance.destroy", {**live.instance_context(), "reason": "hidden-or-reconfigured"})
            del session.members[identifier]
        for identifier, live in wanted.items():
            if session.members.get(identifier) is live or not self.valid(live):
                continue
            binding = live.item["pluginBinding"]
            result = await wire.request("instance.create", {**live.instance_context(),
                "contributionId": binding["contributionId"], "settingsVersion": binding["settingsVersion"],
                "settings": binding["settings"], "secretNames": [], "target": live.target,
                "effectiveRefreshIntervalMs": round(live.interval * 1000)}, 5)
            if result != {"ready": True}:
                raise SdlError("PLUGIN_PROTOCOL_INVALID", "Invalid instance creation acknowledgement.")
            session.members[identifier] = live
            if self.valid(live):
                response = await wire.request("instance.visibility", {**live.instance_context(), "visible": True,
                                     "activationId": live.activation, "target": live.target})
                if response != {"ok": True}:
                    raise SdlError("PLUGIN_PROTOCOL_INVALID", "Invalid visibility acknowledgement.")

    def queue(self, live: Live):
        if not self.valid(live):
            return
        signature = digest(visual(live.item, live.state, live.target["width"]))
        if signature == live.visual_key:
            live.skipped_identical += 1
            return
        live.visual_key = signature
        live.visual_version += 1
        # Replacing does not move its queue position: a fast key cannot starve peers.
        self.pending[live.item["keyIndex"]] = live
        self.paint_wake.set()

    def unavailable(self, live: Live):
        if not self.valid(live):
            return
        live.state = {"value": None, "unit": "", "label": "", "status": "unavailable", "message": None,
                      "image": None, "progress": None, "_lastGood": live.good}
        self.queue(live)

    async def accept(self, session: Session, live: Live, result: dict, start: float):
        if not self.valid(live):
            self.dropped_late += 1
            return
        expected = live.wire_context()
        if not isinstance(result, dict) or any(result.get(k) != v for k, v in expected.items()):
            live.late += 1
            self.dropped_late += 1
            if live.late >= 3:
                raise SdlError("PLUGIN_PROTOCOL_INVALID", "Repeated stale or invalid instance responses.")
            return
        if "error" in result:
            live.errors += 1
            live.due = self.clock() + max(live.interval, min(60, 2 ** min(live.errors, 6)))
            self.unavailable(live)
            return
        if set(result) != {*expected, "sequence", "state"} or type(result["sequence"]) is not int or result["sequence"] <= live.sequence or result["sequence"] > 2**53:
            live.late += 1
            self.dropped_late += 1
            if live.late >= 3:
                raise SdlError("PLUGIN_PROTOCOL_INVALID", "Repeated stale or invalid instance responses.")
            return
        live.late = 0
        state = copy.deepcopy(validate_state(result["state"], set(session.package.manifest["assets"])))
        if state["image"] and state["image"]["kind"] == "asset":
            asset_path = session.package.manifest["assets"][state["image"]["assetKey"]]
            raw = await asyncio.to_thread(read_at, session.worker.snapshot, asset_path, 131072)
            if not self.valid(live):
                self.dropped_late += 1
                return
            state["image"] = {"kind": "png", "dataBase64": base64.b64encode(raw).decode("ascii")}
        live.sequence = result["sequence"]
        live.refreshes += 1
        live.last_duration_ms = (self.clock() - start) * 1000
        if state["status"] in {"ok", "warning"}:
            live.errors = 0
            live.good = state
            live.last_measurement = self.clock()
            live.stale = False
        else:
            live.errors += 1
            state["_lastGood"] = live.good
            live.due = max(live.due, self.clock() + max(live.interval, min(60, 2 ** min(live.errors, 6))))
        live.state = state
        self.queue(live)

    async def refresh(self, session: Session, due: list[Live]):
        due = [live for live in due if self.valid(live)]
        if not due:
            return
        start = self.clock()
        interval = min(live.interval for live in self.instances.values() if live.package.key == session.package.key)
        self.poll_after[session.package.key] = start + interval
        for live in due:
            live.due = start + live.interval
        deadline = min(5000, max(1000, max(round(live.interval * 1000) for live in due)))
        if "refreshMany" in session.package.manifest["protocolFeatures"]:
            reply = await session.worker.wire.request("instance.refreshMany", {"instances": [l.wire_context() for l in due],
                                                      "deadlineMs": deadline}, deadline / 1000)
            if not isinstance(reply, dict) or set(reply) != {"results"} or not isinstance(reply["results"], list) or len(reply["results"]) != len(due):
                raise SdlError("PLUGIN_PROTOCOL_INVALID", "Invalid refresh batch.")
            rows = reply["results"]
        else:
            rows = []
            for live in due:
                if not self.valid(live):
                    rows.append(live.wire_context())
                    continue
                rows.append(await session.worker.wire.request("instance.refresh", {**live.wire_context(), "deadlineMs": deadline}, deadline / 1000))
        completed = self.clock()
        self.poll_after[session.package.key] = next_due(start, completed, interval)
        # Context IDs, not array position, identify results; duplicates never update twice.
        by_id = {}
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("instanceId"), str) or row["instanceId"] in by_id:
                raise SdlError("PLUGIN_PROTOCOL_INVALID", "Duplicate or malformed refresh result.")
            by_id[row["instanceId"]] = row
        for live in due:
            live.due = next_due(start, completed, live.interval)
            await self.accept(session, live, by_id.get(live.identifier, {}), start)

    async def accounting(self):
        now = self.clock()
        for session in list(self.sessions.values()):
            if not session.worker or not session.worker.enforced:
                continue
            data = await asyncio.to_thread(session.worker.accounting)
            if self.sessions.get(session.package.key) is not session or not data:
                continue
            prior = session.stats
            data["sampledAt"] = now
            if prior and now > prior["sampledAt"]:
                share = (data["cpuUsec"] - prior["cpuUsec"]) / ((now - prior["sampledAt"]) * 1000000)
                data["cpuPercentOneLogicalCpu"] = round(100 * share, 3)
                high = share > .045 or data["memoryBytes"] > 48 * 1024**2
                session.resource_strikes = session.resource_strikes + 1 if high else 0
                if session.resource_strikes >= 3:
                    self.failed(session, "PLUGIN_RESOURCE_OVERRUN")
            session.stats = data
        self.wake.set()

    def stale_after(self, live: Live) -> float:
        fair = len(self.instances) / 8
        return max(10, live.item["appearance"]["dynamic"].get("staleAfterMs", 10000) / 1000, 3 * max(live.interval, fair))

    async def run(self):
        try:
            while not self.closed:
                self.wake.clear()
                c = self.controller
                if hasattr(c, "check_resume"):
                    c.check_resume()
                now = self.clock()
                wanted = defaultdict(dict)
                for identifier, live in list(self.instances.items()):
                    if self.valid(live):
                        wanted[live.package.key][identifier] = live
                deadlines = []
                for key, session in list(self.sessions.items()):
                    if key in self.holding and now < self.hold_until:
                        if not session.busy and session.phase == "ready" and session.members:
                            self.launch_task(session, self.sync(session, {}))
                        deadlines.append(self.hold_until)
                        continue
                    if key not in wanted or not self.registry.approved(session.package):
                        self.retire(session)
                    elif session.worker and session.worker.wire and session.worker.wire.fatal:
                        self.failed(session, session.worker.wire.fatal.code)
                for key, members in wanted.items():
                    if key in self.quarantined or key in self.retiring:
                        continue
                    session = self.sessions.get(key)
                    if session is None:
                        due = max(self.available_since, self.launch_after[key])
                        if due > now:
                            deadlines.append(due)
                            continue
                        if len(self.sessions) + len(self.retiring) >= 8:
                            self.reasons[key] = "PLUGIN_WORKER_LIMIT"
                            continue
                        session = Session(next(iter(members.values())).package)
                        self.sessions[key] = session
                        self.launch_after[key] = now + 1
                        self.launch_task(session, self.launch(session))
                        continue
                    if session.busy or session.phase != "ready":
                        continue
                    if session.members != members:
                        self.launch_task(session, self.sync(session, dict(members)))
                        continue
                    if now - session.started >= 60:
                        self.failures[key].clear()
                    due = [live for live in members.values() if live.due <= now] if now >= self.poll_after[key] else []
                    if due:
                        self.launch_task(session, self.refresh(session, due))
                    else:
                        deadlines.extend(max(live.due, self.poll_after[key]) for live in members.values())
                for live in list(self.instances.values()):
                    if live.last_measurement is not None and not live.stale:
                        stale_at = live.last_measurement + self.stale_after(live)
                        if now >= stale_at:
                            live.stale = True
                            self.unavailable(live)
                        else:
                            deadlines.append(stale_at)
                if self.sessions:
                    if now >= self.next_accounting and (self.accounting_task is None or self.accounting_task.done()):
                        self.next_accounting = now + 5
                        self.accounting_task = asyncio.create_task(self.accounting())
                    deadlines.append(self.next_accounting)
                else:
                    self.next_accounting = math.inf
                finite = [t for t in deadlines if math.isfinite(t)]
                timeout = max(.001, min(finite) - self.clock()) if finite else None
                try:
                    if timeout is None:
                        await self.wake.wait()
                    else:
                        await asyncio.wait_for(self.wake.wait(), timeout)
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    async def paint_loop(self):
        try:
            while not self.closed:
                await self.paint_wake.wait()
                self.paint_wake.clear()
                while self.pending and not self.closed:
                    index, live = next(iter(self.pending.items()))
                    if not self.valid(live):
                        self.pending.pop(index, None)
                        continue
                    now = self.clock()
                    delay = max(0, self.last_key_paint[index] + 1 - now)
                    if delay or not self.budget.take():
                        self.throttled_count += 1
                        # Rotate a key-specific delay so another key can use this slot.
                        self.pending.move_to_end(index)
                        await asyncio.sleep(min(max(delay, .125), .125))
                        continue
                    self.pending.pop(index, None)
                    version = live.visual_version
                    c = self.controller
                    try:
                        cap = Capabilities(**c.document["layout"])
                        images = await asyncio.get_running_loop().run_in_executor(c.render_pool,
                            lambda l=live, state=copy.deepcopy(live.state), overlay=c.overlay.get(live.item["id"]):
                                c.renderer.render(l.item, cap, locale=l.target["locale"], overlay=overlay, dynamic=state))
                        if not self.valid(live) or live.visual_version != version:
                            self.dropped_late += 1
                            continue
                        value = hashlib.sha256(images.tobytes()).digest()
                        async with c.io_lock:
                            if not self.valid(live) or live.visual_version != version or c.redraw.is_set():
                                if self.valid(live):
                                    self.pending[index] = live
                                continue
                            if c.sent.get(index) != value:
                                await c.io_call(c.adapter.write, index, images)
                                c.sent[index] = value
                                self.paint_count += 1
                                self.last_key_paint[index] = self.clock()
                            if self.valid(live):
                                live.last_paint = self.clock()
                                live.painted_state = copy.deepcopy(live.state)
                    except asyncio.CancelledError:
                        raise
                    except SdlError as exc:
                        session = self.sessions.get(live.package.key)
                        if session:
                            live.good = None
                            self.failed(session, exc.code)
                    except Exception:
                        c.state = "error"
                        c.invalidated(disconnect=True)
                        c.retry.set()
        except asyncio.CancelledError:
            pass

    async def api(self, method: str, params: dict):
        if method == "plugins.list":
            return self.status(include_packages=True)
        async with self._api_lock:
            if method == "plugins.rescan":
                await asyncio.to_thread(self.registry.scan)
            elif method == "plugins.approve":
                await asyncio.to_thread(self.registry.approve, params)
            elif method == "plugins.setEnabled":
                await asyncio.to_thread(self.registry.enabled, params)
            elif method == "plugins.restart":
                p = self.registry.select(params)
                if not self.registry.approved(p):
                    raise SdlError("PLUGIN_APPROVAL_REQUIRED", "The plugin is not approved/enabled.")
                self.quarantined.discard(p.key)
                self.failures[p.key].clear()
                self.reasons.pop(p.key, None)
                # Explicit retry still respects cadence and normal start churn limits.
                if p.key in self.sessions:
                    self.retire(self.sessions[p.key])
            else:
                raise SdlError("METHOD_NOT_FOUND", "Unknown plugin method.")
            self.reconcile()
            self.controller.request_redraw()
            self.controller.emit("plugins.changed", {"method": method})
            return self.status(include_packages=True)

    async def invoke(self, item: dict):
        raise SdlError("PLUGIN_CAPABILITY_UNAVAILABLE", "Plugin commands are reserved; display plugins can coexist with an independent application/script action.")

    def status(self, include_packages=False):
        now = self.clock()
        result = {"supported": True, "apiVersion": "1.1", "display": True, "invoke": False, "push": False,
                  "mode": self.mode, "allowed": self.allowed, "hostFailure": self.host_failure, "resourceLimitsEnforced": bool(self.sessions) and all(s.worker and s.worker.enforced for s in self.sessions.values()),
                  "resourceRequirement": "systemd-cgroup-v2" if self.mode == "systemd" else "DEVELOPMENT_UNENFORCED",
                  "activeWorkers": len(self.sessions), "stoppingWorkers": len(self.retiring), "pendingPaints": len(self.pending),
                  "dynamicWrites": self.paint_count, "throttledWrites": self.throttled_count, "droppedLate": self.dropped_late,
                  "dynamicWritesPerSecond": 8, "minimumRefreshIntervalMs": 1000,
                  "instances": [{"instanceId": l.identifier, "buttonId": l.item["id"], "keyIndex": l.item["keyIndex"],
                      "pluginId": l.package.manifest["id"], "pluginVersion": l.package.manifest["version"],
                      "visible": True, "status": l.state["status"] if l.state else "loading", "value": l.state["value"] if l.state else None,
                      "requestedIntervalMs": l.item["pluginBinding"]["refreshIntervalMs"], "effectiveIntervalMs": round(l.interval * 1000),
                      "fairPaintIntervalMs": round(max(1, len(self.instances) / 8) * 1000), "staleAfterMs": round(self.stale_after(l) * 1000),
                      "measurementAgeMs": round((now - l.last_measurement) * 1000) if l.last_measurement is not None else None,
                      "paintAgeMs": round((now - l.last_paint) * 1000) if l.last_paint is not None else None,
                      "nextDueMs": max(0, round((l.due - now) * 1000)), "refreshCount": l.refreshes,
                      "lastRefreshDurationMs": round(l.last_duration_ms, 3), "skippedIdentical": l.skipped_identical, "stale": l.stale}
                      for l in self.instances.values()],
                  "workers": [{"package": k, "phase": s.phase, "pid": s.worker.pid if s.worker else None,
                               "resourceLimitsEnforced": bool(s.worker and s.worker.enforced), "accounting": s.stats}
                              for k, s in self.sessions.items()],
                  "blocked": self.blocked, "errors": self.registry.errors,
                  "failures": [{"package": k, "code": code, "quarantined": k in self.quarantined,
                                "restartCount": len(self.failures[k]), "message": self.reason_details.get(k)} for k, code in self.reasons.items()]}
        if include_packages:
            result["packages"] = self.registry.describe()
        return result

    async def close(self):
        self.closed = True
        self.hide()
        for task in self.tasks:
            task.cancel()
        if self.accounting_task:
            self.accounting_task.cancel()
        await asyncio.gather(*self.tasks, *([self.accounting_task] if self.accounting_task else []), return_exceptions=True)
        for session in list(self.sessions.values()):
            self.retire(session)
        if self.retiring:
            await asyncio.gather(*list(self.retiring.values()), return_exceptions=True)
