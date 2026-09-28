import io

import pytest
from PIL import Image

from sdl_core.assets import AssetStore
from sdl_core.errors import SdlError
from sdl_core.model import XL, button
from sdl_core.render import Renderer


def raster(fmt, size=(180, 120)):
    image = Image.new("RGB", size, (21, 90, 160))
    out = io.BytesIO(); image.save(out, fmt)
    return out.getvalue()


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP", "BMP", "GIF"])
async def test_supported_rasters(fmt, paths):
    store = AssetStore(paths.data / "assets")
    result = await store.import_bytes(raster(fmt), "arbitrary.extension")
    store.verify(result["assetId"])
    assert (result["width"], result["height"]) == (180, 120)
    assert store.image(result["assetId"]).mode == "RGBA"


async def test_svg_and_size_limit(paths):
    store = AssetStore(paths.data / "assets")
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100"><rect width="200" height="100" fill="#33AACC"/></svg>'
    result = await store.import_bytes(svg)
    assert (result["width"], result["height"]) == (1024, 512)
    scaled = await store.import_bytes(raster("PNG", (2048, 1024)))
    assert scaled["width"] == 1024


@pytest.mark.parametrize("payload", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>bad()</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><text>Hello</text></svg>',
    b'<!DOCTYPE svg [<!ENTITY x SYSTEM "file:///etc/passwd">]><svg>&x;</svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><rect fill="url(https://example.com/x)"/></svg>',
    b'not an image',
])
async def test_unsafe_images_rejected(payload, paths):
    with pytest.raises(SdlError):
        await AssetStore(paths.data / "assets").import_bytes(payload)


async def test_animated_first_frame_and_alpha(paths):
    one = Image.new("RGB", (20, 20), "red")
    two = Image.new("RGB", (20, 20), "blue")
    out = io.BytesIO(); one.save(out, "GIF", save_all=True, append_images=[two], duration=100)
    store = AssetStore(paths.data / "assets")
    result = await store.import_bytes(out.getvalue())
    assert result["warnings"]
    assert store.image(result["assetId"]).getpixel((1, 1))[:3] == (255, 0, 0)
    image = Image.new("RGBA", (20, 20), (255, 0, 0, 128))
    out = io.BytesIO(); image.save(out, "PNG")
    result = await store.import_bytes(out.getvalue())
    item = button(0, "")
    item["appearance"].update(iconAssetId=result["assetId"], layout="iconOnly", backgroundColor="#0000FF")
    rendered = Renderer(store).render(item, XL)
    assert rendered.getpixel((48, 48)) in [(128, 0, 127), (128, 0, 128)]


async def test_render_modes_cache_fallback_disabled_and_back(paths):
    store = AssetStore(paths.data / "assets")
    result = await store.import_bytes(raster("PNG"))
    renderer = Renderer(store)
    for layout in ["iconOnly", "textOnly", "iconAboveText", "textOverlay"]:
        item = button(0, "Été à Montréal\nA long line " * 5)
        item["appearance"].update(iconAssetId=result["assetId"], layout=layout)
        image = renderer.render(item, XL)
        assert image.size == (96, 96)
        assert image.tobytes() == renderer.render(item, XL).tobytes()
    fallback = button(0, "", {"type": "core.home"})
    assert renderer.render(fallback, XL).getbbox()
    normal = renderer.render(fallback, XL).tobytes()
    fallback["enabled"] = False
    assert renderer.render(fallback, XL).tobytes() != normal
    assert renderer.render(None, XL).getbbox() is None
    assert renderer.render(None, XL, back=True, locale="fr").getbbox()
    assert renderer.render(None, XL, back=True, locale="fr").tobytes() != renderer.render(None, XL, back=True, locale="en").tobytes()


async def test_integrity_failure(paths):
    store = AssetStore(paths.data / "assets")
    result = await store.import_bytes(raster("PNG"))
    (store.directory(result["assetId"]) / "original").write_bytes(b"corrupt")
    with pytest.raises(SdlError):
        store.verify(result["assetId"])
