import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from conftest import eventually
from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, dumps
from sdl_core.model import button
from sdl_controller.execution import ActionExecutor, Launcher
from sdl_controller.repository import ExecutionRepository
from sdl_controller.session import SessionContextProvider


def execution_button(tmp_path, code, timeout=4000):
    script = tmp_path / "test script é.py"
    script.write_text(code)
    action = {"type": "core.execute", "path": str(script), "arguments": ["$HOME", "*.txt", "x;touch /not-executed"],
              "interpreter": {"path": sys.executable, "arguments": ["-u"]}, "workingDirectory": str(tmp_path),
              "environment": {"SDL_TEST": "$HOME is literal"}, "mode": "task", "timeoutMs": timeout,
              "concurrency": "ignoreWhileRunning", "maxParallel": 1}
    return button(0, "Test", action)


def executor(paths, enabled=True):
    return ActionExecutor(ExecutionRepository(paths.state / "executions.json"), Launcher(paths.runtime), SessionContextProvider(), lambda record: None, enabled=enabled)


async def test_literal_arguments_output_and_durable_intent(paths, tmp_path):
    runner = executor(paths)
    item = execution_button(tmp_path, 'import json,os,sys\nprint(json.dumps({"argv":sys.argv[1:],"env":os.environ["SDL_TEST"]}))\n')
    operation = str(uuid4())
    result = await runner.start(item, 1, "configuration", origin="test", operation_id=operation, payload_hash="hash")
    await eventually(lambda: not runner.running)
    record = runner.history.records[-1]
    assert record["state"] == "succeeded", record
    output = json.loads(record["stdout"])
    assert output["argv"] == item["action"]["arguments"]
    assert output["env"] == "$HOME is literal"
    again = await runner.start(item, 1, "configuration", origin="test", operation_id=operation, payload_hash="hash")
    assert again["runId"] == result["runId"]
    assert len(runner.history.records) == 1
    assert "is literal" not in runner.history.path.read_text()


async def test_noisy_output_bounded_and_drained(paths, tmp_path):
    runner = executor(paths)
    item = execution_button(tmp_path, 'import os\nos.write(1,b"x"*1000000)\nos.write(2,b"y"*1000000)\n')
    await runner.start(item, 1, "configuration", origin="test")
    await eventually(lambda: not runner.running)
    record = runner.history.records[-1]
    assert record["state"] == "succeeded"
    assert record["stdoutTruncated"] and record["stderrTruncated"]
    assert len(record["stdout"]) < 66000 and len(record["stderr"]) < 66000


async def test_timeout_concurrency_cancel_and_shutdown(paths, tmp_path):
    runner = executor(paths)
    item = execution_button(tmp_path, 'import time\ntime.sleep(30)\n', timeout=1000)
    await runner.start(item, 1, "configuration", origin="test")
    with pytest.raises(SdlError) as failure:
        await runner.start(item, 1, "configuration", origin="test")
    assert failure.value.code == "ALREADY_RUNNING"
    await eventually(lambda: not runner.running)
    assert runner.history.records[-1]["state"] == "timedOut"
    item["action"]["timeoutMs"] = 30000
    result = await runner.start(item, 1, "configuration", origin="test")
    await eventually(lambda: runner.history.records[-1]["state"] == "running")
    await runner.cancel(result["runId"])
    await eventually(lambda: not runner.running)
    assert runner.history.records[-1]["state"] == "cancelled"
    await runner.start(item, 1, "configuration", origin="test")
    await eventually(lambda: runner.history.records[-1]["state"] == "running")
    await runner.close()
    assert not runner.running
    assert runner.history.records[-1]["state"] == "cancelled"


async def test_invalid_path_does_not_launch(paths, tmp_path):
    runner = executor(paths)
    item = execution_button(tmp_path, "pass")
    item["action"]["path"] = str(tmp_path / "does not exist")
    with pytest.raises(SdlError) as failure:
        await runner.start(item, 1, "configuration", origin="test")
    assert failure.value.code == "PATH_UNAVAILABLE"
    assert not runner.history.records


def test_systemd_app_command_is_independent_and_does_not_expand(tmp_path):
    command = Launcher.application_command("example-id", tmp_path / "literal $HOME.json")
    assert "--user" in command
    assert "--expand-environment=no" in command
    assert not any("BindsTo" in value or "PartOf" in value for value in command)
    assert command[-1] == str(tmp_path / "literal $HOME.json")


async def test_task_lifetime_pipe_eof_stops_process_group(paths, tmp_path):
    marker = tmp_path / "pid"
    script = tmp_path / "sleep.py"
    script.write_text(f'import os,time\nopen({str(marker)!r},"w").write(str(os.getpid()))\ntime.sleep(30)\n')
    launcher = Launcher(paths.runtime)
    handle = await launcher.task(str(uuid4()), [sys.executable, str(script)], str(tmp_path), dict(os.environ))
    await eventually(marker.exists)
    pid = int(marker.read_text())
    # Closing the sole write end is also what the kernel does when a Controller is SIGKILLed.
    os.close(handle.lifetime_fd)
    await asyncio.wait_for(handle.process.wait(), 4)
    assert handle.process.returncode != 0
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_session_rejects_arbitrary_environment_injection():
    context = SessionContextProvider()
    with pytest.raises(SdlError):
        context.update({"LD_PRELOAD": "/anything"})
    context.update({"DISPLAY": ":7"})
    assert context.graphical
    context.update({"WAYLAND_DISPLAY": "wayland-1"})
    assert "DISPLAY" not in context.variables


async def test_immediate_cancel_before_worker_enters(paths, tmp_path):
    runner = executor(paths)
    item = execution_button(tmp_path, 'import time\ntime.sleep(30)\n')
    result = await runner.start(item, 1, "configuration", origin="test")
    await runner.cancel(result["runId"])
    await eventually(lambda: not runner.running)
    assert runner.history.records[-1]["state"] == "cancelled"
