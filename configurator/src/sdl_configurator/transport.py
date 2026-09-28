"""Bounded, same-UID, framed JSON-RPC client for Controller API 1.0.

No dependency on sdl_controller, USB libraries, a shell, or GUI widgets. Connections
are deliberately short-lived. Asset transfers keep one connection per asset.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import os
import socket
import struct
from pathlib import Path
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.jsonutil import dumps, loads
from sdl_core.limits import ASSET_BYTES, CHUNK_BYTES, FRAME_BYTES


class Client:
    def __init__(self, path: Path, *, timeout: float = 45) -> None:
        self.path, self.timeout = path, timeout
        self.reader = self.writer = None
        self.hello: dict = {}

    async def __aenter__(self) -> "Client":
        try:
            self.reader, self.writer = await asyncio.wait_for(asyncio.open_unix_connection(str(self.path)), 3)
            peer = self.writer.get_extra_info("socket")
            credentials = peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _, uid, _ = struct.unpack("3i", credentials)
            if uid != os.getuid():
                raise SdlError("UNSAFE_PEER", "Controller belongs to a different user.")
            self.hello = await self.call("system.hello", {"apiMajor": 1, "apiMinor": 0,
                                                          "clientName": "sdl-configurator"})
            if self.hello.get("apiMajor") != 1:
                raise SdlError("UNSUPPORTED_VERSION", "Controller API major version 1 is required.")
            return self
        except BaseException as exc:
            await self.close()
            if isinstance(exc, (OSError, TimeoutError)):
                raise SdlError("CONTROLLER_UNAVAILABLE", f"Controller is unavailable at {self.path}.") from exc
            raise

    async def close(self) -> None:
        if self.writer:
            self.writer.close()
            try:
                await self.writer.wait_closed()
            except (OSError, ConnectionError):
                pass
            self.writer = None

    async def __aexit__(self, *args) -> None:
        await self.close()

    async def call(self, method: str, params: dict | None = None) -> dict:
        if self.writer is None:
            raise SdlError("CONTROLLER_UNAVAILABLE", "Controller connection is not open.")
        identifier = str(uuid4())
        raw = dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params or {}})
        if not 0 < len(raw) <= FRAME_BYTES:
            raise SdlError("LIMIT_EXCEEDED", "API request exceeds frame limit.")
        try:
            async with asyncio.timeout(self.timeout):
                self.writer.write(struct.pack(">I", len(raw)) + raw)
                await self.writer.drain()
                size, = struct.unpack(">I", await self.reader.readexactly(4))
                if not 0 < size <= FRAME_BYTES:
                    raise SdlError("INVALID_RESPONSE", "Controller returned an invalid frame size.")
                response = loads(await self.reader.readexactly(size), FRAME_BYTES)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise SdlError("CONNECTION_LOST", "Connection was interrupted. A write may already have completed.") from exc
        if not isinstance(response, dict) or response.get("jsonrpc") != "2.0" or response.get("id") != identifier:
            raise SdlError("INVALID_RESPONSE", "Controller response ID or envelope is invalid.")
        if "error" in response:
            error = response["error"]
            if not isinstance(error, dict):
                raise SdlError("INVALID_RESPONSE", "Invalid error response.")
            data = error.get("data", {})
            if not isinstance(data, dict):
                raise SdlError("INVALID_RESPONSE", "Invalid error details object.")
            raise SdlError(data.get("code", "RPC_ERROR"), error.get("message", "Controller rejected the request."),
                           details=data.get("details"))
        if not isinstance(response.get("result"), dict):
            raise SdlError("INVALID_RESPONSE", "Controller response has no object result.")
        return response["result"]

    async def import_asset(self, raw: bytes, name: str) -> dict:
        if not raw or len(raw) > ASSET_BYTES:
            raise SdlError("ASSET_INVALID", "Image must be nonempty and at most 20 MiB.")
        upload = await self.call("asset.beginImport", {"name": name[:256], "byteCount": len(raw),
                                                        "sha256": hashlib.sha256(raw).hexdigest()})
        for sequence, offset in enumerate(range(0, len(raw), CHUNK_BYTES)):
            await self.call("asset.writeChunk", {"uploadId": upload["uploadId"], "sequence": sequence,
                                                  "data": base64.b64encode(raw[offset:offset + CHUNK_BYTES]).decode()})
        return await self.call("asset.finishImport", {"uploadId": upload["uploadId"]})

    async def read_asset(self, identifier: str) -> bytes:
        result = bytearray()
        expected_size = None
        while True:
            chunk = await self.call("asset.readChunk", {"assetId": identifier, "offset": len(result), "length": CHUNK_BYTES})
            total = chunk.get("totalBytes")
            if type(total) is not int or not 0 < total <= ASSET_BYTES or expected_size not in (None, total):
                raise SdlError("ASSET_INVALID", "Invalid or inconsistent asset size.")
            expected_size = total
            try:
                raw = base64.b64decode(chunk["data"], validate=True)
            except (ValueError, TypeError, KeyError) as exc:
                raise SdlError("ASSET_INVALID", "Invalid encoded asset data.") from exc
            if not raw or len(raw) > CHUNK_BYTES or len(result) + len(raw) > total:
                raise SdlError("ASSET_INVALID", "Invalid asset chunk or no progress.")
            result.extend(raw)
            if len(result) == total:
                break
        if "sha256:" + hashlib.sha256(result).hexdigest() != identifier:
            raise SdlError("ASSET_INVALID", "Asset checksum does not match its content ID.")
        return bytes(result)
