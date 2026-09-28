"""Execution with explicit argv, bounded output, no retries, and independent app units."""
import asyncio
import contextlib
import os
import shutil
import signal
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, dumps
from sdl_core.limits import MAX_JOBS, OUTPUT_BYTES
from sdl_core.model import command_argv, validate_executable

from .repository import ExecutionRepository
from .session import SessionContextProvider


@dataclass
class TaskHandle:
    process: asyncio.subprocess.Process
    lifetime_fd: int


class Launcher:
    def __init__(self, runtime: Path) -> None:
        self.runtime = runtime

    def _request(self, run_id: str, argv: list[str], cwd: str, environment: dict) -> Path:
        request = self.runtime / "jobs" / f"{run_id}.json"
        atomic_write(request, dumps({"argv": argv, "cwd": cwd, "environment": environment}))
        return request

    async def task(self, run_id: str, argv: list[str], cwd: str, environment: dict) -> TaskHandle:
        request = self._request(run_id, argv, cwd, environment)
        read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "sdl_controller.task_worker", str(request), str(read_fd),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, pass_fds=(read_fd,), start_new_session=True,
            )
        except BaseException:
            os.close(write_fd)
            request.unlink(missing_ok=True)
            raise
        finally:
            os.close(read_fd)
        return TaskHandle(process, write_fd)

    async def stop(self, handle: TaskHandle) -> None:
        with contextlib.suppress(OSError):
            os.close(handle.lifetime_fd)
        if handle.process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                handle.process.terminate()
            try:
                await asyncio.wait_for(handle.process.wait(), 3)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    handle.process.kill()
                await handle.process.wait()

    @staticmethod
    def application_command(run_id: str, request: Path) -> list[str]:
        return [
            "systemd-run", "--user", "--quiet", "--collect", "--service-type=exec",
            "--expand-environment=no", f"--unit=sdl-app-{run_id}",
            "--property=StandardInput=null", "--property=StandardOutput=null",
            "--property=StandardError=null", "--property=TimeoutStopSec=2s", "--",
            sys.executable, "-m", "sdl_controller.application_worker", str(request),
        ]

    async def application(self, run_id: str, argv: list[str], cwd: str, environment: dict) -> None:
        if not shutil.which("systemd-run"):
            raise SdlError("LAUNCH_UNAVAILABLE", "Application mode requires systemd-run and a logged-in systemd user manager.")
        request = self._request(run_id, argv, cwd, environment)
        process = await asyncio.create_subprocess_exec(
            *self.application_command(run_id, request), stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, error = await asyncio.wait_for(process.communicate(), 10)
        except TimeoutError:
            process.kill()
            await process.wait()
            # Do not cancel the unit/retry: an acknowledgement can be lost after launch.
            raise SdlError("OUTCOME_UNKNOWN", "Application launch acknowledgement timed out. Do not retry automatically.") from None
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            request.unlink(missing_ok=True)
            raise SdlError("LAUNCH_UNAVAILABLE", "The systemd user manager rejected application launch. Check sdlctl doctor and the graphical login session.")


async def drain(reader: asyncio.StreamReader, limit: int = OUTPUT_BYTES) -> tuple[str, bool]:
    kept = bytearray()
    truncated = False
    while data := await reader.read(16384):
        room = max(0, limit - len(kept))
        kept.extend(data[:room])
        truncated |= len(data) > room
    return kept.decode("utf-8", "replace") + ("\n[output truncated]" if truncated else ""), truncated


class ActionExecutor:
    def __init__(self, history: ExecutionRepository, launcher: Launcher, session: SessionContextProvider,
                 on_change, *, enabled: bool = True) -> None:
        self.history, self.launcher, self.session, self.on_change = history, launcher, session, on_change
        self.enabled = enabled
        self.running: dict[str, tuple[dict, asyncio.Task]] = {}
        self.closing = False

    async def start(self, item: dict, revision: int, configuration_id: str, *, origin: str,
                    operation_id: str | None = None, payload_hash: str | None = None) -> dict:
        if operation_id:
            existing = self.history.find_operation(operation_id, payload_hash)
            if existing is not None:
                return {"runId": existing["runId"], "state": existing["state"], "deduplicated": True}
        if not self.enabled:
            raise SdlError("EXECUTION_DISABLED", "Simulator execution is disabled. Restart with --allow-execution only for deliberate local tests.")
        if self.closing:
            raise SdlError("EXECUTION_DISABLED", "Controller is stopping.")
        action = item["action"]
        problems = validate_executable(action)
        if problems:
            raise SdlError("PATH_UNAVAILABLE", problems[0])
        if len(self.running) >= MAX_JOBS:
            raise SdlError("LIMIT_EXCEEDED", "Maximum 16 running actions reached.")
        current = sum(record["buttonId"] == item["id"] for record, _ in self.running.values())
        if current >= action["maxParallel"]:
            raise SdlError("ALREADY_RUNNING", "This button has reached its configured concurrency limit.")
        record = {
            "runId": str(uuid4()), "buttonId": item["id"], "revision": revision,
            "configurationId": configuration_id, "origin": origin, "mode": action["mode"],
            "state": "starting", "startedAt": time.time(), "finishedAt": None, "exitCode": None,
            "operationId": operation_id, "payloadHash": payload_hash,
            "stdout": "", "stderr": "", "stdoutTruncated": False, "stderrTruncated": False,
        }
        # Persist intent BEFORE dispatch. A restart cannot redispatch this operation ID.
        self.history.add(record)
        task = asyncio.create_task(self._run(record, action), name=f"execution-{record['runId']}")
        self.running[record["runId"]] = (record, task)
        def before_start_cancelled(finished):
            if finished.cancelled() and record["runId"] in self.running:
                self.running.pop(record["runId"], None)
                record["state"] = "cancelled"
                record["finishedAt"] = time.time()
                self.history.save()
                self.on_change(record)
        task.add_done_callback(before_start_cancelled)
        self.on_change(record)
        return {"runId": record["runId"], "state": "starting"}

    async def _run(self, record: dict, action: dict) -> None:
        handle = None
        drains = []
        try:
            argv = command_argv(action)
            environment = self.session.environment(action["environment"])
            if action["mode"] == "application":
                await self.launcher.application(record["runId"], argv, action["workingDirectory"], environment)
                record["state"] = "launched"
            else:
                handle = await self.launcher.task(record["runId"], argv, action["workingDirectory"], environment)
                record["state"] = "running"
                self.history.save()
                self.on_change(record)
                drains = [asyncio.create_task(drain(handle.process.stdout)), asyncio.create_task(drain(handle.process.stderr))]
                try:
                    code = await asyncio.wait_for(handle.process.wait(), action["timeoutMs"] / 1000)
                    record["state"] = "succeeded" if code == 0 else "failed"
                    record["exitCode"] = code
                except TimeoutError:
                    record["state"] = "timedOut"
        except asyncio.CancelledError:
            record["state"] = "unknownAfterRestart" if action["mode"] == "application" else "cancelled"
        except SdlError as exc:
            record["state"] = "unknownAfterRestart" if exc.code == "OUTCOME_UNKNOWN" else "failed"
            record["error"] = {"code": exc.code, "message": exc.message}
        except Exception:
            record["state"] = "failed"
            record["error"] = {"code": "EXECUTION_FAILED", "message": "Execution failed; inspect the configured paths and session."}
        finally:
            if handle:
                await self.launcher.stop(handle)
            if drains:
                try:
                    results = await asyncio.wait_for(asyncio.gather(*drains), 3)
                    (record["stdout"], record["stdoutTruncated"]), (record["stderr"], record["stderrTruncated"]) = results
                except TimeoutError:
                    record["stderrTruncated"] = True
            record["finishedAt"] = time.time()
            self.running.pop(record["runId"], None)
            try:
                self.history.save()
            except OSError:
                record["error"] = {"code": "HISTORY_WRITE_FAILED", "message": "Execution completed but its durable outcome could not be saved."}
            self.on_change(record)

    async def cancel(self, run_id: str) -> dict:
        if run_id not in self.running:
            raise SdlError("NOT_RUNNING", "Execution is not running.")
        record, task = self.running[run_id]
        if record["mode"] == "application":
            raise SdlError("CANCEL_UNSUPPORTED", "Independent applications are not cancelled by the Controller.")
        task.cancel()
        return {"ok": True}

    async def close(self) -> None:
        self.closing = True
        tasks = [task for _, task in self.running.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
