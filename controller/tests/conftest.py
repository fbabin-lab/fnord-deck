import asyncio
import os
import tempfile
from pathlib import Path

import pytest

from sdl_core.paths import Paths


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    runtime = tempfile.TemporaryDirectory(prefix="sdl-test-")
    source = str(Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", source)
    for variable, name in (("XDG_CONFIG_HOME", "config"), ("XDG_DATA_HOME", "data"),
                           ("XDG_STATE_HOME", "state"), ("XDG_CACHE_HOME", "cache"),
                           ("XDG_RUNTIME_DIR", "run"), ("HOME", "home")):
        path = tmp_path / name
        path.mkdir(mode=0o700)
        monkeypatch.setenv(variable, runtime.name if variable == "XDG_RUNTIME_DIR" else str(path))
    yield
    runtime.cleanup()


@pytest.fixture
def paths():
    value = Paths.discover("simulator")
    value.prepare()
    return value


async def eventually(predicate, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    assert predicate(), "condition did not become true"
