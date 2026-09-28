"""Bounded cross-thread mailbox and release-triggered input state machine."""
import asyncio
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class InputContext:
    epoch: int
    generation: int
    revision: int
    page_id: str


@dataclass(frozen=True)
class InputRecord:
    context: InputContext
    states: tuple[bool, ...]
    timestamp: float


class Mailbox:
    """One scheduled drain, not one unbounded asyncio callback for each USB event."""
    def __init__(self, loop: asyncio.AbstractEventLoop, handler: Callable, overflow: Callable, capacity: int = 128) -> None:
        self.loop, self.handler, self.overflow = loop, handler, overflow
        self.capacity = capacity
        self.lock = threading.Lock()
        self.items: deque[InputRecord] = deque()
        self.scheduled = self.lost = self.closed = False

    def push(self, record: InputRecord) -> None:
        with self.lock:
            if self.closed:
                return
            if len(self.items) >= self.capacity:
                self.items.clear()
                self.lost = True
            self.items.append(record)
            if not self.scheduled:
                self.scheduled = True
                try:
                    self.loop.call_soon_threadsafe(self.drain)
                except RuntimeError:
                    self.closed = True

    def drain(self) -> None:
        with self.lock:
            items = list(self.items)
            lost = self.lost
            self.items.clear()
            self.scheduled = self.lost = False
        if lost:
            self.overflow()
            # Dropped reports imply unknown input; the newest report only establishes a baseline.
            if items:
                self.handler(items[-1], baseline_only=True)
            return
        for record in items:
            self.handler(record)

    def close(self) -> None:
        with self.lock:
            self.closed = True
            self.items.clear()


class InputGate:
    def __init__(self, count: int) -> None:
        self.count = count
        self.context = InputContext(0, 0, 0, "")
        self.states: tuple[bool, ...] | None = None
        self.armed = [False] * count
        self.presses: dict[int, tuple[InputContext, str]] = {}
        self.accept_after = float("inf")
        self.enabled = False

    def reset(self, context: InputContext, *, disconnect: bool = False) -> None:
        self.context = context
        self.enabled = False
        self.accept_after = float("inf")
        self.presses.clear()
        self.armed = [False] * self.count
        if disconnect:
            self.states = None

    def enable(self) -> None:
        self.enabled = True
        self.accept_after = time.monotonic()
        self.presses.clear()
        # Only actual previously observed released states can arm a key.
        self.armed = [not s for s in self.states] if self.states is not None else [False] * self.count

    def accept(self, record: InputRecord, identities: dict[int, str], *, baseline_only: bool = False) -> list[tuple[int, str]]:
        if len(record.states) != self.count or record.context.epoch != self.context.epoch:
            return []
        old = self.states
        self.states = record.states
        if baseline_only:
            self.presses.clear()
            self.armed = [not s for s in record.states]
            return []
        if record.context != self.context or record.timestamp < self.accept_after or not self.enabled:
            self.presses.clear()
            self.armed = [not s for s in record.states]
            return []
        if old is None:
            self.armed = [not s for s in record.states]
            return []
        releases = []
        for key, (previous, current) in enumerate(zip(old, record.states, strict=True)):
            if current == previous:
                continue
            if current:
                identity = identities.get(key)
                if self.armed[key] and identity is not None:
                    self.presses[key] = (self.context, identity)
                self.armed[key] = False
            else:
                press = self.presses.pop(key, None)
                self.armed[key] = True
                if press and press[0] == self.context and identities.get(key) == press[1]:
                    releases.append((key, press[1]))
        return releases
