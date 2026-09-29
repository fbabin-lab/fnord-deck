"""User-unit resource limits verified before releasing the trusted launch gate."""
from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import shutil
import signal
import sys
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.jsonutil import loads
from .transport import FRAME, Wire

SLICE = "app-sdlplugins.slice"
CONTROLLER_UNIT = "sdl-controller.service"


async def command(*args: str) -> str:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
                                                  stderr=asyncio.subprocess.DEVNULL, limit=16384)
    try:
        async with asyncio.timeout(3):
            raw = await process.stdout.read(16385)
            if len(raw) > 16384:
                raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "Unexpected systemd response size.")
            await process.wait()
        if process.returncode:
            raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "The systemd user manager could not enforce plugin limits.")
        return raw.decode("utf-8")
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


def cgroup_limits(path: Path, *, cpu: float, high: int, memory: int, tasks: int) -> None:
    try:
        quota, period = (path / "cpu.max").read_text().split()
        if quota == "max" or int(period) <= 0 or not 0 < int(quota) / int(period) <= cpu + .000001:
            raise ValueError("CPU quota not enforced")
        for field, maximum in (("memory.high", high), ("memory.max", memory), ("pids.max", tasks)):
            if not 0 < int((path / field).read_text()) <= maximum:
                raise ValueError("Resource limit not enforced")
    except (OSError, ValueError) as exc:
        raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "Required cgroup CPU/memory/task ceilings are not enforced.") from exc


def process_cgroup(pid: int) -> Path:
    lines = Path(f"/proc/{pid}/cgroup").read_text().splitlines()
    group = next((line[3:] for line in lines if line.startswith("0::/")), None)
    if not group or ".." in Path(group).parts:
        raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "A unified cgroup v2 hierarchy is required.")
    return Path("/sys/fs/cgroup") / group.lstrip("/")


class Worker:
    def __init__(self, process, unit, snapshot, mode, wake):
        self.process, self.unit, self.snapshot, self.mode = process, unit, snapshot, mode
        self.wire: Wire | None = None
        self.wake = wake
        self.pid = None
        self.cgroup = None
        self.enforced = False
        self.stopping = False
        self.reaped = False

    async def close(self):
        if self.stopping:
            return
        self.stopping = True
        try:
            if self.wire and not self.wire.pending and not self.wire.fatal and self.process.returncode is None:
                with contextlib.suppress(Exception):
                    await self.wire.request("plugin.shutdown", {"reason": "hidden-or-stopped"}, .25)
            if self.process.stdin:
                self.process.stdin.close()
            if self.unit:
                # Kill the complete unit, not just the systemd-run bridge process.
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(command("systemctl", "--user", "--no-block", "stop", self.unit), .25)
            elif self.process.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(self.process.wait(), .5)
            except TimeoutError:
                if self.unit:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(command("systemctl", "--user", "kill", "--kill-whom=all", "--signal=SIGKILL", self.unit), .25)
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(self.process.pid, signal.SIGKILL)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.process.wait(), .5)
            self.reaped = self.process.returncode is not None
        finally:
            if self.wire:
                await self.wire.close()
            if self.reaped:
                await asyncio.to_thread(shutil.rmtree, self.snapshot, True)
            self.wake()

    def accounting(self) -> dict:
        if not self.enforced or not self.cgroup:
            return {}
        try:
            fields = dict(line.split() for line in (self.cgroup / "cpu.stat").read_text().splitlines())
            return {"cpuUsec": int(fields["usage_usec"]), "memoryBytes": int((self.cgroup / "memory.current").read_text()),
                    "tasks": int((self.cgroup / "pids.current").read_text())}
        except (OSError, ValueError, KeyError):
            return {}


class PluginLauncher:
    def __init__(self, mode: str = "systemd"):
        if mode not in {"systemd", "development"}:
            raise ValueError("Invalid plugin launch mode")
        self.mode = mode

    def argv(self, argv: list[str], snapshot: Path, unit: str) -> list[str]:
        bootstrap = str(Path(__file__).with_name("bootstrap.py"))
        gated = [sys.executable, "-I", "-B", bootstrap, str(os.getpid()), self.mode, *argv]
        if self.mode == "development":
            return gated
        properties = ["CPUQuota=5%", "MemoryHigh=48M", "MemoryMax=64M", "TasksMax=8", "Nice=10",
                      "LimitNOFILE=64", "KillMode=control-group", "TimeoutStopSec=500ms", "SendSIGKILL=yes",
                      "CPUAccounting=yes", "MemoryAccounting=yes", "TasksAccounting=yes", "Restart=no",
                      "NoNewPrivileges=yes", f"BindsTo={CONTROLLER_UNIT}", f"After={CONTROLLER_UNIT}"]
        return ["systemd-run", "--user", "--quiet", "--no-ask-password", "--collect", "--pipe", "--wait",
                "--service-type=exec", "--expand-environment=no", f"--unit={unit}", f"--slice={SLICE}",
                f"--working-directory={snapshot}", *[f"--property={p}" for p in properties], "--", *gated]

    async def launch(self, package, snapshot: Path, wake) -> Worker:
        unit = None
        worker = None
        try:
            if self.mode == "systemd":
                parent = await command("systemctl", "--user", "show", CONTROLLER_UNIT, "--property=MainPID", "--value")
                if int(parent.strip() or "0") != os.getpid():
                    raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "Start the Controller with sdl-session --start. Foreground plugins require explicit development mode.")
                unit = "sdl-plugin-" + uuid4().hex + ".service"
            process = await asyncio.create_subprocess_exec(*self.argv(package.argv(snapshot), snapshot, unit or ""),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                limit=FRAME + 1, start_new_session=True, cwd=snapshot)
            worker = Worker(process, unit, snapshot, self.mode, wake)
            async with asyncio.timeout(5):
                ready = loads(await process.stdout.readline())
                if not isinstance(ready, dict) or ready.get("gate") != "ready" or type(ready.get("pid")) is not int or ready["pid"] <= 0:
                    raise SdlError("PLUGIN_LAUNCH_FAILED", "Trusted plugin launch gate failed.")
                worker.pid = ready["pid"]
                if self.mode == "systemd":
                    text = await command("systemctl", "--user", "show", unit, "--property=MainPID,ControlGroup,Slice,BindsTo,KillMode")
                    props = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
                    if props.get("MainPID") != str(worker.pid) or props.get("Slice") != SLICE or props.get("KillMode") != "control-group" or CONTROLLER_UNIT not in props.get("BindsTo", "").split():
                        raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "Plugin lifetime/resource unit verification failed.")
                    worker.cgroup = await asyncio.to_thread(process_cgroup, worker.pid)
                    expected = Path("/sys/fs/cgroup") / props.get("ControlGroup", "").lstrip("/")
                    if worker.cgroup != expected or worker.cgroup.parent.name != SLICE:
                        raise SdlError("RESOURCE_LIMIT_UNAVAILABLE", "Plugin is outside its resource slice.")
                    await asyncio.to_thread(cgroup_limits, worker.cgroup, cpu=.05, high=48*1024**2, memory=64*1024**2, tasks=8)
                    await asyncio.to_thread(cgroup_limits, worker.cgroup.parent, cpu=.15, high=384*1024**2, memory=512*1024**2, tasks=64)
                    worker.enforced = True
                process.stdin.write(b"GO\n")
                await process.stdin.drain()
                worker.wire = Wire(process, wake)
            return worker
        except BaseException:
            if worker:
                await asyncio.shield(worker.close())
            else:
                await asyncio.to_thread(shutil.rmtree, snapshot, True)
            raise
