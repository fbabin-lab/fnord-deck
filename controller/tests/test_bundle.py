import io
import stat
import zipfile

import pytest

from sdl_cli.main import demo_configuration
from sdl_core.bundle import export_bundle, import_bundle
from sdl_core.errors import SdlError
from sdl_core.model import validate


def test_bundle_roundtrip_changes_ids_and_preserves_content():
    original = demo_configuration()
    imported, assets = import_bundle(export_bundle(original, {}))
    assert assets == {}
    assert imported["name"] == original["name"]
    assert imported["configurationId"] != original["configurationId"]
    assert imported["rootPageId"] != original["rootPageId"]
    assert not validate(imported)["errors"]
    assert len(imported["pages"]) == len(original["pages"])


@pytest.mark.parametrize("name", ["../outside", "/absolute", "x/../../bad", "a\\b", "C:/x", "a//b"])
def test_archive_paths_rejected(name):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(name, "bad")
    with pytest.raises(SdlError):
        import_bundle(output.getvalue())


def test_archive_symlinks_rejected():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "/outside")
    with pytest.raises(SdlError):
        import_bundle(output.getvalue())
