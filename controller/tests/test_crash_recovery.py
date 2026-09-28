"""Real subprocess Controller SIGKILL test; all scripts/state are temporary test fixtures."""
import asyncio
import contextlib
import os
import sys
from uuid import uuid4

from conftest import eventually
from sdl_cli.client import Client
from sdl_cli.main import demo_configuration
from sdl_core.errors import SdlError


async def connect_when_ready(path):
    for _ in range(150):
        try:
            client = Client(path)
            await client.__aenter__()
            return client
        except (SdlError, OSError):
            await asyncio.sleep(0.03)
    raise AssertionError("Subprocess Controller never started")


async def test_sigkill_cleans_task_and_restart_does_not_repeat_intent(paths, tmp_path):
    child_pid_file = tmp_path / "task-pid"
    count_file = tmp_path / "launch-count"
    script = tmp_path / "test-task.py"
    script.write_text(f'import os,time\nopen({str(child_pid_file)!r},"w").write(str(os.getpid()))\nopen({str(count_file)!r},"a").write("started\\n")\ntime.sleep(30)\n')
    process = None
    client = None
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "sdl_controller.main", "--simulate", "--allow-execution", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        client = await connect_when_ready(paths.socket)
        doc = demo_configuration()
        item = doc["pages"][0]["buttons"][1]
        item["action"] = {"type": "core.execute", "path": str(script), "arguments": [],
                          "interpreter": {"path": sys.executable, "arguments": []}, "workingDirectory": str(tmp_path),
                          "environment": {}, "mode": "task", "timeoutMs": 30000,
                          "concurrency": "ignoreWhileRunning", "maxParallel": 1}
        review = await client.call("configuration.review", {"document": doc})
        await client.call("configuration.apply", {"document": doc, "expectedRevision": 0, "operationId": str(uuid4()), "reviewToken": review["reviewToken"], "confirmed": True})
        operation = str(uuid4())
        params = {"buttonId": item["id"], "expectedRevision": 1, "confirmed": True, "operationId": operation}
        first = await client.call("execution.test", params)
        await eventually(child_pid_file.exists)
        pid = int(child_pid_file.read_text())
        process.kill()
        await process.wait()
        await client.__aexit__()
        client = None
        def gone():
            try:
                os.kill(pid, 0)
                return False
            except ProcessLookupError:
                return True
        await eventually(gone, timeout=5)
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "sdl_controller.main", "--simulate", "--allow-execution", stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        client = await connect_when_ready(paths.socket)
        again = await client.call("execution.test", params)
        assert again["runId"] == first["runId"]
        assert again["state"] == "unknownAfterRestart"
        assert again["deduplicated"] is True
        await asyncio.sleep(0.2)
        assert count_file.read_text() == "started\n"
    finally:
        if client is not None:
            with contextlib.suppress(Exception):
                await client.__aexit__()
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 5)
            except TimeoutError:
                process.kill()
                await process.wait()
