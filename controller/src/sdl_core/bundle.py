"""Portable data only: no approvals, plugins, session variables or execution history."""
import copy
import hashlib
import io
import stat
import zipfile
from pathlib import PurePosixPath

from .errors import SdlError
from .jsonutil import dumps, loads
from .model import all_buttons, require_valid

MAX_BUNDLE_BYTES = 256 * 1024 * 1024


def export_bundle(document: dict, assets: dict[str, bytes]) -> bytes:
    document = copy.deepcopy(document)
    for _, button in all_buttons(document):
        if button["pluginBinding"]:
            button["pluginBinding"]["secretRefs"] = {}
    files = {"configuration.json": dumps(document)}
    for identifier, raw in assets.items():
        if identifier != "sha256:" + hashlib.sha256(raw).hexdigest():
            raise SdlError("ASSET_INVALID", "Bundle asset checksum mismatch.")
        files[f"assets/{identifier[7:]}"] = raw
    if sum(map(len, files.values())) > MAX_BUNDLE_BYTES:
        raise SdlError("LIMIT_EXCEEDED", "Bundle exceeds 256 MiB.")
    manifest = {"formatVersion": "1.0", "files": {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", dumps(manifest))
        for name, data in files.items():
            archive.writestr(name, data)
    return output.getvalue()


def import_bundle(raw: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(raw) > MAX_BUNDLE_BYTES:
        raise SdlError("LIMIT_EXCEEDED", "Bundle exceeds 256 MiB.")
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = set()
            total = 0
            members = archive.infolist()
            if len(members) > 10000:
                raise SdlError("LIMIT_EXCEEDED", "Archive contains too many entries.")
            for entry in members:
                name = entry.filename
                path = PurePosixPath(name)
                mode = entry.external_attr >> 16
                if (name in names or name.startswith("/") or "\\" in name or ":" in name
                    or ".." in path.parts or "." in path.parts or "//" in name
                    or stat.S_ISLNK(mode) or entry.is_dir() or entry.flag_bits & 1):
                    raise SdlError("BUNDLE_INVALID", "Unsafe, duplicate or unsupported archive member.")
                names.add(name)
                total += entry.file_size
                if total > MAX_BUNDLE_BYTES:
                    raise SdlError("LIMIT_EXCEEDED", "Archive expansion exceeds 256 MiB.")
            manifest = loads(archive.read("manifest.json"), 2 * 1024 * 1024)
            if manifest.get("formatVersion") != "1.0" or set(manifest["files"]) != names - {"manifest.json"}:
                raise SdlError("BUNDLE_INVALID", "Bundle manifest is inconsistent.")
            files = {}
            for name, checksum in manifest["files"].items():
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != checksum:
                    raise SdlError("BUNDLE_INVALID", "Bundle checksum mismatch.")
                files[name] = data
            document = loads(files.pop("configuration.json"))
            require_valid(document)
            assets = {}
            expected = {b["appearance"]["iconAssetId"] for _, b in all_buttons(document) if b["appearance"]["iconAssetId"]}
            for name, data in files.items():
                identifier = "sha256:" + hashlib.sha256(data).hexdigest()
                if name != "assets/" + identifier[7:]:
                    raise SdlError("BUNDLE_INVALID", "Unexpected asset path or content identifier.")
                assets[identifier] = data
            if set(assets) != expected:
                raise SdlError("BUNDLE_INVALID", "Bundle assets do not match configuration references.")
            # Imported identities are renewed, preventing a portable document from inheriting local
            # action approvals just because an attacker copied a previously approved UUID/action.
            from uuid import uuid4
            ids = {p["id"]: str(uuid4()) for p in document["pages"]}
            document["configurationId"] = str(uuid4())
            document["rootPageId"] = ids[document["rootPageId"]]
            document["target"]["serialNumber"] = None
            for page in document["pages"]:
                page["id"] = ids[page["id"]]
                if page["parentPageId"]:
                    page["parentPageId"] = ids[page["parentPageId"]]
                for button in page["buttons"]:
                    button["id"] = str(uuid4())
                    if button["action"]["type"] == "core.navigate":
                        button["action"]["pageId"] = ids[button["action"]["pageId"]]
                    if button["pluginBinding"]:
                        button["pluginBinding"]["instanceId"] = str(uuid4())
                        button["pluginBinding"]["secretRefs"] = {}
            return document, assets
    except SdlError:
        raise
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, RuntimeError) as exc:
        raise SdlError("BUNDLE_INVALID", "Invalid or damaged configuration bundle.") from exc
