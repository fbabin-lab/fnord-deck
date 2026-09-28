"""Narrow integration seams; future plugins must not acquire the USB device."""
from collections.abc import Callable
from typing import Protocol

from PIL.Image import Image

from .model import Capabilities


class DeviceAdapter(Protocol):
    capabilities: Capabilities
    serial: str | None

    def enumerate(self) -> list[dict]: ...
    def connect(self, serial: str | None, callback: Callable[[tuple[bool, ...]], None]) -> dict: ...
    def connected(self) -> bool: ...
    def write(self, key: int, image: Image) -> None: ...
    def brightness(self, percent: int) -> None: ...
    def close(self) -> None: ...


class RenderService(Protocol):
    def page(self, document: dict, page_id: str, overlays: dict | None = None) -> list[Image]: ...


class ExtensionHost(Protocol):
    """Future boundary only. No discovery/loading/processes/secrets in this release."""
    def status(self) -> dict: ...
    async def invoke(self, button: dict) -> None: ...


class DeferredExtensions:
    def status(self) -> dict:
        return {"supported": False, "reason": "Plugins deferred", "instances": []}

    async def invoke(self, button: dict) -> None:
        from .errors import SdlError
        raise SdlError("PLUGIN_DEFERRED", "Plugin actions are not implemented in this release.")
