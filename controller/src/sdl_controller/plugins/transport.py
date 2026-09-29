"""Bounded JSONL RPC with one reader, explicit deadlines, and no implicit retries."""
from __future__ import annotations

import asyncio
import contextlib
import time

from sdl_core.errors import SdlError
from sdl_core.jsonutil import dumps, loads

FRAME = 262144


class Budget:
    def __init__(self, rate: float, burst: float, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens, self.updated = burst, clock()

    def take(self, count: float = 1) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + max(0, now - self.updated) * self.rate)
        self.updated = now
        if count > self.tokens:
            return False
        self.tokens -= count
        return True


class Wire:
    def __init__(self, process, wake):
        self.process, self.wake = process, wake
        self.pending = {}
        self.number = 0
        self.fatal: SdlError | None = None
        self.stderr = bytearray()
        self.stderr_dropped = 0
        self.closed = False
        self.reader = asyncio.create_task(self._read(), name="plugin-jsonl")
        self.logger = asyncio.create_task(self._stderr(), name="plugin-stderr")

    def fail(self, code: str) -> None:
        if self.fatal is None:
            self.fatal = SdlError(code, "Plugin transport stopped; inspect plugin status.")
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(self.fatal)
        self.wake()

    async def _read(self):
        messages, data = Budget(64, 128), Budget(512 * 1024, 512 * 1024)
        try:
            while not self.closed:
                raw = await self.process.stdout.readline()
                if not raw:
                    self.fail("PLUGIN_EXITED")
                    return
                if not raw.endswith(b"\n") or len(raw) > FRAME or not messages.take() or not data.take(len(raw)):
                    self.fail("PLUGIN_OUTPUT_LIMIT")
                    return
                value = loads(raw)
                if not isinstance(value, dict) or value.get("jsonrpc") != "2.0" or set(value) not in ({"jsonrpc", "id", "result"}, {"jsonrpc", "id", "error"}):
                    self.fail("PLUGIN_PROTOCOL_INVALID")
                    return
                identifier = value.get("id")
                if not isinstance(identifier, str) or identifier not in self.pending:
                    self.fail("PLUGIN_UNSOLICITED_RESPONSE")
                    return
                future = self.pending.pop(identifier)
                if future.done():
                    continue
                if "error" in value:
                    error = value["error"]
                    if not isinstance(error, dict) or type(error.get("code")) is not int:
                        self.fail("PLUGIN_PROTOCOL_INVALID")
                        future.set_exception(self.fatal)
                        return
                    future.set_exception(SdlError("PLUGIN_RPC_ERROR", "Plugin rejected a request.",
                                                   details={"rpcCode": error["code"]}))
                else:
                    future.set_result(value["result"])
        except asyncio.CancelledError:
            pass
        except (OSError, ValueError, SdlError, RecursionError):
            self.fail("PLUGIN_PROTOCOL_INVALID")

    async def _stderr(self):
        # Drain even discarded output; never let a full pipe deadlock navigation.
        budget = Budget(4096, 65536)
        try:
            while not self.closed:
                chunk = await self.process.stderr.read(4096)
                if not chunk:
                    return
                if not budget.take(len(chunk)):
                    self.stderr_dropped += len(chunk)
                    self.fail("PLUGIN_LOG_FLOOD")
                    return
                self.stderr.extend(chunk)
                if len(self.stderr) > 65536:
                    self.stderr_dropped += len(self.stderr) - 65536
                    del self.stderr[:-65536]
        except (asyncio.CancelledError, OSError):
            pass

    async def request(self, method: str, params: dict, timeout: float = .5):
        if self.fatal or self.closed:
            raise self.fatal or SdlError("PLUGIN_STOPPED", "Plugin worker is stopped.")
        if len(self.pending) >= 2:
            raise SdlError("PLUGIN_BUSY", "Plugin control queue is full.")
        self.number += 1
        identifier = f"h-{self.number}"
        raw = dumps({"jsonrpc": "2.0", "id": identifier, "method": method, "params": params}) + b"\n"
        if len(raw) > FRAME:
            raise SdlError("PLUGIN_INPUT_LIMIT", "Plugin request exceeds the frame limit.")
        future = asyncio.get_running_loop().create_future()
        self.pending[identifier] = future
        try:
            async with asyncio.timeout(timeout):
                self.process.stdin.write(raw)
                await self.process.stdin.drain()
                return await future
        except TimeoutError as exc:
            self.fail("PLUGIN_TIMEOUT")
            raise SdlError("PLUGIN_TIMEOUT", "Plugin missed its request deadline.") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            self.fail("PLUGIN_EXITED")
            raise self.fatal from exc
        finally:
            self.pending.pop(identifier, None)
            if future.done() and not future.cancelled():
                # Retrieve an exception when write/drain failed before awaiting it.
                with contextlib.suppress(BaseException):
                    future.exception()
            elif not future.done():
                future.cancel()

    async def close(self):
        self.closed = True
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()
        for task in (self.reader, self.logger):
            task.cancel()
        await asyncio.gather(self.reader, self.logger, return_exceptions=True)
