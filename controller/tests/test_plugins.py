"""Production host integration, using the real reference worker and simulator."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pytest

from conftest import eventually
from sdl_cli.client import Client
from sdl_controller.controller import Controller
from sdl_controller.plugins.host import next_due
from sdl_controller.plugins.launcher import PluginLauncher, cgroup_limits
from sdl_controller.plugins.registry import verify
from sdl_controller.plugins.transport import Budget
from sdl_core.dynamic import DEFAULT_DYNAMIC, validate_dynamic, validate_state, visual
from sdl_core.errors import SdlError
from sdl_core.model import button, empty_configuration, validate

SOURCE = Path(__file__).resolve().parents[2] / "plugins/cpu"


def layout(count=2, navigation=True):
    doc = empty_configuration()
    root = doc["pages"][0]
    if navigation:
        child = {"id": str(uuid4()), "name": "No plugins", "parentPageId": root["id"], "buttons": []}
        doc["pages"].append(child)
        root["buttons"].append(button(0, "Away", {"type": "core.navigate", "pageId": child["id"]}))
    for number in range(count):
        b = button(number + int(navigation), "CPU fallback")
        b["pluginBinding"] = {"instanceId": str(uuid4()), "pluginId": "org.fnord.cpu", "pluginVersion": "0.2.0", "contributionId": "usage",
            "settingsVersion": 1, "settings": {"scope": "logicalCpu" if number else "total", "logicalCpu": 0,
            "label": "CPU 0" if number else "CPU", "decimals": 0, "warningPercent": 85}, "secretRefs": {}, "refreshIntervalMs": 2000}
        b["appearance"]["dynamic"] = copy.deepcopy(DEFAULT_DYNAMIC)
        root["buttons"].append(b)
    return doc


async def apply(c, doc):
    return await c.apply({"document": doc, "expectedRevision": c.repository.revision, "operationId": str(uuid4())})


async def installed(paths, *, allowed=True, mode="development", approve=True):
    (paths.data / "plugins").mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = paths.data / "plugins/org.fnord.cpu/0.2.0"
    shutil.copytree(SOURCE, destination)
    c = Controller(paths, simulator=True, allow_plugins=allowed, plugin_mode=mode)
    await c.start()
    if approve:
        catalog = await c.extensions.api("plugins.list", {})
        assert len(catalog["packages"]) == 1, catalog
        package = catalog["packages"][0]
        await c.extensions.api("plugins.approve", {k: package[k] for k in ("pluginId", "pluginVersion", "fingerprint", "interpreterIdentity")} | {"confirmed": True})
    await eventually(lambda: c.synchronized)
    return c


async def test_cpu_reaches_physical_painter_two_instances_and_stops_hidden(paths):
    c = await installed(paths)
    try:
        doc = layout()
        assert not c.extensions.sessions
        await apply(c, doc)
        await eventually(lambda: len(c.extensions.instances) == 2)
        await eventually(lambda: all(l.last_measurement is not None for l in c.extensions.instances.values()), timeout=8)
        assert len(c.extensions.sessions) == 1
        assert c.extensions.paint_count >= 1
        lives = list(c.extensions.instances.values())
        assert lives[0].identifier != lives[1].identifier
        assert {l.state["label"] for l in lives} == {"CPU", "CPU 0"}
        session = next(iter(c.extensions.sessions.values()))
        pid = session.worker.pid
        assert pid and Path(f"/proc/{pid}").exists()
        c.navigate(doc["pages"][1]["id"])
        assert not c.extensions.instances
        await eventually(lambda: not c.extensions.sessions and not c.extensions.retiring)
        assert not Path(f"/proc/{pid}").exists()
        before = [l.refreshes for l in lives]
        await asyncio.sleep(1.1)
        assert [l.refreshes for l in lives] == before
        assert not c.extensions.pending
        assert not c.execution.history.records
    finally:
        await c.close()


@pytest.mark.parametrize("operation", ["pause", "blank", "brightness", "disconnect"])
async def test_invisibility_revokes_immediately(paths, operation):
    c = await installed(paths)
    try:
        doc = layout(1)
        await apply(c, doc)
        await eventually(lambda: bool(c.extensions.sessions) and next(iter(c.extensions.sessions.values())).phase == "ready")
        if operation == "pause":
            await c.dispatch("runtime.pause", {"paused": True}, None)
        elif operation == "blank":
            await c.dispatch("runtime.blank", {"blanked": True}, None)
        elif operation == "brightness":
            doc["settings"]["brightnessPercent"] = 0
            await apply(c, doc)
        else:
            c.adapter.online = False
            c.retry.set()
        await eventually(lambda: not c.extensions.instances and not c.extensions.sessions and not c.extensions.retiring)
    finally:
        await c.close()


async def test_dormant_unapproved_or_simulator_not_opted_in(paths):
    c = await installed(paths, allowed=False)
    try:
        await apply(c, layout())
        await eventually(lambda: c.synchronized)
        await asyncio.sleep(.25)
        assert c.extensions.status()["allowed"] is False
        assert not c.extensions.sessions
        await c.extensions.api("plugins.rescan", {})
        await c.extensions.api("plugins.restart", {"pluginId": "org.fnord.cpu", "pluginVersion": "0.2.0"})
        assert not c.extensions.sessions
    finally:
        await c.close()


async def test_unapproved_never_runs_then_approval_activates_only_visible(paths):
    c = await installed(paths, approve=False)
    try:
        await apply(c, layout(1))
        await eventually(lambda: c.synchronized)
        await asyncio.sleep(.3)
        assert not c.extensions.sessions
        assert c.extensions.blocked[0]["code"] == "PLUGIN_APPROVAL_REQUIRED"
        catalog = await c.extensions.api("plugins.list", {})
        package = catalog["packages"][0]
        with pytest.raises(SdlError):
            await c.extensions.api("plugins.approve", {k: package[k] for k in ("pluginId", "pluginVersion", "fingerprint", "interpreterIdentity")} | {"confirmed": False})
        assert not c.extensions.sessions
    finally:
        await c.close()


async def test_default_resource_mode_fails_closed_without_user_service(paths):
    c = await installed(paths, mode="systemd")
    try:
        await apply(c, layout(1))
        await eventually(lambda: bool(c.extensions.reasons), timeout=8)
        assert "RESOURCE_LIMIT_UNAVAILABLE" in c.extensions.reasons.values()
        await eventually(lambda: not c.extensions.sessions and not c.extensions.retiring)
        assert c.synchronized
        assert not c.extensions.status()["resourceLimitsEnforced"]
    finally:
        await c.close()


async def test_metric_paints_do_not_reset_held_button(paths):
    c = await installed(paths)
    events = []
    original_emit = c.emit
    def capture(kind, value):
        events.append((kind, value))
        original_emit(kind, value)
    c.emit = capture
    try:
        await apply(c, layout(1))
        await eventually(lambda: bool(c.extensions.instances))
        await eventually(lambda: c.synchronized and c.gate.enabled)
        context = c.context
        c.adapter.set_key(1, True)
        await asyncio.sleep(.05)
        await eventually(lambda: c.extensions.paint_count > 0, timeout=8)
        assert c.context == context and c.gate.enabled
        c.adapter.set_key(1, False)
        await asyncio.sleep(.15)
        accepted = [e for e in events if e[0] == "button.activated" and e[1]["keyIndex"] == 1]
        assert len(accepted) == 1
    finally:
        await c.close()


async def test_duplicate_instances_different_parameters_one_worker(paths):
    c = await installed(paths)
    try:
        doc = layout(32, navigation=False)
        for b in doc["pages"][0]["buttons"]:
            b["pluginBinding"]["refreshIntervalMs"] = 1  # clamped by host, cannot busy-loop
        await apply(c, doc)
        await eventually(lambda: len(c.extensions.instances) == 32, timeout=8)
        await eventually(lambda: all(l.refreshes >= 2 for l in c.extensions.instances.values()), timeout=8)
        assert len(c.extensions.sessions) == 1
        assert all(l.interval == 1 for l in c.extensions.instances.values())
        assert len(c.extensions.pending) <= 32
        assert c.extensions.paint_count <= 32
    finally:
        await c.close()


async def test_apply_keeps_worker_but_replaces_stale_activations(paths):
    c = await installed(paths)
    try:
        doc = layout()
        await apply(c, doc)
        await eventually(lambda: len(c.extensions.instances) == 2)
        await eventually(lambda: bool(c.extensions.sessions) and next(iter(c.extensions.sessions.values())).worker is not None)
        before = list(c.extensions.instances.values())
        old_pid = next(iter(c.extensions.sessions.values())).worker.pid
        doc["pages"][0]["buttons"][1]["enabled"] = False
        await apply(c, doc)
        await eventually(lambda: c.synchronized and len(c.extensions.instances) == 1)
        await eventually(lambda: next(iter(c.extensions.sessions.values())).busy is None)
        live = next(iter(c.extensions.instances.values()))
        assert live.epoch != before[1].epoch
        assert next(iter(c.extensions.sessions.values())).worker.pid == old_pid
        assert all(not c.extensions.valid(old) for old in before)
    finally:
        await c.close()


def test_package_fingerprint_matches_reference_and_tamper_rejected(tmp_path):
    path = tmp_path / "cpu"
    shutil.copytree(SOURCE, path)
    p = verify(path)
    assert len(p.fingerprint) == 64
    (path / "cpu_usage.py").write_text("raise SystemExit(99)\n")
    with pytest.raises(SdlError):
        verify(path)


def test_symlink_package_file_rejected_without_execution(tmp_path):
    path = tmp_path / "cpu"
    shutil.copytree(SOURCE, path)
    (path / "README.md").unlink()
    (path / "README.md").symlink_to(SOURCE / "README.md")
    with pytest.raises(SdlError):
        verify(path)


def test_duplicate_instance_ids_invalid_and_display_templates_literal():
    doc = layout()
    a, b = doc["pages"][0]["buttons"][1:]
    b["pluginBinding"]["instanceId"] = a["pluginBinding"]["instanceId"]
    assert any(e["code"] == "DUPLICATE_PLUGIN_INSTANCE" for e in validate(doc)["errors"])
    for text in ["{{value.__class__}}", "{{value+1}}", "{{secrets.token}}"]:
        with pytest.raises(SdlError):
            validate_dynamic({**DEFAULT_DYNAMIC, "textTemplate": text})


def test_rounded_visuals_and_null_are_not_false_zero():
    b = layout(1)["pages"][0]["buttons"][1]
    state = {"value": 12.1, "unit": "%", "label": "CPU", "status": "ok", "message": None, "progress": .121, "image": None}
    a = visual(b, state, 96)
    b_value = visual(b, {**state, "value": 12.2, "progress": .122, "message": "fresh"}, 96)
    assert a == b_value
    assert visual(b, {**state, "value": None, "status": "unavailable"}, 96)["text"] is None
    assert "0%" in visual(b, {**state, "value": 0, "progress": 0}, 96)["text"]
    with pytest.raises(SdlError):
        validate_state({**state, "value": float("nan")})


def test_skip_missed_ticks_and_token_budget():
    assert next_due(0, .1, 2) == 2
    assert next_due(0, 3, 2) == 5
    now = [0]
    b = Budget(8, 8, lambda: now[0])
    assert all(b.take() for _ in range(8))
    assert not b.take()
    now[0] = .125
    assert b.take() and not b.take()


def test_systemd_literal_arguments_and_required_limits(tmp_path):
    launcher = PluginLauncher()
    command = launcher.argv(["/usr/bin/python3", "script.py", "$HOME", "a b"], tmp_path, "sdl-plugin-test.service")
    assert "--expand-environment=no" in command
    assert "--property=CPUQuota=5%" in command
    assert "--property=BindsTo=sdl-controller.service" in command
    assert "--property=KillMode=control-group" in command
    assert command[-2:] == ["$HOME", "a b"]
    for name, value in {"cpu.max": "5000 100000", "memory.high": str(48*1024**2), "memory.max": str(64*1024**2), "pids.max": "8"}.items():
        (tmp_path / name).write_text(value)
    cgroup_limits(tmp_path, cpu=.05, high=48*1024**2, memory=64*1024**2, tasks=8)
    (tmp_path / "cpu.max").write_text("max 100000")
    with pytest.raises(SdlError):
        cgroup_limits(tmp_path, cpu=.05, high=48*1024**2, memory=64*1024**2, tasks=8)
