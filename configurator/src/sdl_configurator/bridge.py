"""One asyncio worker thread; Qt objects remain on the GUI thread."""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from uuid import uuid4

from PySide6.QtCore import QObject, Signal, Slot

from sdl_core.errors import SdlError


class Bridge(QObject):
    completed = Signal(str, object)
    failed = Signal(str, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.loop = asyncio.new_event_loop()
        self.callbacks: dict[str, tuple[Callable, Callable]] = {}
        self.futures: dict = {}
        self._closed = False
        self.thread = threading.Thread(target=self._run, name="configurator-io", daemon=True)
        self.completed.connect(self._done)
        self.failed.connect(self._failed)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()
        self.loop.close()

    def submit(self, coroutine: Coroutine, success: Callable, failure: Callable) -> str:
        if self._closed:
            coroutine.close()
            raise RuntimeError("Configurator worker is closed.")
        identifier = str(uuid4())
        self.callbacks[identifier] = (success, failure)
        future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        self.futures[identifier] = future

        def done(f):
            if self._closed:
                return
            try:
                result = f.result()
            except BaseException as exc:
                if isinstance(exc, SdlError):
                    error = {"code": exc.code, "message": exc.message, "details": exc.details}
                else:
                    error = {"code": "LOCAL_ERROR", "message": str(exc)[:1500] or type(exc).__name__, "details": None}
                self.failed.emit(identifier, error)
            else:
                self.completed.emit(identifier, result)
        future.add_done_callback(done)
        return identifier

    @Slot(str, object)
    def _done(self, identifier: str, value) -> None:
        callbacks = self.callbacks.pop(identifier, None)
        self.futures.pop(identifier, None)
        if callbacks:
            callbacks[0](value)

    @Slot(str, object)
    def _failed(self, identifier: str, error) -> None:
        callbacks = self.callbacks.pop(identifier, None)
        self.futures.pop(identifier, None)
        if callbacks:
            callbacks[1](error)

    def close(self) -> None:
        self._closed = True
        self.callbacks.clear()
        async def shutdown():
            current = asyncio.current_task()
            tasks = [task for task in asyncio.all_tasks() if task is not current]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await self.loop.shutdown_asyncgens()
            await self.loop.shutdown_default_executor()
        future = asyncio.run_coroutine_threadsafe(shutdown(), self.loop)
        # GUI closes only after its last draft write; subprocess conversion cancellation
        # is implemented by the shared asset store. Never stop the Controller here.
        try:
            future.result(timeout=8)
        except Exception:
            pass
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=2)
