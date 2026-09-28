import asyncio
import copy
import io
import os
import socket
import struct
from uuid import uuid4

import pytest
from PIL import Image

from conftest import eventually
from sdl_cli.client import Client
from sdl_cli.main import demo_configuration
from sdl_core.errors import SdlError
from sdl_core.model import button
from sdl_controller.controller import Controller
from sdl_controller.ipc import authorized_peer, encode_frame, read_frame


@pytest.fixture
async def controller(paths):
    controller = Controller(paths, simulator=True)
    await controller.start()
    await eventually(lambda: controller.synchronized)
    try:
        yield controller
    finally:
        await controller.close()


async def apply(client, document, expected=0):
    return await client.call("configuration.apply", {"document": document, "expectedRevision": expected, "operationId": str(uuid4())})


async def click(controller, key):
    controller.adapter.set_key(key, True)
    await asyncio.sleep(0.02)
    controller.adapter.set_key(key, False)
    await asyncio.sleep(0.1)


async def test_live_api_nested_navigation_and_back(controller):
    doc = demo_configuration()
    async with Client(controller.paths.socket) as client:
        response = await apply(client, doc)
        assert response["configurationApplied"]
        assert response["deviceSynchronized"] is False
        await eventually(lambda: controller.synchronized)
        await click(controller, 0)
        await eventually(lambda: controller.page_id == doc["pages"][1]["id"] and controller.synchronized)
        await click(controller, 1)
        await eventually(lambda: controller.page_id == doc["pages"][2]["id"] and controller.synchronized)
        await click(controller, 0)
        await eventually(lambda: controller.page_id == doc["pages"][1]["id"] and controller.synchronized)
        await click(controller, 2)
        await eventually(lambda: controller.page_id == doc["rootPageId"] and controller.synchronized)
        assert controller.execution.history.records == []
        preview = await client.call("render.preview")
        assert preview["mediaType"] == "image/png"
        assert controller.adapter.writes >= 32


async def test_held_press_during_apply_pause_and_navigation(controller):
    doc = demo_configuration()
    async with Client(controller.paths.socket) as client:
        await apply(client, doc)
        await eventually(lambda: controller.synchronized)
        controller.adapter.set_key(0, True)
        await asyncio.sleep(0.02)
        await client.call("runtime.navigate", {"pageId": doc["pages"][1]["id"], "expectedRevision": 1})
        await eventually(lambda: controller.synchronized)
        controller.adapter.set_key(0, False)
        await asyncio.sleep(0.1)
        assert controller.page_id == doc["pages"][1]["id"]
        await client.call("runtime.pause", {"paused": True})
        await click(controller, 0)
        assert controller.page_id == doc["pages"][1]["id"]
        await client.call("runtime.pause", {"paused": False})
        await eventually(lambda: controller.synchronized and controller.gate.enabled)
        await click(controller, 0)
        assert controller.page_id == doc["rootPageId"]


async def test_revision_conflict_and_deduplication(controller):
    doc = demo_configuration()
    operation = str(uuid4())
    params = {"document": doc, "expectedRevision": 0, "operationId": operation}
    async with Client(controller.paths.socket) as client:
        one = await client.call("configuration.apply", params)
        two = await client.call("configuration.apply", params)
        assert one["revision"] == two["revision"] == 1
        assert two["deduplicated"]
        with pytest.raises(SdlError) as failure:
            await apply(client, doc)
        assert failure.value.code == "REVISION_CONFLICT"
        params["document"] = copy.deepcopy(doc)
        params["document"]["name"] = "different"
        with pytest.raises(SdlError) as failure:
            await client.call("configuration.apply", params)
        assert failure.value.code == "OPERATION_CONFLICT"


async def test_disconnect_apply_reconnect_preserves_page(controller):
    doc = demo_configuration()
    async with Client(controller.paths.socket) as client:
        await apply(client, doc)
        await eventually(lambda: controller.synchronized)
        await click(controller, 0)
        page = controller.page_id
        await client.call("simulator.connection", {"connected": False})
        await eventually(lambda: controller.state == "disconnected")
        doc["settings"]["brightnessPercent"] = 70
        result = await apply(client, doc, 1)
        assert result["deviceSynchronized"] is False
        assert controller.page_id == page
        await client.call("simulator.connection", {"connected": True})
        await eventually(lambda: controller.synchronized)
        assert controller.page_id == page
        assert controller.adapter.percent == 70


