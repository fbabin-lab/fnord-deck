#!/usr/bin/env python3
"""Finite, terminal-only reference host. Not Controller integration or a sandbox."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time
from uuid import uuid4
from verify_package import verify

ROOT = Path(__file__).resolve().parents[1]


class Client:
    def __init__(self) -> None:
        _, self.fingerprint = verify(ROOT / "cpu")
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-B", str(ROOT / "cpu/cpu_usage.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.pending = b""
        self.sequence = 0

    def call(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = f"h-{self.sequence}"
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.process.stdin.write((json.dumps(message) + "\n").encode())
        self.process.stdin.flush()
        deadline = time.monotonic() + 5
        while b"\n" not in self.pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not self.selector.select(remaining):
                raise RuntimeError("Plugin response timed out.")
            chunk = os.read(self.process.stdout.fileno(), 65_536)
            if not chunk:
                raise RuntimeError("Plugin closed its output unexpectedly.")
            self.pending += chunk
            if len(self.pending) > 262_144:
                raise RuntimeError("Plugin response exceeds frame limit.")
        raw, self.pending = self.pending.split(b"\n", 1)
        response = json.loads(raw)
        if response.get("id") != request_id:
            raise RuntimeError("Mismatched response identity.")
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        return response["result"]

    def initialize(self) -> None:
        self.call("plugin.initialize", {"apiVersion": "1.1", "hostVersion": "reference-demo",
                  "pluginId": "org.fnord.cpu", "pluginVersion": "0.2.0", "fingerprint": self.fingerprint,
                  "locale": "en", "limits": {"minRefreshIntervalMs": 1000,
                  "maxInstances": 128, "maxMessageBytes": 262144}})

    def close(self) -> None:
        self.selector.close()
        try:
            self.process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=2)
        self.process.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3, help="1..30 displayed samples after baseline")
    parser.add_argument("--interval", type=int, default=2, help="1..60 seconds between requests")
    parser.add_argument("--cpu", action="append", help="total or a logical CPU index; repeat for multiple buttons")
    args = parser.parse_args()
    if not 1 <= args.samples <= 30 or not 1 <= args.interval <= 60:
        parser.error("samples must be 1..30 and interval 1..60 seconds")
    cpus = args.cpu or ["total"]
    if len(cpus) > 32 or any(c != "total" and (not c.isascii() or not c.isdigit() or not 0 <= int(c) <= 4095) for c in cpus):
        parser.error("Use up to 32 --cpu values: total or logical CPU 0..4095")
    client = Client()
    try:
        client.initialize()
        instances = []
        for index, cpu in enumerate(cpus):
            context = {"instanceId": str(uuid4()), "instanceEpoch": str(uuid4())}
            target = {"deviceId": "terminal-demo", "keyIndex": index, "width": 96, "height": 96,
                      "locale": "en", "displayFields": ["text", "progress"]}
            client.call("instance.create", {**context, "contributionId": "usage", "settingsVersion": 1,
                        "settings": {"scope": "total" if cpu == "total" else "logicalCpu",
                                     "logicalCpu": 0 if cpu == "total" else int(cpu), "label": "CPU " + cpu},
                        "secretNames": [], "target": target, "effectiveRefreshIntervalMs": args.interval * 1000})
            activation = str(uuid4())
            client.call("instance.visibility", {**context, "visible": True, "activationId": activation,
                                                "target": target})
            instances.append(({**context, "activationId": activation}, target))
        print("Terminal protocol demo only; the installed Controller and USB device are untouched.")
        for step in range(args.samples + 1):
            response = client.call("instance.refreshMany", {"instances": [c for c, _ in instances], "deadlineMs": 1000})
            for item in response["results"]:
                state = item.get("state")
                if state is None:
                    print("Plugin error:", item["error"])
                    continue
                value = "collecting baseline / unavailable" if state["value"] is None else f"{state['value']:g}%"
                print(f"{state['label']}: {value} ({state['status']})")
            if step < args.samples:
                time.sleep(args.interval)  # host-owned scheduling, never inside the plugin
        for context, target in instances:
            client.call("instance.visibility", {**context, "visible": False, "activationId": None, "target": target})
        client.call("plugin.shutdown", {"reason": "demo-finished"})
        print("All instances hidden; plugin stopped.")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        raise SystemExit(f"Demo failed: {error}")
