"""Upright logical rendering shared with the future Configurator. No native USB transforms."""
from collections import OrderedDict
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .errors import SdlError
from .jsonutil import digest
from .limits import RENDER_CACHE_BYTES
from .model import DEFAULT_APPEARANCE, Capabilities

FONT_ROOTS = [Path("/usr/share/fonts/truetype/dejavu"), Path("/usr/share/fonts/truetype/liberation2")]


def font_path(bold: bool = False) -> Path:
    names = ["DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"]
    for root, name in zip(FONT_ROOTS, names, strict=True):
        if (root / name).is_file():
            return root / name
    raise SdlError("FONT_UNAVAILABLE", "Install fonts-dejavu-core (or fonts-liberation2).")


def _lines(text: str, font, width: int) -> list[str]:
    lines = []
    for paragraph in text.split("\n"):
        line = ""
        for char in paragraph:
            trial = line + char
            if line and font.getlength(trial) > width:
                lines.append(line.rstrip())
                line = char.lstrip()
            else:
                line = trial
        lines.append(line)
    return lines or [""]


def _text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], appearance: dict) -> None:
    x, y, width, height = box
    text = appearance["text"]
    if not text or width < 1 or height < 1:
        return
    path = font_path(appearance["fontWeight"] == "bold")
    for size in range(appearance["fontSizePx"], 7, -1):
        font = ImageFont.truetype(str(path), size)
        lines = _lines(text, font, width)
        step = size + 2
        if len(lines) * step <= height:
            break
    capacity = max(1, height // step)
    if len(lines) > capacity:
        lines = lines[:capacity]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last + "…"
    top = y + max(0, (height - len(lines) * step) // 2)
    for index, line in enumerate(lines):
        length = font.getlength(line)
        align = appearance["textAlign"]
        left = x if align == "left" else x + (width - length if align == "right" else (width - length) / 2)
        draw.text((left, top + index * step), line, font=font, fill=appearance["textColor"], anchor="lt")


class Renderer:
    def __init__(self, assets) -> None:
        self.assets = assets
        self.cache: OrderedDict[str, bytes] = OrderedDict()
        self.cache_bytes = 0
        self.font_identity = [(str(font_path(b)), font_path(b).stat().st_mtime_ns) for b in (False, True)]

    def render(self, item: dict | None, capabilities: Capabilities, *, back: bool = False,
               locale: str = "en", overlay: str | None = None) -> Image.Image:
        key = digest({"button": item, "back": back, "locale": locale, "overlay": overlay,
                      "layout": capabilities.json(), "font": self.font_identity, "renderer": 1})
        size = (capabilities.keyWidth, capabilities.keyHeight)
        if key in self.cache:
            self.cache.move_to_end(key)
            return Image.frombytes("RGB", size, self.cache[key])
        if back:
            appearance = {**DEFAULT_APPEARANCE, "text": "Retour" if locale == "fr" else "Back", "layout": "textOnly"}
            item = {"appearance": appearance, "action": {"type": "core.none"}, "enabled": True}
        if item is None:
            image = Image.new("RGB", size, "black")
        else:
            appearance = item["appearance"]
            image = Image.new("RGBA", size, appearance["backgroundColor"])
            pad = min(appearance["paddingPx"], min(size) // 4)
            x, y, width, height = pad, pad, size[0] - 2 * pad, size[1] - 2 * pad
            icon = appearance["iconAssetId"]
            text = appearance["text"]
            layout = appearance["layout"]
            text_box = (x, y, width, height)
            icon_box = (x, y, width, height)
            if layout == "iconAboveText" and icon and text:
                icon_height = max(1, round(height * 0.60))
                icon_box = (x, y, width, icon_height)
                text_box = (x, y + icon_height + 2, width, height - icon_height - 2)
            if icon and layout != "textOnly":
                original = self.assets.image(icon)
                ix, iy, iw, ih = icon_box
                fit = appearance["imageFit"]
                if fit == "cover":
                    fitted = ImageOps.fit(original, (iw, ih), method=Image.Resampling.LANCZOS)
                elif fit == "stretch":
                    fitted = original.resize((iw, ih), Image.Resampling.LANCZOS)
                else:
                    fitted = ImageOps.contain(original, (iw, ih), method=Image.Resampling.LANCZOS)
                image.alpha_composite(fitted, (ix + (iw - fitted.width) // 2, iy + (ih - fitted.height) // 2))
            if layout != "iconOnly":
                _text(ImageDraw.Draw(image), text_box, appearance)
            if (not icon and not text and item["action"]["type"] != "core.none") or back:
                draw = ImageDraw.Draw(image)
                cy = height // 3 if back else size[1] // 2
                if back:
                    draw.line([(x + 22, cy - 9), (x + 10, cy), (x + 22, cy + 9)], fill="white", width=3)
                    draw.line([(x + 11, cy), (x + 43, cy)], fill="white", width=3)
                    # Move the localized Back text below the generated arrow.
                    image.paste(appearance["backgroundColor"], (0, 0, size[0], size[1]))
                    draw = ImageDraw.Draw(image)
                    draw.line([(x + 22, cy - 9), (x + 10, cy), (x + 22, cy + 9)], fill="white", width=3)
                    draw.line([(x + 11, cy), (x + 43, cy)], fill="white", width=3)
                    _text(draw, (x, size[1] // 2, width, height // 2), appearance)
                else:
                    draw.polygon([(size[0] // 3, cy - 12), (size[0] // 3, cy + 12), (size[0] * 2 // 3, cy)], fill=appearance["textColor"])
            image = image.convert("RGB")
            if not item["enabled"]:
                image = Image.blend(image, Image.new("RGB", size, "black"), 0.6)
                ImageDraw.Draw(image).line([(3, 3), (size[0] - 4, size[1] - 4)], fill="#888888", width=2)
            if overlay:
                draw = ImageDraw.Draw(image)
                color = "#E64A45" if overlay in ("failed", "timedOut", "unknownAfterRestart") else "#36A867" if overlay in ("succeeded", "launched") else "#447AC2"
                draw.ellipse((size[0] - 14, 2, size[0] - 3, 13), fill=color)
        raw = image.tobytes()
        self.cache[key] = raw
        self.cache_bytes += len(raw)
        while self.cache_bytes > RENDER_CACHE_BYTES and self.cache:
            _, removed = self.cache.popitem(last=False)
            self.cache_bytes -= len(removed)
        return image

    def page(self, document: dict, page_id: str, overlays: dict | None = None) -> list[Image.Image]:
        capabilities = Capabilities(**document["layout"])
        page = next(p for p in document["pages"] if p["id"] == page_id)
        buttons = {b["keyIndex"]: b for b in page["buttons"]}
        overlays = overlays or {}
        return [self.render(buttons.get(index), capabilities,
                            back=index == 0 and page["parentPageId"] is not None,
                            locale=document["settings"]["locale"],
                            overlay=overlays.get(buttons.get(index, {}).get("id")))
                for index in range(capabilities.key_count)]
