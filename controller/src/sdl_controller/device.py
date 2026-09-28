"""Only this module imports the USB library or applies native JPEG/orientation transforms."""
import hashlib
import io
import os
from collections import OrderedDict
from pathlib import Path
from typing import Callable

from PIL import Image

from sdl_core.errors import SdlError
from sdl_core.model import Capabilities, XL


class SimulatorAdapter:
    capabilities = XL
    serial = "SIMULATOR-XL"

    def __init__(self) -> None:
        self.online = True
        self.opened = False
        self.callback = None
        self.states = [False] * XL.key_count
        self.images: dict[int, Image.Image] = {}
        self.writes = 0
        self.percent = 50

    def enumerate(self) -> list[dict]:
        return [{"deviceId": self.serial, "serialNumber": self.serial, "vendorId": 4057, "productId": 108, "model": "Stream Deck XL simulator"}] if self.online else []

    def connect(self, serial: str | None, callback: Callable) -> dict:
        if not self.online:
            raise SdlError("DEVICE_UNAVAILABLE", "Simulator is disconnected.")
        self.callback = callback
        self.opened = True
        callback(tuple(self.states))
        return {**self.enumerate()[0], "layout": self.capabilities.json(), "firmware": "simulator"}

    def connected(self) -> bool:
        return self.online and self.opened

    def write(self, key: int, image: Image.Image) -> None:
        if not self.connected():
            raise SdlError("DEVICE_UNAVAILABLE", "Simulator disconnected.")
        self.images[key] = image.copy()
        self.writes += 1

    def brightness(self, percent: int) -> None:
        self.percent = percent

    def close(self) -> None:
        self.opened = False
        self.callback = None
        self.images.clear()

    def set_key(self, key: int, down: bool) -> None:
        if not self.connected() or self.callback is None:
            raise SdlError("DEVICE_UNAVAILABLE", "Simulator is not connected.")
        self.states[key] = down
        self.callback(tuple(self.states))


