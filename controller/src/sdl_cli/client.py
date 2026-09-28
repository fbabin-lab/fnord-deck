import asyncio
import base64
import hashlib
from pathlib import Path
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.limits import ASSET_BYTES, CHUNK_BYTES
from sdl_controller.ipc import authorized_peer, encode_frame, read_frame


class Client:
    def __init__(self, socket_path: Path) -> None:
        self.path = socket_path
        self.reader = self.writer = None

    async def __aenter__(self):
        try:
            self.reader, self.writer = await asyncio.open_unix_connection(str(self.path))
        except OSError as exc:
            raise SdlError("CONTROLLER_UNAVAILABLE", f"Controller is not available at {self.path}. Start it first.") from exc
        if not authorized_peer(self.writer.get_extra_info("socket")):
            self.writer.close()
            raise SdlError("UNSAFE_PEER", "Controller socket belongs to another UID.")
        await self.call("system.hello", {"apiMajor": 1, "apiMinor": 0, "clientName": "sdlctl"})
        return self

    async def __aexit__(self, *args):
        if self.writer:
            self.writer.close()
            await self.writer.wait_closed()

    async def call(self, method: str, params: dict | None = None) -> dict:
        identifier = str(uuid4())
        self.writer.write(encode_frame({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}}))
        await self.writer.drain()
        while True:
            response = await asyncio.wait_for(read_frame(self.reader), 45)
            if response.get("id") != identifier:
                if response.get("method") == "runtime.event":
                    continue
                raise SdlError("INVALID_RESPONSE", "Unexpected IPC response ID.")
            if "error" in response:
                error = response["error"]
                raise SdlError(error.get("data", {}).get("code", "RPC_ERROR"), error["message"], details=error.get("data", {}).get("details"))
            return response["result"]

    async def import_asset(self, raw: bytes, name: str) -> dict:
        if not raw or len(raw) > ASSET_BYTES:
            raise SdlError("ASSET_INVALID", "Image must be nonempty and at most 20 MiB.")
        upload = await self.call("asset.beginImport", {"name": name, "byteCount": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        for sequence, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
            await self.call("asset.writeChunk", {"uploadId": upload["uploadId"], "sequence": sequence,
                                                "data": base64.b64encode(raw[offset:offset + CHUNK_BYTES]).decode()})
        return await self.call("asset.finishImport", {"uploadId": upload["uploadId"]})

    async def read_asset(self, asset_id: str) -> bytes:
        value = bytearray()
        while True:
            chunk = await self.call("asset.readChunk", {"assetId": asset_id, "offset": len(value), "length": CHUNK_BYTES})
            decoded = base64.b64decode(chunk["data"], validate=True)
            if not decoded and len(value) < chunk["totalBytes"]:
                raise SdlError("ASSET_INVALID", "Asset read made no progress.")
            value.extend(decoded)
            if len(value) > ASSET_BYTES:
                raise SdlError("LIMIT_EXCEEDED", "Asset is too large.")
            if len(value) >= chunk["totalBytes"]:
                break
        if "sha256:" + hashlib.sha256(value).hexdigest() != asset_id:
            raise SdlError("ASSET_INVALID", "Exported asset hash mismatch.")
        return bytes(value)