async def test_executable_review_and_simulator_execution_disabled(controller, tmp_path):
    doc = demo_configuration()
    item = doc["pages"][0]["buttons"][1]
    item["action"] = {"type": "core.execute", "path": "/usr/bin/true", "arguments": [], "interpreter": None,
                      "workingDirectory": str(tmp_path), "environment": {}, "mode": "task", "timeoutMs": 1000,
                      "concurrency": "ignoreWhileRunning", "maxParallel": 1}
    async with Client(controller.paths.socket) as client:
        with pytest.raises(SdlError) as failure:
            await apply(client, doc)
        assert failure.value.code == "REVIEW_REQUIRED"
        review = await client.call("configuration.review", {"document": doc})
        assert len(review["actions"]) == 1
        await client.call("configuration.apply", {"document": doc, "expectedRevision": 0, "operationId": str(uuid4()),
                                                  "reviewToken": review["reviewToken"], "confirmed": True})
        assert not controller.execution.history.records
        await eventually(lambda: controller.synchronized)
        with pytest.raises(SdlError) as failure:
            await client.call("execution.test", {"buttonId": item["id"], "expectedRevision": 1, "confirmed": True, "operationId": str(uuid4())})
        assert failure.value.code == "EXECUTION_DISABLED"


async def test_image_import_over_ipc(controller):
    image = Image.new("RGBA", (45, 90), "green")
    data = io.BytesIO(); image.save(data, "PNG")
    async with Client(controller.paths.socket) as client:
        asset = await client.import_asset(data.getvalue(), "icon.jpg")
        assert await client.read_asset(asset["assetId"]) == data.getvalue()
        doc = demo_configuration()
        doc["pages"][0]["buttons"][1]["appearance"].update(iconAssetId=asset["assetId"], layout="iconAboveText")
        await apply(client, doc)
        await eventually(lambda: controller.synchronized)
        assert controller.adapter.images[1].getbbox()


async def test_event_order_and_no_plugin_host(controller):
    async with Client(controller.paths.socket) as client:
        subscribed = await client.call("events.subscribe", {"types": ["runtime.paused"]})
        async with Client(controller.paths.socket) as other:
            await other.call("runtime.pause", {"paused": True})
        event = await asyncio.wait_for(read_frame(client.reader), 2)
        assert event["method"] == "runtime.event"
        assert event["params"]["eventSequence"] > subscribed["eventSequence"]
        assert (await client.call("plugins.list"))["supported"] is False
        with pytest.raises(SdlError) as failure:
            await client.call("plugins.rescan")
        assert failure.value.code == "FEATURE_DEFERRED"


async def test_socket_permissions_duplicate_controller_and_malformed_frame(controller):
    assert controller.paths.socket.stat().st_mode & 0o777 == 0o600
    with pytest.raises(SdlError) as failure:
        Controller(controller.paths, simulator=True)
    assert failure.value.code == "ALREADY_RUNNING"
    reader, writer = await asyncio.open_unix_connection(str(controller.paths.socket))
    writer.write(struct.pack("!I", 17 * 1024 * 1024)); await writer.drain()
    assert await asyncio.wait_for(reader.read(), 2) == b""
    writer.close(); await writer.wait_closed()
    async with Client(controller.paths.socket) as client:
        assert (await client.call("system.snapshot"))["device"]["state"] == "ready"


def test_peer_uid_rejected():
    class Peer:
        def getsockopt(self, *args):
            return struct.pack("3i", 123, os.getuid() + 1, 123)
    assert not authorized_peer(Peer())


async def test_exported_result_shapes_against_running_controller(controller):
    import json
    from pathlib import Path
    from jsonschema import Draft202012Validator
    path = Path(__file__).resolve().parents[1] / 'schemas/local-api-envelopes-v1.json'
    schema = json.loads(path.read_text())
    calls = [
        ('system.hello', {'apiMajor':1,'apiMinor':0,'clientName':'contract-test'}),
        ('system.snapshot', {}), ('system.diagnostics', {}),
        ('configuration.get', {}), ('configuration.history', {}),
        ('configuration.validate', {'document':demo_configuration()}),
        ('configuration.review', {'document':demo_configuration()}),
        ('device.list', {}), ('runtime.pause', {'paused':False}),
        ('execution.list', {}), ('plugins.list', {}), ('render.preview', {}),
    ]
    async with Client(controller.paths.socket) as client:
        for method, params in calls:
            result = await client.call(method, params)
            Draft202012Validator(schema['$defs']['Result_'+method]).validate(result)
