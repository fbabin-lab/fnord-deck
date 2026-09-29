"""Async orchestration: applied state, action registry, rendering and device lifecycle."""
import asyncio
import base64
import contextlib
import hashlib
import importlib.metadata
import io
import logging
import os
import platform
import secrets
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

from PIL import Image, features

from sdl_core.assets import AssetStore
from .plugins.host import PluginHost
from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, digest, dumps, read_json
from sdl_core.limits import CHUNK_BYTES, FRAME_BYTES, UPLOAD_SECONDS
from sdl_core.model import (Capabilities, all_buttons, execution_fingerprints, execution_summary,
                            pages_by_id, require_valid, uuid, validate)
from sdl_core.paths import Paths, ProcessLock
from sdl_core.render import Renderer, font_path

from .device import ElgatoAdapter, SimulatorAdapter
from .execution import ActionExecutor, Launcher
from .input import InputContext, InputGate, InputRecord, Mailbox
from .ipc import LocalServer
from .repository import ConfigurationRepository, ExecutionRepository
from .session import SessionContextProvider

LOG = logging.getLogger("sdl")
VERSION = "0.2.0"


class Controller:
    def __init__(self, paths: Paths, *, simulator: bool = False, allow_execution: bool = False, adapter=None, allow_plugins: bool = False, plugin_mode: str = "systemd", plugin_launcher=None) -> None:
        self.paths = paths
        self.paths.prepare()
        self.lock = ProcessLock(self.paths.runtime / "controller.lock")
        self.simulator = simulator
        self.repository = ConfigurationRepository(paths.config)
        self.assets = AssetStore(paths.data / "assets")
        self.renderer = Renderer(self.assets)
        self.session = SessionContextProvider()
        self.execution = ActionExecutor(ExecutionRepository(paths.state / "executions.json"),
                                        Launcher(paths.runtime), self.session, self.execution_changed,
                                        enabled=not simulator or allow_execution)
        self.adapter = adapter or (SimulatorAdapter() if simulator else ElgatoAdapter())
        self.runtime_epoch = str(uuid4())
        self.sequence = 0
        self.device_epoch = 0
        self.generation = 0
        self.page_id = self.document["rootPageId"]
        self.gate = InputGate(Capabilities(**self.document["layout"]).key_count)
        self.state = "disconnected"
        self.device_info: dict | None = None
        self.device_error: dict | None = None
        self.last_error: dict | None = None
        self.selected_serial = self.document["target"]["serialNumber"]
        pin = self.paths.state / "selected-device.json"
        if self.selected_serial is None and pin.exists():
            try:
                saved = read_json(pin)
                self.selected_serial = saved["serialNumber"]
                if not isinstance(self.selected_serial, str):
                    self.selected_serial = None
            except (OSError, SdlError, KeyError):
                self.selected_serial = None
        self.paused = False
        self.blanked = False
        self._sleep_offset = self.sleep_offset()
        self.synchronized = False
        self.redraw = asyncio.Event()
        self.retry = asyncio.Event()
        self.io_lock = asyncio.Lock()
        self.config_lock = asyncio.Lock()
        self.io_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdl-usb")
        self.render_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sdl-render")
        self.sent: dict[int, bytes] = {}
        self.overlay: dict[str, str] = {}
        self.review_tokens: dict[str, tuple[str, float]] = {}
        self.server = LocalServer(paths.socket, self.dispatch)
        self.mailbox: Mailbox | None = None
        self.tasks: set[asyncio.Task] = set()
        self.closing = False
        self.extensions = PluginHost(self, mode=plugin_mode, allowed=not simulator or allow_plugins, launcher=plugin_launcher)
        self.action_handlers = {
            "core.none": self._none, "core.navigate": self._navigate_action,
            "core.home": self._home, "core.execute": self._execute,
            "core.plugin.invoke": self.extensions.invoke,
        }

    @property
    def document(self) -> dict:
        return self.repository.document

    @property
    def context(self) -> InputContext:
        return InputContext(self.device_epoch, self.generation, self.repository.revision, self.page_id)

    async def io_call(self, func, *args):
        return await asyncio.get_running_loop().run_in_executor(self.io_pool, func, *args)

    async def render_call(self, *args):
        return await asyncio.get_running_loop().run_in_executor(self.render_pool, self.renderer.page, *args)

    def spawn(self, coroutine, name: str) -> asyncio.Task:
        task = asyncio.create_task(coroutine, name=name)
        self.tasks.add(task)
        def done(finished):
            self.tasks.discard(finished)
            if not finished.cancelled() and finished.exception():
                LOG.error("BACKGROUND_OPERATION_FAILED task=%s", name)
        task.add_done_callback(done)
        return task

    async def start(self) -> None:
        try:
            # Startup validation never enables actions from damaged data.
            result = validate(self.document, self.assets.exists)
            try:
                for _, item in all_buttons(self.document):
                    asset_id = item["appearance"]["iconAssetId"]
                    if asset_id:
                        await asyncio.to_thread(self.assets.verify, asset_id)
            except SdlError:
                result["errors"].append({"code": "ASSET_INVALID"})
            if result["errors"]:
                from sdl_core.model import empty_configuration
                self.repository.recovery = "Applied configuration references invalid/missing assets. Preserved files; using an empty safe configuration."
                self.repository.document = empty_configuration()
                self.repository.revision = 0
                self.repository.approved = set()
                self.page_id = self.document["rootPageId"]
            self.gate.reset(self.context, disconnect=True)
            self.mailbox = Mailbox(asyncio.get_running_loop(), self.input_record, self.input_overflow)
            await self.server.start()
            await self.extensions.start()
            self.spawn(self.device_loop(), "device-lifecycle")
            self.spawn(self.render_loop(), "render")
            self.spawn(self.maintenance_loop(), "maintenance")
            LOG.info("CONTROLLER_STARTED simulator=%s", self.simulator)
        except BaseException:
            self.lock.close()
            self.lock = None
            raise

    def emit(self, event_type: str, payload: dict) -> None:
        self.sequence += 1
        self.server.event({"runtimeEpoch": self.runtime_epoch, "eventSequence": self.sequence,
                           "revision": self.repository.revision, "type": event_type, "payload": payload})

    def invalidated(self, *, disconnect: bool = False) -> None:
        self.generation += 1
        self.gate.reset(self.context, disconnect=disconnect)
        self.synchronized = False
        if disconnect:
            self.extensions.hide()
        else:
            self.extensions.layout_changing()
        self.redraw.set()

    def request_redraw(self) -> None:
        self.redraw.set()

    def input_overflow(self) -> None:
        self.gate.reset(self.context, disconnect=True)
        self.last_error = {"code": "INPUT_OVERFLOW", "message": "Input overflow: pending clicks cancelled; waiting for a released baseline."}
        self.request_redraw()
        self.emit("input.resynchronized", self.last_error)

    @staticmethod
    def sleep_offset() -> float:
        return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()

    def check_resume(self) -> bool:
        offset = self.sleep_offset()
        resumed = offset - self._sleep_offset > .5
        self._sleep_offset = offset
        if resumed:
            self.invalidated(disconnect=True)
            self.emit("runtime.resumed", {"freshPluginBaselines": True})
        return resumed

    def input_record(self, record: InputRecord, *, baseline_only: bool = False) -> None:
        if self.check_resume():
            return
        if self.closing:
            return
        page = pages_by_id(self.document)[self.page_id]
        identities = {b["keyIndex"]: b["id"] for b in page["buttons"] if b["enabled"]}
        if page["parentPageId"] is not None:
            identities[0] = "back:" + self.page_id
        for key, identity in self.gate.accept(record, identities, baseline_only=baseline_only):
            self.spawn(self.activate(key, identity, record.context), "button-activation")

    async def activate(self, key: int, identity: str, context: InputContext) -> None:
        if self.paused or self.blanked or context != self.context or not self.gate.enabled or self.closing:
            return
        page = pages_by_id(self.document)[self.page_id]
        if identity == "back:" + self.page_id and key == 0 and page["parentPageId"]:
            self.navigate(page["parentPageId"])
            return
        item = next((b for b in page["buttons"] if b["id"] == identity and b["keyIndex"] == key and b["enabled"]), None)
        if item is None:
            return
        self.emit("button.activated", {"buttonId": identity, "keyIndex": key, "pageId": self.page_id})
        try:
            await self.action_handlers[item["action"]["type"]](item)
        except SdlError as exc:
            self.last_error = {"code": exc.code, "message": exc.message, "buttonId": identity}
            self.overlay[identity] = "failed"
            self.request_redraw()
            self.emit("action.error", self.last_error)

    async def _none(self, item: dict) -> None:
        pass

    async def _navigate_action(self, item: dict) -> None:
        self.navigate(item["action"]["pageId"])

    async def _home(self, item: dict) -> None:
        self.navigate(self.document["rootPageId"])

    async def _execute(self, item: dict) -> None:
        if digest({"buttonId": item["id"], "action": item["action"]}) not in self.repository.approved:
            raise SdlError("REVIEW_REQUIRED", "This executable action has not been reviewed.")
        await self.execution.start(item, self.repository.revision, self.document["configurationId"], origin="physical")

    def navigate(self, page_id: str) -> dict:
        if page_id not in pages_by_id(self.document):
            raise SdlError("PAGE_UNAVAILABLE", "Page is not part of the applied configuration.")
        self.page_id = page_id
        self.invalidated()
        self.emit("runtime.navigated", {"pageId": page_id, "pageGeneration": self.generation})
        return {"pageId": page_id, "pageGeneration": self.generation}

    def execution_changed(self, record: dict) -> None:
        if record["revision"] == self.repository.revision:
            self.overlay[record["buttonId"]] = record["state"]
            self.request_redraw()
        self.emit("execution.changed", {k: v for k, v in record.items() if k not in ("stdout", "stderr")})

    async def device_loop(self) -> None:
        backoff = 0.5
        while not self.closing:
            try:
                self.check_resume()
                async with self.io_lock:
                    if self.state == "ready" and not self.retry.is_set():
                        if not await self.io_call(self.adapter.connected):
                            raise SdlError("DEVICE_UNAVAILABLE", "USB device disconnected.")
                    else:
                        self.retry.clear()
                        await self.io_call(self.adapter.close)
                        self.device_epoch += 1
                        self.invalidated(disconnect=True)
                        self.state = "connecting"
                        self.emit("device.changed", {"state": self.state})
                        epoch = self.device_epoch
                        def input_callback(states, connection_epoch=epoch):
                            current = self.context
                            captured = InputContext(connection_epoch, current.generation, current.revision, current.page_id)
                            self.mailbox.push(InputRecord(captured, states, time.monotonic()))
                        info = await self.io_call(self.adapter.connect, self.selected_serial, input_callback)
                        if self.adapter.capabilities.json() != self.document["layout"]:
                            raise SdlError("LAYOUT_MISMATCH", "Configured layout differs from the connected device.")
                        if not self.simulator and self.selected_serial is None:
                            self.selected_serial = info["serialNumber"]
                            atomic_write(self.paths.state / "selected-device.json", dumps({"serialNumber": self.selected_serial}))
                        self.device_info = info
                        self.device_error = None
                        self.state = "ready"
                        self.sent.clear()
                        self.request_redraw()
                        self.emit("device.changed", {"state": self.state, "device": info})
                        backoff = 0.5
                try:
                    await asyncio.wait_for(self.retry.wait(), 1)
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                safe = exc if isinstance(exc, SdlError) else SdlError("DEVICE_IO_ERROR", "Device operation failed.")
                self.state = {"DEVICE_UNAVAILABLE": "disconnected", "DEVICE_PERMISSION_DENIED": "permissionDenied",
                              "DEVICE_IN_USE": "inUse", "LAYOUT_MISMATCH": "layoutMismatch"}.get(safe.code, "error")
                self.device_error = {"code": safe.code, "message": safe.message}
                self.device_epoch += 1
                self.invalidated(disconnect=True)
                async with self.io_lock:
                    with contextlib.suppress(Exception):
                        await self.io_call(self.adapter.close)
                self.emit("device.changed", {"state": self.state, "error": self.device_error})
                self.retry.clear()
                try:
                    await asyncio.wait_for(self.retry.wait(), backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 5)

    async def render_loop(self) -> None:
        last_generation = -1
        last_paint = 0.0
        while not self.closing:
            await self.redraw.wait()
            self.redraw.clear()
            if self.state != "ready":
                continue
            # At most four background/status repaints per second. Navigation bypasses this wait.
            while self.generation == last_generation and time.monotonic() - last_paint < 0.25:
                delay = 0.25 - (time.monotonic() - last_paint)
                if delay <= 0:
                    break
                try:
                    await asyncio.wait_for(self.redraw.wait(), delay)
                    self.redraw.clear()
                except TimeoutError:
                    break
            context = self.context
            # Only layout transitions invalidate input; value/status-only paints do not.
            layout_change = self.generation != last_generation or not self.synchronized
            if layout_change:
                self.gate.reset(context)
            try:
                images = await self.render_call(self.document, self.page_id, dict(self.overlay), self.extensions.states())
                if self.blanked:
                    images = [Image.new("RGB", image.size, "black") for image in images]
                async with self.io_lock:
                    if context != self.context or self.state != "ready":
                        continue
                    await self.io_call(self.adapter.brightness, self.document["settings"]["brightnessPercent"])
                    for index, image in enumerate(images):
                        if context != self.context or self.state != "ready":
                            break
                        value = hashlib.sha256(image.tobytes()).digest()
                        if self.sent.get(index) != value:
                            await self.io_call(self.adapter.write, index, image)
                            self.sent[index] = value
                    else:
                        if context == self.context:
                            self.synchronized = True
                            last_generation = self.generation
                            last_paint = time.monotonic()
                            if not self.paused and not self.blanked:
                                self.gate.enable()
                            self.extensions.reconcile()
                            self.emit("device.synchronized", {"pageId": self.page_id, "pageGeneration": self.generation})
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.synchronized = False
                self.gate.reset(self.context)
                safe = exc if isinstance(exc, SdlError) else SdlError("RENDER_FAILED", "Rendering or USB write failed.")
                self.last_error = {"code": safe.code, "message": safe.message}
                self.emit("render.error", self.last_error)
                self.state = "error"
                self.retry.set()
            # Metric paints use their own fair queue and never trigger a page repaint.
            await asyncio.sleep(0.05)

    async def maintenance_loop(self) -> None:
        while not self.closing:
            await asyncio.sleep(5)
            now = time.monotonic()
            self.review_tokens = {k: v for k, v in self.review_tokens.items() if v[1] > now}
            for client in list(self.server.clients):
                for identifier, upload in list(client.uploads.items()):
                    if upload["expires"] < now:
                        upload["path"].unlink(missing_ok=True)
                        del client.uploads[identifier]

    def snapshot(self) -> dict:
        return {"runtimeVersion": VERSION, "runtimeEpoch": self.runtime_epoch, "eventSequence": self.sequence,
                "revision": self.repository.revision, "configurationId": self.document["configurationId"],
                "currentPageId": self.page_id, "pageGeneration": self.generation, "paused": self.paused, "blanked": self.blanked,
                "device": {"state": self.state, "descriptor": self.device_info, "error": self.device_error,
                           "selectedSerial": self.selected_serial, "deviceSynchronized": self.synchronized},
                "simulator": self.simulator, "executionEnabled": self.execution.enabled,
                "plugins": self.extensions.status(), "lastError": self.last_error,
                "recovery": self.repository.recovery, "executionRecovery": self.execution.history.recovery,
                "recentExecutions": self.execution.history.summaries(limit=10)["executions"]}

    def check_revision(self, expected: int) -> None:
        if expected != self.repository.revision:
            raise SdlError("REVISION_CONFLICT", "The active configuration changed; refresh the snapshot.")

    async def apply(self, params: dict) -> dict:
        async with self.config_lock:
            document = params["document"]
            operation = uuid(params["operationId"])
            payload_hash = digest({"document": document, "expectedRevision": params["expectedRevision"]})
            existing = self.repository.lookup(operation, payload_hash)
            if existing is not None:
                return {**existing, "deduplicated": True}
            self.check_revision(params["expectedRevision"])
            warnings = require_valid(document, self.assets.exists)
            fingerprints = execution_fingerprints(document)
            needs_review = not fingerprints.issubset(self.repository.approved)
            if needs_review:
                review = self.review_tokens.get(params.get("reviewToken", ""))
                if params.get("confirmed") is not True or not review or review[0] != digest(document) or review[1] < time.monotonic():
                    raise SdlError("REVIEW_REQUIRED", "Review and explicitly confirm new or changed executable actions before Apply.")
            assets = {b["appearance"]["iconAssetId"] for _, b in all_buttons(document) if b["appearance"]["iconAssetId"]}
            for asset_id in assets:
                await asyncio.to_thread(self.assets.verify, asset_id)
            page_id = self.page_id if self.page_id in pages_by_id(document) else document["rootPageId"]
            await self.render_call(document, page_id)
            old_target = self.document["target"]
            result = self.repository.commit(document, params["expectedRevision"], operation, payload_hash, fingerprints, warnings)
            self.page_id = page_id
            self.overlay.clear()
            self.invalidated()
            if document["target"] != old_target:
                self.selected_serial = document["target"]["serialNumber"]
                if self.selected_serial:
                    atomic_write(self.paths.state / "selected-device.json", dumps({"serialNumber": self.selected_serial}))
                else:
                    (self.paths.state / "selected-device.json").unlink(missing_ok=True)
                self.retry.set()
            self.emit("configuration.applied", result)
            return result

    async def dispatch(self, method: str, params: dict, client) -> dict:
        if method == "system.hello":
            if params["apiMajor"] != 1:
                raise SdlError("UNSUPPORTED_VERSION", "This Controller requires local API major version 1.")
            client.hello = True
            return {"apiMajor": 1, "apiMinor": 1, "runtimeVersion": VERSION, "maxFrameBytes": FRAME_BYTES,
                    "capabilities": {"plugins": True, "pluginApiVersion": "1.1", "pluginDisplay": True, "pluginInvoke": False, "pluginPush": False, "secrets": False, "simulator": self.simulator,
                                     "assetImport": True, "events": True, "preview": True}}
        if method == "system.snapshot":
            return self.snapshot()
        if method == "system.diagnostics":
            return {**diagnostics(self.session), "pluginHost": self.extensions.status()}
        if method == "events.subscribe":
            client.pending_events = set(params.get("types", []))
            return {"subscriptionId": str(uuid4()), "runtimeEpoch": self.runtime_epoch, "eventSequence": self.sequence}
        if method == "configuration.get":
            record = self.repository.get(params.get("revision"))
            return {**record, "warnings": validate(record["document"], self.assets.exists)["warnings"]}
        if method == "configuration.validate":
            return validate(params["document"], self.assets.exists)
        if method == "configuration.review":
            require_valid(params["document"], self.assets.exists)
            if len(self.review_tokens) >= 64:
                self.review_tokens.pop(next(iter(self.review_tokens)))
            token = secrets.token_urlsafe(32)
            value = digest(params["document"])
            self.review_tokens[token] = (value, time.monotonic() + 300)
            return {"contentHash": value, "reviewToken": token, "expiresInSeconds": 300,
                    "actions": execution_summary(params["document"])}
        if method == "configuration.apply":
            return await self.apply(params)
        if method == "configuration.history":
            return self.repository.history(params.get("cursor", 0), params.get("limit", 20))
        if method.startswith("asset."):
            return await self.asset_method(method, params, client)
        if method == "device.list":
            async with self.io_lock:
                return {"devices": await self.io_call(self.adapter.enumerate)}
        if method == "device.retry":
            if params.get("deviceId") and self.device_info and params["deviceId"] != self.device_info["deviceId"]:
                raise SdlError("INVALID_PARAMS", "Device selection is changed through target.serialNumber and Apply.")
            self.retry.set()
            return {"ok": True}
        if method == "runtime.navigate":
            self.check_revision(params["expectedRevision"])
            return self.navigate(params["pageId"])
        if method == "runtime.blank":
            self.blanked = params["blanked"]
            self.invalidated()
            return {"blanked": self.blanked}
        if method == "runtime.pause":
            self.paused = params["paused"]
            self.invalidated()
            self.emit("runtime.paused", {"paused": self.paused})
            return {"paused": self.paused}
        if method == "session.update":
            return self.session.update(params["variables"])
        if method == "execution.test":
            operation = uuid(params["operationId"])
            payload_hash = digest({"buttonId": params["buttonId"], "expectedRevision": params["expectedRevision"]})
            prior = self.execution.history.find_operation(operation, payload_hash)
            if prior:
                return {"runId": prior["runId"], "state": prior["state"], "deduplicated": True}
            self.check_revision(params["expectedRevision"])
            if not params["confirmed"]:
                raise SdlError("CONFIRMATION_REQUIRED", "Testing runs a real action; explicit confirmation is required.")
            if self.paused:
                raise SdlError("PAUSED", "Resume the Controller before testing an action.")
            item = next((b for _, b in all_buttons(self.document) if b["id"] == params["buttonId"]), None)
            if not item or not item["enabled"] or item["action"]["type"] != "core.execute":
                raise SdlError("ACTION_UNAVAILABLE", "An enabled, applied execution button is required.")
            if digest({"buttonId": item["id"], "action": item["action"]}) not in self.repository.approved:
                raise SdlError("REVIEW_REQUIRED", "The executable action is not approved.")
            return await self.execution.start(item, self.repository.revision, self.document["configurationId"],
                                              origin="test", operation_id=operation, payload_hash=payload_hash)
        if method == "execution.list":
            return self.execution.history.summaries(params.get("cursor", 0), params.get("limit", 20), params.get("includeOutput", False))
        if method == "execution.cancel":
            return await self.execution.cancel(params["runId"])
        if method.startswith("plugins."):
            return await self.extensions.api(method, params)
        if method == "render.preview":
            page = params.get("pageId", self.page_id)
            if page not in pages_by_id(self.document):
                raise SdlError("PAGE_UNAVAILABLE", "Page is not applied.")
            images = await self.render_call(self.document, page)
            cap = Capabilities(**self.document["layout"])
            preview = Image.new("RGB", (cap.columns * cap.keyWidth, cap.rows * cap.keyHeight))
            for index, image in enumerate(images):
                preview.paste(image, ((index % cap.columns) * cap.keyWidth, (index // cap.columns) * cap.keyHeight))
            buffer = io.BytesIO()
            preview.save(buffer, "PNG")
            return {"mediaType": "image/png", "data": base64.b64encode(buffer.getvalue()).decode(), "pageId": page, "revision": self.repository.revision}
        if method.startswith("simulator."):
            if not self.simulator:
                raise SdlError("METHOD_NOT_FOUND", "Simulator control is never available on a hardware Controller.")
            if method == "simulator.key":
                self.adapter.set_key(params["keyIndex"], params["down"])
            else:
                self.adapter.online = params["connected"]
                self.retry.set()
            return {"ok": True}
        raise SdlError("METHOD_NOT_FOUND", "Unknown method.")

    async def asset_method(self, method: str, params: dict, client) -> dict:
        if method == "asset.beginImport":
            if len(client.uploads) >= 2:
                raise SdlError("LIMIT_EXCEEDED", "Only two simultaneous image imports are allowed per connection.")
            identifier = str(uuid4())
            path = self.paths.runtime / "uploads" / identifier
            atomic_write(path, b"")
            client.uploads[identifier] = {"path": path, "name": params["name"], "size": params["byteCount"],
                                           "hash": params["sha256"], "sequence": 0, "received": 0,
                                           "expires": time.monotonic() + UPLOAD_SECONDS}
            return {"uploadId": identifier, "maxChunkBytes": CHUNK_BYTES}
        if method == "asset.readChunk":
            path = self.assets.directory(params["assetId"]) / "original"
            try:
                with path.open("rb") as stream:
                    stream.seek(params["offset"])
                    data = stream.read(params["length"])
                    size = os.fstat(stream.fileno()).st_size
            except OSError as exc:
                raise SdlError("ASSET_UNAVAILABLE", "Managed asset not found.") from exc
            return {"data": base64.b64encode(data).decode(), "totalBytes": size, "offset": params["offset"]}
        identifier = params["uploadId"]
        upload = client.uploads.get(identifier)
        if not upload or upload["expires"] < time.monotonic():
            raise SdlError("UPLOAD_UNAVAILABLE", "Upload expired or belongs to another connection.")
        if method == "asset.writeChunk":
            try:
                data = base64.b64decode(params["data"], validate=True)
            except ValueError as exc:
                raise SdlError("INVALID_PARAMS", "Invalid base64 image chunk.") from exc
            if len(data) > CHUNK_BYTES or not data or params["sequence"] != upload["sequence"] or upload["received"] + len(data) > upload["size"]:
                raise SdlError("INVALID_PARAMS", "Image chunk is oversized, empty, out of sequence or exceeds the declared size.")
            with upload["path"].open("ab") as stream:
                stream.write(data)
            upload["received"] += len(data)
            upload["sequence"] += 1
            return {"sequence": params["sequence"]}
        try:
            data = upload["path"].read_bytes()
            if len(data) != upload["size"] or hashlib.sha256(data).hexdigest() != upload["hash"]:
                raise SdlError("ASSET_INVALID", "Image upload size or SHA-256 mismatch.")
            return await self.assets.import_bytes(data, upload["name"])
        finally:
            upload["path"].unlink(missing_ok=True)
            del client.uploads[identifier]

    async def close(self) -> None:
        self.closing = True
        self.gate.reset(self.context, disconnect=True)
        if self.mailbox:
            self.mailbox.close()
        await self.server.close()
        await self.extensions.close()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.execution.close()
        with contextlib.suppress(Exception):
            await self.io_call(self.adapter.close)
        self.io_pool.shutdown(wait=True, cancel_futures=True)
        self.render_pool.shutdown(wait=True, cancel_futures=True)
        if self.lock:
            self.lock.close()
            self.lock = None
        LOG.info("CONTROLLER_STOPPED")


def diagnostics(session: SessionContextProvider | None = None) -> dict:
    versions = {}
    for name in ("streamdeck", "Pillow", "CairoSVG", "jsonschema", "defusedxml"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    import ctypes.util
    return {"runtimeVersion": VERSION, "python": sys.version.split()[0], "platform": platform.platform(),
            "dependencies": versions, "font": str(font_path()), "hidBackend": "LibUSBHIDAPI",
            "hidLibrary": ctypes.util.find_library("hidapi-libusb"),
            "codecs": {name: features.check(name) for name in ("jpg", "webp", "zlib", "littlecms2")},
            "graphicalSessionAvailable": session.graphical if session else None,
            "hardwareVerification": "not performed by this diagnostic", "pluginsSupported": True, "pluginApiVersion": "1.1", "pluginInvokeSupported": False}
