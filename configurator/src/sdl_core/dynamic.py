"""Pure plugin display validation/composition. Never interprets expressions or paths."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import math
import re
import struct

from PIL import Image

from .errors import SdlError

DEFAULT_DYNAMIC = {"enabled": True, "textTemplate": "{{label}}\n{{value}}{{unit}}",
                   "allowedOverrides": ["text", "progress"], "numberDecimals": 0, "staleAfterMs": 10000}
TOKENS = {"label", "value", "unit", "status"}


def invalid(message="Invalid plugin display state."):
    raise SdlError("PLUGIN_DISPLAY_INVALID", message)


def validate_dynamic(value: dict) -> None:
    if not isinstance(value, dict) or set(value) - set(DEFAULT_DYNAMIC):
        invalid("Unknown dynamic display option.")
    if type(value.get("enabled")) is not bool:
        invalid("Dynamic display enabled must be Boolean.")
    template = value.get("textTemplate", DEFAULT_DYNAMIC["textTemplate"])
    if not isinstance(template, str) or len(template) > 512 or "\x00" in template:
        invalid("Invalid dynamic text template.")
    stripped = re.sub(r"\{\{(label|value|unit|status)\}\}", "", template)
    if "{{" in stripped or "}}" in stripped:
        invalid("Templates accept only label, value, unit, and status substitutions.")
    allowed = value.get("allowedOverrides", ["text", "progress"])
    if not isinstance(allowed, list) or any(x not in {"text", "icon", "progress"} for x in allowed) or len(set(allowed)) != len(allowed):
        invalid("Unsupported dynamic display override.")
    if type(value.get("numberDecimals", 0)) is not int or not 0 <= value.get("numberDecimals", 0) <= 3:
        invalid("Display precision must be between zero and three decimals.")
    if type(value.get("staleAfterMs", 10000)) is not int or not 1000 <= value.get("staleAfterMs", 10000) <= 3600000:
        invalid("Invalid stale-data interval.")


def validate_state(state: dict, assets: set[str] = frozenset()) -> dict:
    if not isinstance(state, dict) or set(state) != {"value", "unit", "label", "status", "message", "progress", "image"}:
        invalid()
    value = state["value"]
    if value is not None and not (isinstance(value, str) and len(value) <= 256 or type(value) in (int, float) and math.isfinite(value) and abs(value) <= 1e100):
        invalid()
    for field, maximum in (("unit", 32), ("label", 128), ("message", 512)):
        text = state[field]
        if field == "message" and text is None:
            continue
        if not isinstance(text, str) or len(text) > maximum or any(ord(c) < 32 and c != "\n" for c in text):
            invalid()
    if not isinstance(state["status"], str) or state["status"] not in {"ok", "warning", "error", "unavailable"}:
        invalid()
    progress = state["progress"]
    if progress is not None and not (type(progress) in (int, float) and math.isfinite(progress) and 0 <= progress <= 1):
        invalid()
    image = state["image"]
    if image is not None:
        if not isinstance(image, dict):
            invalid()
        if image.get("kind") == "asset":
            if set(image) != {"kind", "assetKey"} or not isinstance(image["assetKey"], str) or image["assetKey"] not in assets:
                invalid("Image refers to an undeclared asset.")
        elif image.get("kind") == "png":
            if set(image) != {"kind", "dataBase64"} or not isinstance(image["dataBase64"], str) or len(image["dataBase64"]) > 174764:
                invalid("Dynamic PNG exceeds its byte limit.")
        else:
            invalid("Only declared assets and bounded inline PNGs are accepted.")
    return state


def decode_png(raw: bytes) -> Image.Image:
    """Called only on the rendering executor, after scalar checks and coalescing."""
    if len(raw) > 131072 or len(raw) < 33 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR":
        invalid("Invalid or oversized dynamic PNG.")
    width, height = struct.unpack(">II", raw[16:24])
    if not width or not height or width * height > 1000000:
        invalid("Dynamic PNG exceeds one million pixels.")
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
                invalid("Animated dynamic PNG is not supported.")
            source.load()
            return source.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise SdlError("PLUGIN_DISPLAY_INVALID", "Dynamic PNG could not be decoded.") from exc


def visual(item: dict, state: dict | None, width: int) -> dict | None:
    """Effective, rounded output key: freshness/messages do not force repaints."""
    policy = item["appearance"].get("dynamic")
    if not policy or not policy.get("enabled") or state is None or not item["enabled"]:
        return None
    policy = {**DEFAULT_DYNAMIC, **policy}
    allowed = policy["allowedOverrides"]
    good = state["status"] in {"ok", "warning"}
    result = {"text": None, "image": None, "progressPixels": None, "marker": state["status"]}
    if "text" in allowed and good and state["value"] is not None:
        value = state["value"]
        if type(value) in (int, float):
            value = f"{value:.{policy['numberDecimals']}f}"
        substitutions = {"label": state["label"], "value": str(value), "unit": state["unit"], "status": state["status"]}
        result["text"] = re.sub(r"\{\{(label|value|unit|status)\}\}", lambda m: substitutions[m[1]], policy["textTemplate"])[:1024]
    elif "text" in allowed and state.get("_lastGood"):
        previous = visual(item, state["_lastGood"], width)
        if previous:
            result.update(previous)
            result["marker"] = state["status"]
    if good and "progress" in allowed and state["progress"] is not None:
        result["progressPixels"] = round(state["progress"] * max(1, width - 4))
    if good and "icon" in allowed and state["image"]:
        image = state["image"]
        result["image"] = image
    return result


def image_for(visual_state: dict | None) -> Image.Image | None:
    if not visual_state or not visual_state["image"]:
        return None
    image = visual_state["image"]
    if image["kind"] != "png":
        invalid("Host did not resolve a declared asset.")
    try:
        raw = base64.b64decode(image["dataBase64"], validate=True)
    except ValueError as exc:
        raise SdlError("PLUGIN_DISPLAY_INVALID", "Invalid PNG base64.") from exc
    return decode_png(raw)