class ElgatoAdapter:
    capabilities = XL
    serial: str | None = None

    def __init__(self) -> None:
        self.deck = None
        self.native_cache: OrderedDict[str, bytes] = OrderedDict()

    @staticmethod
    def _devices():
        try:
            from StreamDeck.DeviceManager import DeviceManager
            # The installed rule and declared system dependency are for this explicit backend.
            manager = DeviceManager(transport="libusb")
            return [d for d in manager.enumerate() if d.vendor_id() == 4057 and d.product_id() == 108]
        except ModuleNotFoundError as exc:
            raise SdlError("DEPENDENCY_MISSING", "Install the locked streamdeck package in this application's virtual environment.") from exc
        except Exception as exc:
            raise SdlError("HID_BACKEND_UNAVAILABLE", "Cannot load the HID backend. Install libhidapi-libusb0 and run sdlctl doctor.") from exc

    def enumerate(self) -> list[dict]:
        devices = self._devices()
        result = []
        for deck in devices:
            serial = self.serial if self.deck is not None and deck.id() == self.deck.id() else None
            result.append({"deviceId": deck.id(), "serialNumber": serial, "vendorId": deck.vendor_id(), "productId": deck.product_id(), "model": deck.deck_type()})
        return result

    @staticmethod
    def _translate(exc: Exception) -> SdlError:
        text = str(exc).lower()
        if isinstance(exc, PermissionError) or "permission" in text or "access denied" in text or "libusb_error_access" in text:
            return SdlError("DEVICE_PERMISSION_DENIED", "USB permission denied. Install the supplied udev rule and unplug/replug the deck.")
        if "busy" in text or "claim" in text or "libusb_error_busy" in text:
            return SdlError("DEVICE_IN_USE", "USB device is busy. Stop other Stream Deck controllers; no force takeover is attempted.")
        return SdlError("DEVICE_IO_ERROR", "USB access failed. Check cable, udev permissions and competing controllers; run sdlctl doctor.")

    def connect(self, serial: str | None, callback: Callable) -> dict:
        devices = self._devices()
        if not devices:
            raise SdlError("DEVICE_UNAVAILABLE", "No model 20GAT9901 (0fd9:006c) detected.")
        if serial is None and len(devices) > 1:
            raise SdlError("DEVICE_SELECTION_REQUIRED", "Multiple matching decks found. Disconnect all but the intended deck for initial selection, or set its serialNumber in the configuration.")
        from StreamDeck.Devices.StreamDeck import ControlType
        for deck in devices:
            try:
                # Pinned-library adaptation: observe full input reports, not only changed-key
                # callbacks. Cached all-false library startup values are NOT a released baseline.
                original_read = deck._read_control_states
                observed = {"selected": False, "states": None}
                def read_report(original=original_read, observed=observed):
                    report = original()
                    if report is not None and ControlType.KEY in report:
                        observed["states"] = tuple(bool(v) for v in report[ControlType.KEY])
                        if observed["selected"]:
                            callback(observed["states"])
                    return report
                deck._read_control_states = read_report
                deck.set_poll_frequency(100)
                deck.open()
                actual_serial = deck.get_serial_number()
                if serial is not None and serial != actual_serial:
                    deck.close()
                    continue
                rows, columns = deck.key_layout()
                width, height = deck.key_image_format()["size"]
                capabilities = Capabilities(rows, columns, width, height)
                if capabilities != XL or deck.key_count() != capabilities.key_count:
                    deck.close()
                    raise SdlError("LAYOUT_MISMATCH", "The device reports unexpected capabilities for model 20GAT9901.")
                observed["selected"] = True
                if observed["states"] is not None:
                    callback(observed["states"])
                self.deck, self.serial, self.capabilities = deck, actual_serial, capabilities
                return {"deviceId": deck.id(), "serialNumber": actual_serial,
                        "vendorId": deck.vendor_id(), "productId": deck.product_id(),
                        "model": deck.deck_type(), "layout": capabilities.json(), "firmware": deck.get_firmware_version()}
            except SdlError:
                raise
            except Exception as exc:
                try:
                    deck.close()
                except Exception:
                    pass
                raise self._translate(exc) from exc
        raise SdlError("DEVICE_UNAVAILABLE", "The selected serial number is not connected. Another deck will not be selected automatically.")

    def connected(self) -> bool:
        try:
            return self.deck is not None and self.deck.is_open() and self.deck.connected()
        except Exception:
            return False

    def brightness(self, percent: int) -> None:
        if self.deck is None:
            raise SdlError("DEVICE_UNAVAILABLE", "Device is disconnected.")
        try:
            self.deck.set_brightness(percent)
        except Exception as exc:
            raise self._translate(exc) from exc

    def write(self, key: int, image: Image.Image) -> None:
        if self.deck is None:
            raise SdlError("DEVICE_UNAVAILABLE", "Device is disconnected.")
        from StreamDeck.ImageHelpers import PILHelper
        cache_key = hashlib.sha256(image.tobytes()).hexdigest()
        try:
            native = self.native_cache.get(cache_key)
            if native is None:
                native = PILHelper.to_native_key_format(self.deck, image)
                self.native_cache[cache_key] = native
                if len(self.native_cache) > 1024:
                    self.native_cache.popitem(last=False)
            else:
                self.native_cache.move_to_end(cache_key)
            self.deck.set_key_image(key, native)
        except Exception as exc:
            raise self._translate(exc) from exc

    def close(self) -> None:
        deck, self.deck = self.deck, None
        self.native_cache.clear()
        if deck is not None:
            try:
                if deck.is_open():
                    deck.set_key_callback(None)
                    for key in range(deck.key_count()):
                        deck.set_key_image(key, None)
            except Exception:
                pass
            finally:
                try:
                    deck.close()
                except Exception:
                    pass
