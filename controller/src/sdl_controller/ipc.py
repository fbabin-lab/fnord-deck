"""Same-UID, length-prefixed JSON-RPC, bounded clients and event queues."""
import asyncio
import contextlib
import os
import socket
import stat
import struct
from pathlib import Path

from sdl_core.api_contract import validate_params
from sdl_core.errors import SdlError
from sdl_core.jsonutil import dumps, loads
from sdl_core.limits import EVENT_QUEUE, FRAME_BYTES


async def read_frame(reader: asyncio.StreamReader) -> dict:
    header = await reader.readexactly(4)
    size = struct.unpack("!I", header)[0]
    if size == 0 or size > FRAME_BYTES:
        raise SdlError("LIMIT_EXCEEDED", "Invalid IPC frame length.")
    payload = await asyncio.wait_for(reader.readexactly(size), 30)
    value = loads(payload, FRAME_BYTES)
    if not isinstance(value, dict):
        raise SdlError("INVALID_JSON", "JSON-RPC batches and non-object frames are unsupported.")
    return value


def encode_frame(value: dict) -> bytes:
    payload = dumps(value)
    if len(payload) > FRAME_BYTES:
        raise SdlError("LIMIT_EXCEEDED", "Response exceeds frame limit.")
    return struct.pack("!I", len(payload)) + payload


def authorized_peer(connection) -> bool:
    if connection is None:
        return False
    _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    return uid == os.getuid()


class Connection:
    def __init__(self, server, reader, writer) -> None:
        self.server, self.reader, self.writer = server, reader, writer
        self.hello = False
        self.ids: set[str] = set()
        self.events: set[str] | None = None
        self.pending_events: set[str] | None = None
        self.queue: asyncio.Queue = asyncio.Queue(EVENT_QUEUE)
        self.uploads: dict = {}
        self.sender: asyncio.Task | None = None

    def send(self, message: dict) -> None:
        try:
            self.queue.put_nowait(message)
        except asyncio.QueueFull:
            self.writer.close()

    async def send_loop(self) -> None:
        try:
            while True:
                message = await self.queue.get()
                self.writer.write(encode_frame(message))
                await asyncio.wait_for(self.writer.drain(), 5)
        except (ConnectionError, asyncio.CancelledError, TimeoutError, SdlError):
            self.writer.close()

    async def run(self) -> None:
        if not authorized_peer(self.writer.get_extra_info("socket")):
            self.writer.close()
            return
        self.sender = asyncio.create_task(self.send_loop())
        try:
            while not self.writer.is_closing():
                request = await read_frame(self.reader)
                identifier = request.get("id")
                if request.get("jsonrpc") != "2.0" or not isinstance(request.get("method"), str):
                    raise SdlError("INVALID_JSON", "Invalid JSON-RPC envelope.")
                if not isinstance(identifier, str) or not identifier or len(identifier) > 128:
                    raise SdlError("INVALID_PARAMS", "Client requests require a nonempty string ID.")
                if identifier in self.ids or len(self.ids) >= 1024:
                    raise SdlError("INVALID_PARAMS", "Repeated request ID or connection request limit reached; reconnect.")
                self.ids.add(identifier)
                method = request["method"]
                try:
                    validate_params(method, request.get("params", {}))
                    if not self.hello and method != "system.hello":
                        raise SdlError("HELLO_REQUIRED", "Call system.hello before using the API.")
                    result = await self.server.handler(method, request.get("params", {}), self)
                    self.send({"jsonrpc": "2.0", "id": identifier, "result": result})
                    if self.pending_events is not None:
                        self.events, self.pending_events = self.pending_events, None
                except SdlError as exc:
                    number = -32601 if exc.code == "METHOD_NOT_FOUND" else -32602 if exc.code == "INVALID_PARAMS" else -32000
                    self.send({"jsonrpc": "2.0", "id": identifier, "error": exc.rpc(number)})
                except Exception:
                    self.send({"jsonrpc": "2.0", "id": identifier,
                               "error": SdlError("INTERNAL_ERROR", "Operation failed safely; no automatic action retry will occur.").rpc(-32603)})
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError, SdlError, asyncio.CancelledError):
            pass
        finally:
            for upload in self.uploads.values():
                upload["path"].unlink(missing_ok=True)
            if self.sender:
                self.sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.sender
            self.writer.close()
            with contextlib.suppress(Exception):
                await self.writer.wait_closed()
            self.server.clients.discard(self)


class LocalServer:
    def __init__(self, path: Path, handler) -> None:
        self.path, self.handler = path, handler
        self.clients: set[Connection] = set()
        self.tasks: set[asyncio.Task] = set()
        self.server = None
        self.owns_socket = False

    async def start(self) -> None:
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise SdlError("UNSAFE_PATH", "Refusing to replace a non-socket or foreign-owned IPC path.")
            self.path.unlink()
        self.server = await asyncio.start_unix_server(self.accept, path=str(self.path), limit=FRAME_BYTES + 4)
        self.owns_socket = True
        os.chmod(self.path, 0o600)

    async def accept(self, reader, writer) -> None:
        if len(self.clients) >= 16:
            writer.close()
            return
        connection = Connection(self, reader, writer)
        self.clients.add(connection)
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            await connection.run()
        finally:
            self.tasks.discard(task)

    def event(self, event: dict) -> None:
        for client in list(self.clients):
            if client.events is not None and (not client.events or event["type"] in client.events):
                client.send({"jsonrpc": "2.0", "method": "runtime.event", "params": event})

    async def close(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*list(self.tasks), return_exceptions=True)
        if self.owns_socket and self.path.exists():
            self.path.unlink()
        self.owns_socket = False
