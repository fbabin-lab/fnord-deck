"""Use cases at the editor/API boundary. Safe operations never invoke commands."""
from __future__ import annotations

import asyncio
import copy
import io
from pathlib import Path
from uuid import uuid4

from PIL import Image

from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, digest
from sdl_core.limits import ASSET_BYTES
from sdl_core.model import require_valid, validate
from sdl_core.render import Renderer

from .document import asset_ids
from .storage import Workspace, read_bounded
from .transport import Client


class Backend:
    def __init__(self, workspace: Workspace, client_factory=Client) -> None:
        self.workspace = workspace
        self.client_factory = client_factory
        self._render_lock = asyncio.Lock()
        self._apply_lock = asyncio.Lock()
        self._renderer = None

    def client(self):
        return self.client_factory(self.workspace.paths.socket)

    async def rpc(self, method: str, params: dict | None = None) -> dict:
        async with self.client() as client:
            return await client.call(method, params)

    async def snapshot(self) -> dict:
        return await self.rpc("system.snapshot")

    async def pull(self) -> dict:
        record = await self.rpc("configuration.get")
        require_valid(record["document"])
        for identifier in sorted(asset_ids(record["document"])):
            if self.workspace.assets.exists(identifier):
                await asyncio.to_thread(self.workspace.assets.verify, identifier)
                continue
            async with self.client() as client:
                raw = await client.read_asset(identifier)
            await self.workspace.assets.import_bytes(raw, identifier)
        return record

    async def prepare_apply(self, document: dict, expected: int | None) -> dict:
        require_valid(document, self.workspace.assets.exists)
        current = await self.rpc("configuration.get")
        revision = current["revision"]
        if expected is not None and revision != expected:
            raise SdlError("REVISION_CONFLICT", "Controller has a newer revision. Your draft was preserved.",
                           details={"expectedRevision": expected, "currentRevision": revision})
        # Originals are uploaded through the public API, never by editing Controller files.
        for identifier in sorted(asset_ids(document)):
            await asyncio.to_thread(self.workspace.assets.verify, identifier)
            async with self.client() as client:
                try:
                    await client.call("asset.readChunk", {"assetId": identifier, "offset": 0, "length": 1})
                    continue
                except SdlError as exc:
                    if exc.code not in {"ASSET_UNAVAILABLE", "ASSET_INVALID", "NOT_FOUND"}:
                        raise
            raw = await asyncio.to_thread(read_bounded, self.workspace.assets.directory(identifier) / "original", ASSET_BYTES)
            async with self.client() as client:
                result = await client.import_asset(raw, identifier)
            if result["assetId"] != identifier:
                raise SdlError("ASSET_INVALID", "Controller image identifier differs from the local original.")
        report = await self.rpc("configuration.validate", {"document": document})
        if report["errors"]:
            raise SdlError("VALIDATION_FAILED", "Controller rejected the draft.", details=report)
        review = await self.rpc("configuration.review", {"document": document})
        if review["contentHash"] != digest(document):
            raise SdlError("INVALID_RESPONSE", "Review does not match the draft.")
        return {"document": copy.deepcopy(document), "expectedRevision": revision,
                "operationId": str(uuid4()), "reviewToken": review["reviewToken"],
                "actions": review["actions"], "warnings": report["warnings"],
                "previousName": current["document"]["name"]}

    async def apply_reviewed(self, prepared: dict, *, confirmed: bool) -> dict:
        if confirmed is not True:
            raise SdlError("REVIEW_REQUIRED", "Explicit user confirmation is required before Apply.")
        async with self._apply_lock:
            if self.workspace.pending() is not None:
                raise SdlError("PENDING_APPLY", "Resolve or discard the previous pending Apply first.")
            params = {k: prepared[k] for k in ("document", "expectedRevision", "operationId", "reviewToken")}
            params["confirmed"] = True
            await self.workspace.remember_pending(params)
            try:
                result = await self.rpc("configuration.apply", params)
            except SdlError as exc:
                if exc.code in {"REVISION_CONFLICT", "REVIEW_REQUIRED", "VALIDATION_FAILED", "OPERATION_CONFLICT"}:
                    await self.workspace.clear_pending()  # These explicit server rejections did not commit.
                raise
            await self.workspace.clear_pending()
            return result

    async def resolve_pending(self) -> dict:
        async with self._apply_lock:
            pending = self.workspace.pending()
            if pending is None:
                raise SdlError("NO_PENDING_APPLY", "No interrupted Apply is recorded.")
            params = {k: pending[k] for k in ("document", "expectedRevision", "operationId")}
            # Deliberate user-requested replay. The Controller deduplicates before review.
            # Uncommitted executable content cannot be newly approved through this path.
            result = await self.rpc("configuration.apply", params)
            await self.workspace.clear_pending()
            return {"result": result, "document": pending["document"]}

    async def validate_local(self, document: dict) -> dict:
        return await asyncio.to_thread(validate, document, self.workspace.assets.exists)

    async def render(self, document: dict, page_id: str) -> list[bytes]:
        async with self._render_lock:
            def draw():
                if self._renderer is None:
                    self._renderer = Renderer(self.workspace.assets)
                frames = []
                for image in self._renderer.page(document, page_id):
                    output = io.BytesIO()
                    image.save(output, "PNG")
                    frames.append(output.getvalue())
                return frames
            return await asyncio.to_thread(draw)

    async def export_preview(self, path: Path, document: dict, page_id: str) -> None:
        frames = await self.render(document, page_id)
        layout = document["layout"]
        w, h = layout["keyWidth"], layout["keyHeight"]
        grid = Image.new("RGB", (layout["columns"] * w, layout["rows"] * h), "black")
        for i, raw in enumerate(frames):
            with Image.open(io.BytesIO(raw)) as image:
                grid.paste(image, ((i % layout["columns"]) * w, (i // layout["columns"]) * h))
        output = io.BytesIO()
        grid.save(output, "PNG")
        await asyncio.to_thread(atomic_write, path, output.getvalue())

    async def prepare_test(self, document: dict, button_id: str) -> dict:
        current = await self.rpc("configuration.get")
        if digest(document) != digest(current["document"]):
            raise SdlError("DRAFT_NOT_APPLIED", "Apply this exact draft before testing an action.")
        item = next((b for p in current["document"]["pages"] for b in p["buttons"] if b["id"] == button_id), None)
        if item is None or item["action"]["type"] != "core.execute" or not item["enabled"]:
            raise SdlError("INVALID_ACTION", "Select an enabled, applied executable action.")
        return {"buttonId": button_id, "expectedRevision": current["revision"],
                "operationId": str(uuid4()), "action": copy.deepcopy(item["action"])}

    async def execute_confirmed(self, prepared: dict, *, confirmed: bool) -> dict:
        if confirmed is not True:
            raise SdlError("REVIEW_REQUIRED", "Testing executes a program and requires explicit confirmation.")
        params = {k: prepared[k] for k in ("buttonId", "expectedRevision", "operationId")}
        params["confirmed"] = True
        # This is the only execution RPC in the Configurator. It is never retried.
        return await self.rpc("execution.test", params)
