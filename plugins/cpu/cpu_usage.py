#!/usr/bin/env python3
"""Fnord Deck CPU display plugin; JSON-RPC/JSONL API 1.1, no autonomous polling.

GPL-3.0-only. Run by a host, or use ../tools/cpu_demo.py. No third-party imports.
Only accepted visible refresh requests read /proc/stat. Clock/reader injection is
for tests, never a remotely configurable filesystem path.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import re
import sys
import time
from dataclasses import dataclass
from typing import Any, BinaryIO, Callable
from uuid import UUID

PLUGIN_ID = "org.fnord.cpu"
PLUGIN_VERSION = "0.2.0"
API_VERSION = "1.1"
MAX_FRAME_BYTES = 262_144
MAX_INSTANCES = 128
MIN_INTERVAL_MS = 1_000
MAX_INTERVAL_MS = 3_600_000
MAX_CPU_ROWS = 4_097
MAX_CPU_BYTES = 2_097_152
MAX_CPU_LINE = 4_096
CPU_NAME = re.compile(r"cpu(?:0|[1-9][0-9]*)?\Z")


class RpcError(Exception):
    def __init__(self, code: int, message: str, **data: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data

    def json(self) -> dict:
        return {"code": self.code, "message": self.message, "data": self.data}


def integer(value: Any, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RpcError(-32602, message)


def fields(obj: Any, required: set[str], optional: set[str] | None = None) -> dict:
    require(isinstance(obj, dict), "Expected an object.")
    require(required <= obj.keys() and obj.keys() <= required | (optional or set()),
            "Missing or unknown fields.")
    return obj


def identifier(value: Any) -> str:
    try:
        require(isinstance(value, str) and str(UUID(value)) == value, "Expected a canonical UUID.")
    except (ValueError, TypeError, AttributeError) as exc:
        raise RpcError(-32602, "Expected a canonical UUID.") from exc
    return value


def parse_cpu_rows(lines: Any) -> dict[str, tuple[int, ...]]:
    """Accept the initial CPU rows, not interrupts or other costly /proc details."""
    result: dict[str, tuple[int, ...]] = {}
    total_bytes = 0
    for line in lines:
        if not isinstance(line, str):
            raise ValueError("Expected text CPU rows.")
        parts = line.split()
        if not parts or not CPU_NAME.fullmatch(parts[0]):
            break
        total_bytes += len(line)
        if len(line) > MAX_CPU_LINE or total_bytes > MAX_CPU_BYTES or len(result) >= MAX_CPU_ROWS:
            raise ValueError("CPU snapshot exceeds bounds.")
        if parts[0] in result or not 9 <= len(parts) <= 17:
            raise ValueError("Duplicate or incomplete CPU row.")
        if any(not re.fullmatch(r"[0-9]{1,20}", part) for part in parts[1:]):
            raise ValueError("Invalid CPU counter.")
        # guest/guest_nice are already included in user/nice; never add twice.
        result[parts[0]] = tuple(int(part) for part in parts[1:9])
    if "cpu" not in result:
        raise ValueError("Missing aggregate CPU row.")
    return result


def read_cpu_rows() -> dict[str, tuple[int, ...]]:
    with Path("/proc/stat").open("r", encoding="ascii", errors="strict") as stream:
        def lines():
            while True:
                line = stream.readline(MAX_CPU_LINE + 1)
                if not line:
                    return
                # A potentially huge intr line is never read in its entirety.
                first = line.split(maxsplit=1)[0] if line.strip() else ""
                if not CPU_NAME.fullmatch(first):
                    return
                yield line
        return parse_cpu_rows(lines())


def utilization(before: tuple[int, ...], after: tuple[int, ...]) -> float | None:
    """0..100 aggregate/per-logical-CPU non-idle time, with iowait excluded.

    Any decreasing first-eight counter (including the unreliable iowait field),
    or zero elapsed counter time, needs a fresh baseline instead of a false 0%.
    Steal is included in non-idle time. This is not load average or process CPU.
    """
    delta = [new - old for old, new in zip(before, after, strict=True)]
    total = sum(delta)
    if any(value < 0 for value in delta) or total <= 0:
        return None
    return min(100.0, max(0.0, 100.0 * (total - delta[3] - delta[4]) / total))


def settings(value: Any) -> dict:
    fields(value, set(), {"scope", "logicalCpu", "label", "decimals", "warningPercent"})
    result = {"scope": "total", "logicalCpu": 0, "label": "CPU", "decimals": 0,
              "warningPercent": 85}
    result.update(value)
    require(result["scope"] in ("total", "logicalCpu"), "Invalid CPU scope.")
    require(integer(result["logicalCpu"], 0, 4095), "Invalid logical CPU index.")
    label = result["label"]
    require(isinstance(label, str) and len(label) <= 32 and
            all(ord(c) >= 32 and ord(c) != 127 and not 0xD800 <= ord(c) <= 0xDFFF for c in label),
            "Invalid label.")
    require(integer(result["decimals"], 0, 1), "Decimals must be 0 or 1.")
    warn = result["warningPercent"]
    require(type(warn) in (int, float) and 1 <= warn <= 100 and math.isfinite(warn),
            "Invalid warning threshold.")
    return result


def target(value: Any) -> dict:
    fields(value, {"deviceId", "keyIndex", "width", "height", "locale", "displayFields"})
    require(isinstance(value["deviceId"], str) and 1 <= len(value["deviceId"]) <= 128,
            "Invalid device identity.")
    require(integer(value["keyIndex"], 0, 4095), "Invalid key index.")
    require(integer(value["width"], 1, 1024) and integer(value["height"], 1, 1024),
            "Invalid target dimensions.")
    require(isinstance(value["locale"], str) and 1 <= len(value["locale"]) <= 32,
            "Invalid locale.")
    require(isinstance(value["displayFields"], list) and
            all(isinstance(x, str) and x in ("text", "icon", "progress")
                for x in value["displayFields"]), "Invalid display fields.")
    return dict(value)


@dataclass
class Snapshot:
    number: int
    at: float
    rows: dict[str, tuple[int, ...]] | None


@dataclass
class Instance:
    epoch: str
    options: dict
    interval_ms: int
    target: dict
    activation: str | None = None
    visible_since: float = 0.0
    last_request: float | None = None
    baseline: Snapshot | None = None
    sequence: int = 0


class CpuPlugin:
    def __init__(self, reader: Callable = read_cpu_rows,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.reader = reader
        self.clock = clock
        self.initialized = False
        self.stopping = False
        self.instances: dict[str, Instance] = {}
        self.minimum_ms = MIN_INTERVAL_MS
        self.maximum_instances = MAX_INSTANCES
        self.snapshot: Snapshot | None = None
        self.last_read: float | None = None
        self.read_count = 0

    def _instance(self, context: dict, visible: bool = False) -> Instance:
        key = identifier(context.get("instanceId"))
        epoch = identifier(context.get("instanceEpoch"))
        inst = self.instances.get(key)
        if inst is None or inst.epoch != epoch:
            raise RpcError(-32003, "Unknown instance or obsolete epoch.")
        if visible:
            activation = identifier(context.get("activationId"))
            if inst.activation is None or inst.activation != activation:
                raise RpcError(-32004, "Instance is hidden or activation is obsolete.")
        return inst

    def _discard_unused_snapshot(self) -> None:
        if not any(i.activation for i in self.instances.values()):
            self.snapshot = None
            # Keep last_read so hide/show churn cannot bypass the source floor.

    def _sample(self, now: float) -> Snapshot | None:
        visible = [i for i in self.instances.values() if i.activation]
        if not visible:
            return None
        interval = min(i.interval_ms for i in visible) / 1000.0
        if self.last_read is not None and now - self.last_read < interval:
            return self.snapshot
        self.last_read = now
        self.read_count += 1
        try:
            rows = self.reader()
        except (OSError, ValueError, UnicodeError):
            rows = None
        self.snapshot = Snapshot(self.read_count, now, rows)
        return self.snapshot

    def _refresh(self, context: dict, now: float | None = None) -> dict:
        inst = self._instance(context, visible=True)
        now = self.clock() if now is None else now
        interval = inst.interval_ms / 1000.0
        if inst.last_request is not None and now - inst.last_request < interval:
            remaining = max(1, math.ceil((interval - (now - inst.last_request)) * 1000))
            raise RpcError(-32005, "Refresh requested too soon.", retryAfterMs=remaining)
        inst.last_request = now
        sample = self._sample(now)
        opt = inst.options
        state = {"value": None, "unit": "%", "label": opt["label"], "status": "unavailable",
                 "message": "Collecting a baseline.", "progress": None, "image": None}
        name = "cpu" if opt["scope"] == "total" else "cpu" + str(opt["logicalCpu"])
        if sample is None or sample.at < inst.visible_since:
            inst.baseline = None
        elif sample.rows is None:
            state["message"] = "CPU counters are unavailable."
            inst.baseline = None
        elif name not in sample.rows:
            state["message"] = "The selected logical CPU is unavailable."
            inst.baseline = None
        else:
            previous = inst.baseline
            if previous and previous.number == sample.number:
                raise RpcError(-32008, "No new CPU sample.", retryAfterMs=inst.interval_ms)
            # Logical CPU topology changes also reset the aggregate baseline.
            if previous and previous.rows and previous.rows.keys() == sample.rows.keys():
                value = utilization(previous.rows[name], sample.rows[name])
                if value is not None:
                    value = round(value, opt["decimals"])
                    state.update(value=value, progress=value / 100.0, message=None,
                                 status="warning" if value >= opt["warningPercent"] else "ok")
                else:
                    state["message"] = "CPU counters changed; collecting a new baseline."
            inst.baseline = sample
        inst.sequence += 1
        return {"instanceId": context["instanceId"], "instanceEpoch": inst.epoch,
                "activationId": inst.activation, "sequence": inst.sequence, "state": state}

    def dispatch(self, method: str, params: Any) -> dict:
        require(isinstance(params, dict), "Parameters must be an object.")
        if method == "plugin.initialize":
            if self.initialized:
                raise RpcError(-32002, "Plugin is already initialized.")
            fields(params, {"apiVersion", "hostVersion", "pluginId", "pluginVersion",
                            "fingerprint", "locale", "limits"})
            require(params["apiVersion"] == API_VERSION, "Unsupported plugin API version.")
            require(params["pluginId"] == PLUGIN_ID and params["pluginVersion"] == PLUGIN_VERSION,
                    "Plugin identity mismatch.")
            require(isinstance(params["fingerprint"], str) and
                    re.fullmatch(r"[0-9a-f]{64}", params["fingerprint"]) is not None,
                    "Invalid package fingerprint.")
            require(isinstance(params["hostVersion"], str) and 1 <= len(params["hostVersion"]) <= 64,
                    "Invalid host version.")
            require(isinstance(params["locale"], str) and 1 <= len(params["locale"]) <= 32,
                    "Invalid locale.")
            limits = fields(params["limits"], {"minRefreshIntervalMs", "maxInstances", "maxMessageBytes"})
            require(integer(limits["minRefreshIntervalMs"], MIN_INTERVAL_MS, MAX_INTERVAL_MS),
                    "Invalid refresh floor.")
            require(integer(limits["maxInstances"], 1, MAX_INSTANCES), "Invalid instance limit.")
            require(integer(limits["maxMessageBytes"], MAX_FRAME_BYTES, MAX_FRAME_BYTES), "Unsupported frame limit.")
            self.minimum_ms = limits["minRefreshIntervalMs"]
            self.maximum_instances = limits["maxInstances"]
            self.initialized = True
            return {"apiVersion": API_VERSION, "pluginVersion": PLUGIN_VERSION,
                    "capabilities": ["display", "refreshMany"]}
        if not self.initialized:
            raise RpcError(-32001, "Initialize the plugin first.")
        if self.stopping:
            raise RpcError(-32009, "Plugin is stopping.")
        if method == "instance.create":
            fields(params, {"instanceId", "instanceEpoch", "contributionId", "settingsVersion",
                            "settings", "secretNames", "target", "effectiveRefreshIntervalMs"})
            key, epoch = identifier(params["instanceId"]), identifier(params["instanceEpoch"])
            require(key not in self.instances, "Duplicate instance identity.")
            if len(self.instances) >= self.maximum_instances:
                raise RpcError(-32007, "Instance limit reached.")
            require(params["contributionId"] == "usage" and type(params["settingsVersion"]) is int
                    and params["settingsVersion"] == 1, "Unsupported contribution/settings version.")
            require(params["secretNames"] == [], "CPU plugin does not consume secrets.")
            require(integer(params["effectiveRefreshIntervalMs"], self.minimum_ms, MAX_INTERVAL_MS),
                    "Refresh interval is outside allowed limits.")
            self.instances[key] = Instance(epoch, settings(params["settings"]),
                                           params["effectiveRefreshIntervalMs"], target(params["target"]))
            return {"ready": True}
        if method == "instance.visibility":
            fields(params, {"instanceId", "instanceEpoch", "visible", "activationId", "target"})
            inst = self._instance(params)
            require(type(params["visible"]) is bool, "Visibility must be Boolean.")
            new_target = target(params["target"])
            new_activation = identifier(params["activationId"]) if params["visible"] else None
            require(params["visible"] or params["activationId"] is None,
                    "A hidden instance must have a null activation ID.")
            if new_activation != inst.activation:
                inst.activation = new_activation
                inst.visible_since = self.clock()
                inst.baseline = None
                # Preserve last_request: reactivation cannot bypass rate limits.
            inst.target = new_target
            self._discard_unused_snapshot()
            return {"ok": True}
        if method == "instance.refresh":
            fields(params, {"instanceId", "instanceEpoch", "activationId", "deadlineMs"})
            require(integer(params["deadlineMs"], 1, 5000), "Invalid refresh deadline.")
            return self._refresh(params)
        if method == "instance.refreshMany":
            fields(params, {"instances", "deadlineMs"})
            require(integer(params["deadlineMs"], 1, 5000), "Invalid refresh deadline.")
            contexts = params["instances"]
            require(isinstance(contexts, list) and 1 <= len(contexts) <= self.maximum_instances,
                    "Invalid refresh batch size.")
            seen = set()
            for context in contexts:
                fields(context, {"instanceId", "instanceEpoch", "activationId"})
                key = identifier(context["instanceId"])
                identifier(context["instanceEpoch"])
                identifier(context["activationId"])
                require(key not in seen, "Duplicate instance in refresh batch.")
                seen.add(key)
            results = []
            now = self.clock()  # one timestamp/source acquisition window for the batch
            for context in contexts:
                try:
                    results.append(self._refresh(context, now))
                except RpcError as error:
                    results.append({**context, "error": error.json()})
            return {"results": results}
        if method == "instance.destroy":
            fields(params, {"instanceId", "instanceEpoch", "reason"})
            self._instance(params)
            require(isinstance(params["reason"], str) and len(params["reason"]) <= 128,
                    "Invalid destroy reason.")
            del self.instances[params["instanceId"]]
            self._discard_unused_snapshot()
            return {"ok": True}
        if method == "plugin.ping":
            fields(params, set())
            return {"ok": True}
        if method == "plugin.shutdown":
            fields(params, {"reason"})
            require(isinstance(params["reason"], str) and len(params["reason"]) <= 128,
                    "Invalid shutdown reason.")
            self.instances.clear()
            self.snapshot = None
            self.stopping = True
            return {"ok": True}
        raise RpcError(-32601, "Method is not supported.")


def strict_loads(raw: bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key.")
            result[key] = value
        return result

    def nonfinite(_):
        raise ValueError("Non-finite JSON number.")

    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Non-finite JSON number.")
        return number

    return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                      parse_constant=nonfinite, parse_float=finite_float)


def handle(plugin: CpuPlugin, request: Any) -> dict | None:
    request_id = None
    try:
        if not isinstance(request, dict) or request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
            raise RpcError(-32600, "Invalid JSON-RPC request.")
        if "id" not in request:
            # This plugin's lifecycle methods require requests; notifications have
            # no side effects and never receive a response (JSON-RPC 2.0).
            return None
        if not isinstance(request["id"], str) or not re.fullmatch(r"h-[A-Za-z0-9_-]{1,80}", request["id"]):
            raise RpcError(-32600, "Invalid host request ID.")
        request_id = request["id"]
        if request.keys() - {"jsonrpc", "id", "method", "params"}:
            raise RpcError(-32600, "Unknown envelope field.")
        result = plugin.dispatch(request["method"], request.get("params", {}))
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    except RpcError as error:
        return {"jsonrpc": "2.0", "id": request_id, "error": error.json()}


def serve(source: BinaryIO, sink: BinaryIO, plugin: CpuPlugin | None = None) -> int:
    plugin = plugin or CpuPlugin()
    while not plugin.stopping:
        raw = source.readline(MAX_FRAME_BYTES + 1)  # blocks, no timer or idle polling
        if not raw:
            return 0  # parent closed pipe; no orphan/background work
        if len(raw) > MAX_FRAME_BYTES or not raw.endswith(b"\n"):
            return 2
        try:
            request = strict_loads(raw)
        except (ValueError, UnicodeError, RecursionError):
            response = {"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Invalid JSON frame.", "data": {}}}
            sink.write((json.dumps(response) + "\n").encode("utf-8"))
            sink.flush()
            return 2
        response = handle(plugin, request)
        if response is not None:
            encoded = (json.dumps(response, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
            if len(encoded) > MAX_FRAME_BYTES:
                return 2
            sink.write(encoded)
            sink.flush()
    return 0


def main() -> int:
    # Prevent interpreter-generated cache files inside the approved package.
    sys.dont_write_bytecode = True
    try:
        return serve(sys.stdin.buffer, sys.stdout.buffer)
    except BrokenPipeError:
        return 0
    except (OSError, ValueError, RecursionError):
        print("CPU_PLUGIN_IO_ERROR", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
