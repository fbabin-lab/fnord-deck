"""Contract tests against a fake of the pinned library API, NOT physical USB verification."""
import sys
import types

import pytest
from PIL import Image

from sdl_core.errors import SdlError
from sdl_controller.device import ElgatoAdapter


class Deck:
    def __init__(self, serial="chosen"):
        self.serial = serial
        self.opened = False
        self.closed = False
        self.written = []
        self.report = {"keys": [False] * 32}

    def vendor_id(self): return 4057
    def product_id(self): return 108
    def id(self): return "usb-path-" + self.serial
    def deck_type(self): return "Stream Deck XL"
    def _read_control_states(self): return self.report
    def set_poll_frequency(self, hz): self.poll = hz
    def open(self): self.opened = True; self._read_control_states()
    def close(self): self.opened = False; self.closed = True
    def get_serial_number(self): return self.serial
    def key_layout(self): return (4, 8)
    def key_image_format(self): return {"size": (96, 96), "format": "JPEG", "flip": (True, True), "rotation": 0}
    def key_count(self): return 32
    def get_firmware_version(self): return "fake-only"
    def connected(self): return self.opened
    def is_open(self): return self.opened
    def set_key_image(self, key, data): self.written.append((key, data))
    def set_brightness(self, value): self.percent = value
    def set_key_callback(self, callback): pass


@pytest.fixture
def library(monkeypatch):
    deck = Deck()
    selected_transport = []
    class Manager:
        def __init__(self, transport): selected_transport.append(transport)
        def enumerate(self): return [deck]
    device_manager = types.ModuleType("StreamDeck.DeviceManager")
    device_manager.DeviceManager = Manager
    base = types.ModuleType("StreamDeck.Devices.StreamDeck")
    base.ControlType = types.SimpleNamespace(KEY="keys")
    images = types.ModuleType("StreamDeck.ImageHelpers")
    calls = []
    def native(device, image):
        calls.append(image.copy())
        return b"native-JPEG-from-helper"
    images.PILHelper = types.SimpleNamespace(to_native_key_format=native)
    for name, module in (("StreamDeck.DeviceManager", device_manager), ("StreamDeck.Devices.StreamDeck", base), ("StreamDeck.ImageHelpers", images)):
        monkeypatch.setitem(sys.modules, name, module)
    return deck, selected_transport, calls


def test_library_transport_native_helper_and_full_reports(library):
    deck, selected_transport, native = library
    adapter = ElgatoAdapter()
    reports = []
    info = adapter.connect(None, reports.append)
    assert selected_transport == ["libusb"]
    assert info["serialNumber"] == "chosen"
    assert reports == [(False,) * 32]
    image = Image.new("RGB", (96, 96))
    image.putpixel((0, 0), (200, 30, 20))
    adapter.write(0, image); adapter.write(1, image)
    assert len(native) == 1
    assert native[0].getpixel((0, 0)) == (200, 30, 20), "Controller must not pre-rotate"
    assert deck.written[:2] == [(0, b"native-JPEG-from-helper"), (1, b"native-JPEG-from-helper")]
    adapter.brightness(70)
    assert deck.percent == 70
    adapter.close()
    assert deck.closed


def test_serial_mismatch_does_not_emit_other_device_input(library):
    deck, _, _ = library
    adapter = ElgatoAdapter()
    reports = []
    with pytest.raises(SdlError) as error:
        adapter.connect("another serial", reports.append)
    assert error.value.code == "DEVICE_UNAVAILABLE"
    assert reports == []
    assert deck.closed


def test_multiple_decks_require_selection(monkeypatch):
    adapter = ElgatoAdapter()
    one, two = Deck("one"), Deck("two")
    monkeypatch.setattr(adapter, "_devices", lambda: [one, two])
    with pytest.raises(SdlError) as error:
        adapter.connect(None, lambda states: None)
    assert error.value.code == "DEVICE_SELECTION_REQUIRED"
    assert not one.opened and not two.opened
