"""Short-lived, resource-limited image decoder. No external SVG resources are allowed."""
import io
import json
import re
import resource
import sys
import warnings
from pathlib import Path

from .limits import ASSET_BYTES, ASSET_PIXELS, CANONICAL_SIZE, CONVERSION_MEMORY

ALLOWED_TAGS = {
    "svg", "g", "defs", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "linearGradient", "radialGradient", "stop", "clipPath", "mask", "title", "desc",
}
ALLOWED_ATTRS = {
    "id", "viewBox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy",
    "r", "rx", "ry", "fx", "fy", "d", "points", "transform", "fill", "fill-opacity",
    "fill-rule", "stroke", "stroke-width", "stroke-opacity", "stroke-linecap",
    "stroke-linejoin", "stroke-miterlimit", "stroke-dasharray", "stroke-dashoffset",
    "opacity", "clip-path", "clip-rule", "mask", "maskUnits", "maskContentUnits",
    "clipPathUnits", "gradientUnits", "gradientTransform", "spreadMethod", "offset",
    "stop-color", "stop-opacity", "preserveAspectRatio", "version", "style",
}
STYLE_ATTRS = {"fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-opacity",
               "stroke-linecap", "stroke-linejoin", "opacity", "stop-color", "stop-opacity"}


def safe_svg(raw: bytes) -> bytes:
    from defusedxml import ElementTree
    if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise ValueError("SVG DTDs and entities are forbidden")
    root = ElementTree.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    if root.tag != "{http://www.w3.org/2000/svg}svg" and root.tag != "svg":
        raise ValueError("Not an SVG document")
    nodes = list(root.iter())
    if len(nodes) > 10000:
        raise ValueError("Too many SVG elements")
    for node in nodes:
        if not isinstance(node.tag, str) or node.tag.rsplit("}", 1)[-1] not in ALLOWED_TAGS:
            raise ValueError("Unsupported SVG element (text, images, scripts and resource links are forbidden)")
        for name, value in node.attrib.items():
            if name not in ALLOWED_ATTRS or "\\" in value or "@" in value or "&" in value:
                raise ValueError("Unsupported SVG attribute")
            if name == "style":
                for rule in value.split(";"):
                    if not rule.strip():
                        continue
                    key, separator, val = rule.partition(":")
                    if not separator or key.strip() not in STYLE_ATTRS:
                        raise ValueError("Unsupported SVG CSS")
                    validate_value(val)
            else:
                validate_value(value)
    # Parse/re-serialize rather than sending processing instructions to the rasterizer.
    return ElementTree.tostring(root, encoding="utf-8")


def validate_value(value: str) -> None:
    cleaned = re.sub(r"url\(\s*#[A-Za-z_][A-Za-z0-9_.:-]*\s*\)", "", value, flags=re.I)
    if re.search(r"url\s*\(|https?:|file:|data:|javascript:|expression\s*\(", cleaned, re.I):
        raise ValueError("External SVG resources are forbidden")


def convert(raw: bytes):
    from PIL import Image, ImageCms, ImageOps
    Image.MAX_IMAGE_PIXELS = ASSET_PIXELS
    warnings.simplefilter("error", Image.DecompressionBombWarning)
    notes = []
    stripped = raw.lstrip()
    if stripped.startswith(b"<") or stripped.startswith(b"\xef\xbb\xbf<"):
        import cairosvg
        svg = safe_svg(raw)
        # Match viewBox/declared aspect ratio inside a bounded surface; conversion is isolated.
        from defusedxml import ElementTree
        element = ElementTree.fromstring(svg)
        viewbox = element.get("viewBox", "").replace(",", " ").split()
        if len(viewbox) == 4:
            width, height = float(viewbox[2]), float(viewbox[3])
        else:
            def dimension(name):
                text = element.get(name, "96")
                if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:px)?", text):
                    raise ValueError("SVG requires a viewBox or numeric pixel dimensions")
                return float(text.removesuffix("px"))
            width, height = dimension("width"), dimension("height")
        if not (0 < width <= 1_000_000 and 0 < height <= 1_000_000):
            raise ValueError("Invalid SVG dimensions")
        factor = CANONICAL_SIZE / max(width, height)
        png = cairosvg.svg2png(bytestring=svg, output_width=max(1, round(width * factor)),
                              output_height=max(1, round(height * factor)), unsafe=False)
        image = Image.open(io.BytesIO(png)).convert("RGBA")
        media = "image/svg+xml"
    else:
        image = Image.open(io.BytesIO(raw))
        fmt = image.format
        if fmt not in {"PNG", "JPEG", "WEBP", "BMP", "GIF"}:
            raise ValueError("Unsupported image format")
        if image.width * image.height > ASSET_PIXELS:
            raise ValueError("Image exceeds 40 million pixels")
        if getattr(image, "n_frames", 1) > 1:
            notes.append("Animated image: only its first frame was imported.")
        image.seek(0)
        profile = image.info.get("icc_profile")
        image = ImageOps.exif_transpose(image).convert("RGBA")
        image.load()
        if profile:
            try:
                alpha = image.getchannel("A")
                image = ImageCms.profileToProfile(image.convert("RGB"), ImageCms.ImageCmsProfile(io.BytesIO(profile)),
                                                  ImageCms.createProfile("sRGB"), outputMode="RGB").convert("RGBA")
                image.putalpha(alpha)
            except Exception:
                notes.append("Invalid/unsupported color profile: retained decoded colors.")
        media = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp", "BMP": "image/bmp", "GIF": "image/gif"}[fmt]
        image.thumbnail((CANONICAL_SIZE, CANONICAL_SIZE), Image.Resampling.LANCZOS)
    return image, {"mediaType": media, "width": image.width, "height": image.height, "warnings": notes}


def main() -> None:
    resource.setrlimit(resource.RLIMIT_AS, (CONVERSION_MEMORY, CONVERSION_MEMORY))
    resource.setrlimit(resource.RLIMIT_CPU, (4, 5))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024 * 1024, 32 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    try:
        raw = Path(sys.argv[1]).read_bytes()
        if len(raw) > ASSET_BYTES:
            raise ValueError("Image exceeds 20 MiB")
        image, metadata = convert(raw)
        image.save(sys.argv[2], "PNG")
        Path(sys.argv[3]).write_text(json.dumps(metadata))
    except Exception as exc:
        print(f"Image conversion rejected: {str(exc)[:300]}", file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
