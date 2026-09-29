"""Data-only package inventory, immutable launch snapshots and fingerprint approvals."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import struct
from dataclasses import dataclass, replace
from itertools import islice
from pathlib import Path, PurePosixPath
from uuid import uuid4

from jsonschema import Draft202012Validator

from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, digest, dumps, loads, read_json
from sdl_core.paths import private_directory

ID = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9_-]*)+")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
HASH = re.compile(r"[a-f0-9]{64}")
PRINTABLE = r"^[^\u0000-\u001f\u007f]*$"
MAX_FILE = 2 * 1024 * 1024
MAX_PACKAGE = 8 * 1024 * 1024


def fail(message: str, code: str = "PLUGIN_PACKAGE_INVALID") -> None:
    raise SdlError(code, message)


def relative(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or len(name) > 256 or not name or "\x00" in name:
        fail("Invalid package-relative path.")
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(x in (".", "..") for x in path.parts) or len(path.parts) > 8:
        fail("Invalid package-relative path.")
    return path.parts


def check_owner(info: os.stat_result, directory: bool = False) -> None:
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if info.st_uid != os.getuid() or info.st_mode & 0o022 or not expected(info.st_mode):
        fail("Plugin files must be owned by this user, regular, and not group/world writable.")


def read_at(root: Path, name: str, maximum: int = MAX_FILE) -> bytes:
    """Open every component relative to held directory FDs; never follow symlinks."""
    parts = relative(name)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        check_owner(os.fstat(fd), True)
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            check_owner(os.fstat(fd), True)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            check_owner(os.fstat(file_fd))
            with os.fdopen(file_fd, "rb", closefd=False) as stream:
                raw = stream.read(maximum + 1)
            if len(raw) > maximum:
                fail("Package file exceeds its byte limit.")
            return raw
        finally:
            os.close(file_fd)
    finally:
        os.close(fd)


def inventory(root: Path) -> set[str]:
    result: set[str] = set()
    pending = [root]
    count = 0
    while pending:
        current = pending.pop()
        check_owner(current.lstat(), True)
        with os.scandir(current) as entries:
            for item in entries:
                count += 1
                if count > 256:
                    fail("Package inventory is too large.")
                path = Path(item.path)
                name = path.relative_to(root).as_posix()
                relative(name)
                if item.is_symlink():
                    fail("Package symlinks are forbidden.")
                if item.is_dir(follow_symlinks=False):
                    pending.append(path)
                else:
                    check_owner(item.stat(follow_symlinks=False))
                    result.add(name)
    if len(result) > 129:
        fail("Package exceeds 128 payload files.")
    return result


def settings_schema(schema: dict) -> None:
    """Bounded, non-executable form subset; no remote refs or pathological regexes."""
    if not isinstance(schema, dict) or schema.get("type") != "object":
        fail("Settings schema must describe an object.")
    pending = [(schema, 0)]
    count = 0
    permitted = {"type", "properties", "additionalProperties", "required", "title", "description",
                 "default", "enum", "minimum", "maximum", "minLength", "maxLength", "minItems",
                 "maxItems", "items", "pattern"}
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > 128 or depth > 5 or not isinstance(node, dict) or set(node) - permitted:
            fail("Unsupported or excessively complex settings schema.")
        kind = node.get("type")
        if kind not in {"object", "string", "integer", "number", "boolean", "array"}:
            fail("Unsupported settings field type.")
        if "pattern" in node and node["pattern"] != PRINTABLE:
            fail("Only the bounded printable-text pattern is supported.")
        if kind == "object":
            if depth or node.get("additionalProperties") is not False or len(node.get("properties", {})) > 32:
                fail("Settings require one flat, closed object with at most 32 fields.")
            pending.extend((field, depth + 1) for field in node.get("properties", {}).values())
        if kind == "array":
            if node.get("items", {}).get("type") not in {"string", "integer", "number", "boolean"}:
                fail("Only arrays of primitive settings are supported.")
            pending.append((node["items"], depth + 1))
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise SdlError("PLUGIN_PACKAGE_INVALID", "Invalid settings schema.") from exc


def validate_settings(contribution: dict, settings: dict) -> None:
    if not isinstance(settings, dict) or len(dumps(settings)) > 8192:
        fail("Plugin settings exceed their size limit.", "PLUGIN_SETTINGS_INVALID")
    if next(Draft202012Validator(contribution["settingsSchema"]).iter_errors(settings), None):
        fail("Settings do not match the contribution schema.", "PLUGIN_SETTINGS_INVALID")
    for value in settings.values():
        if isinstance(value, list) and len(value) > 64 or isinstance(value, str) and len(value) > 1024:
            fail("A plugin setting exceeds its size limit.", "PLUGIN_SETTINGS_INVALID")


def localized(value) -> bool:
    return isinstance(value, dict) and isinstance(value.get("en"), str) and all(
        isinstance(k, str) and isinstance(v, str) and len(v) <= 1024 for k, v in value.items())


def validate_manifest(m: dict) -> None:
    if not isinstance(m, dict) or m.get("manifestVersion") != "1.1":
        fail("Unsupported manifest version.")
    if not ID.fullmatch(m.get("id", "")) or m["id"].startswith("core.") or not VERSION.fullmatch(m.get("version", "")):
        fail("Invalid plugin identity/version.")
    if m.get("api") != {"min": "1.1", "max": "1.1"} or m.get("resourceProfile") != "lightweight":
        fail("Unsupported plugin API/resource profile.")
    if not localized(m.get("name")) or not localized(m.get("description")):
        fail("Plugin text requires an English fallback.")
    if not isinstance(m.get("protocolFeatures"), list) or set(m["protocolFeatures"]) - {"refreshMany"}:
        fail("Unsupported required protocol feature.")
    if not isinstance(m.get("files"), dict) or not 1 <= len(m["files"]) <= 128 or "manifest.json" in m["files"]:
        fail("Invalid package file list.")
    for name, value in m["files"].items():
        relative(name)
        if not isinstance(value, str) or not HASH.fullmatch(value):
            fail("Invalid file SHA-256.")
    assets = m.get("assets")
    if not isinstance(assets, dict) or len(assets) > 64 or any(
        not isinstance(k, str) or len(k) > 128 or v not in m["files"] or not v.lower().endswith(".png") for k, v in assets.items()):
        fail("Invalid registered PNG assets.")
    permissions = m.get("permissions")
    if not isinstance(permissions, dict) or set(permissions) != {"filesystemRead", "network", "processExecution", "secrets"}:
        fail("A complete permission declaration is required.")
    if any(type(permissions[k]) is not bool for k in ("network", "processExecution")) or not isinstance(permissions["filesystemRead"], list):
        fail("Invalid permission declaration.")
    if len(permissions["filesystemRead"]) > 32 or any(not isinstance(p, str) or len(p) > 4096 or not p.startswith("/") or "\x00" in p for p in permissions["filesystemRead"]):
        fail("Invalid disclosed filesystem paths.")
    if permissions["secrets"] != []:
        fail("Secret-requiring plugins are not supported by this host.")
    entry = m.get("entrypoint", {})
    if entry.get("kind") == "interpreted":
        keys = {"kind", "interpreterPath", "interpreterArguments", "scriptPath", "arguments"}
        interpreter = entry.get("interpreterPath", "")
        if not isinstance(interpreter, str) or not interpreter.startswith("/") or "\x00" in interpreter:
            fail("An absolute interpreter path is required.")
        target = entry.get("scriptPath")
    elif entry.get("kind") == "executable":
        keys = {"kind", "path", "arguments"}
        target = entry.get("path")
    else:
        fail("Unsupported plugin entrypoint.")
    if set(entry) != keys or target not in m["files"]:
        fail("Entrypoint must be a declared package file.")
    for key in ("arguments", "interpreterArguments"):
        args = entry.get(key, [])
        if not isinstance(args, list) or len(args) > 32 or any(not isinstance(a, str) or len(a) > 4096 or "\x00" in a for a in args):
            fail("Entrypoint arguments must be bounded literal strings.")
    contributions = m.get("contributions")
    if not isinstance(contributions, list) or not 1 <= len(contributions) <= 32:
        fail("Invalid contributions.")
    seen = set()
    for c in contributions:
        if not isinstance(c, dict) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", c.get("id", "")) or c["id"] in seen:
            fail("Contribution IDs must be unique.")
        seen.add(c["id"])
        if not localized(c.get("name")) or c.get("capabilities") != ["display"] or c.get("commands") != [] or c.get("secrets") != []:
            fail("This release supports display-only contributions, without commands or secrets.")
        if c.get("updateMode") != "poll" or c.get("backgroundPolicy") != "visibleOnly" or type(c.get("settingsVersion")) is not int or c["settingsVersion"] < 1:
            fail("Only visibility-driven polling is supported.")
        if any(type(c.get(k)) is not int or not 1000 <= c[k] <= 3600000 for k in ("minRefreshIntervalMs", "defaultRefreshIntervalMs")):
            fail("Contribution intervals must be between one second and one hour.")
        settings_schema(c.get("settingsSchema"))
        labels = c.get("settingsLabels", {})
        if not isinstance(labels, dict) or len(labels) > 32 or any(k not in c["settingsSchema"].get("properties", {}) or not localized(v) for k, v in labels.items()):
            fail("Invalid localized settings labels.")


@dataclass(frozen=True)
class Package:
    root: Path
    manifest: dict
    fingerprint: str
    interpreter_identity: str
    payloads: dict[str, bytes]

    @property
    def key(self) -> str:
        return self.manifest["id"] + "@" + self.manifest["version"]

    def contribution(self, identifier: str) -> dict:
        for c in self.manifest["contributions"]:
            if c["id"] == identifier:
                return c
        fail("Contribution is unavailable.", "PLUGIN_UNAVAILABLE")

    def argv(self, snapshot: Path) -> list[str]:
        entry = self.manifest["entrypoint"]
        if entry["kind"] == "interpreted":
            return [entry["interpreterPath"], *entry["interpreterArguments"], str(snapshot / entry["scriptPath"]), *entry["arguments"]]
        return [str(snapshot / entry["path"]), *entry["arguments"]]


def verify(root: Path) -> Package:
    actual = inventory(root)
    raw = read_at(root, "manifest.json", 65536)
    m = loads(raw)
    validate_manifest(m)
    if set(m["files"]) != actual - {"manifest.json"}:
        fail("Package contains missing or unlisted files.")
    hashed = hashlib.sha256(b"fnord-deck-plugin-v1.1\0")
    hashed.update(struct.pack(">Q", len(raw)))
    hashed.update(raw)
    payloads = {"manifest.json": raw}
    total = len(raw)
    for name in sorted(m["files"]):
        payload = read_at(root, name)
        total += len(payload)
        if total > MAX_PACKAGE:
            fail("Package exceeds 8 MiB.")
        file_hash = hashlib.sha256(payload).hexdigest()
        if file_hash != m["files"][name]:
            fail("Package file hash mismatch.")
        path_bytes = name.encode("utf-8")
        hashed.update(struct.pack(">Q", len(path_bytes)))
        hashed.update(path_bytes)
        hashed.update(bytes.fromhex(file_hash))
        payloads[name] = payload
    identity = {"kind": "package-executable"}
    entry = m["entrypoint"]
    if entry["kind"] == "interpreted":
        path = Path(entry["interpreterPath"]).resolve(strict=True)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
            fail("Interpreter is not an executable regular file.")
        identity = {"path": entry["interpreterPath"], "realPath": str(path), "device": info.st_dev,
                    "inode": info.st_ino, "size": info.st_size, "mtimeNs": info.st_mtime_ns}
    return Package(root, m, hashed.hexdigest(), digest(identity), payloads)


class PluginRegistry:
    def __init__(self, root: Path, trust_path: Path, snapshot_root: Path):
        self.root = private_directory(root)
        self.snapshot_root = private_directory(snapshot_root)
        self.trust_path = trust_path
        self.packages: dict[str, Package] = {}
        self.errors: list[dict] = []
        try:
            saved = read_json(trust_path) if trust_path.exists() else {}
            self.trust = saved if isinstance(saved, dict) else {}
        except (OSError, SdlError):
            self.trust = {}

    def scan(self) -> None:
        packages = {}
        errors = []
        count = 0
        for group in sorted(islice(self.root.iterdir(), 129)):
            if group.name.startswith("."):
                continue
            if count >= 128:
                errors.append({"code": "PLUGIN_INVENTORY_LIMIT"})
                break
            try:
                check_owner(group.lstat(), True)
                if not ID.fullmatch(group.name):
                    fail("Invalid package directory.")
                for version in sorted(islice(group.iterdir(), 129)):
                    count += 1
                    if count > 128:
                        break
                    try:
                        p = verify(version)
                        if p.manifest["id"] != group.name or p.manifest["version"] != version.name:
                            fail("Package identity does not match its directory.")
                        packages[p.key] = replace(p, payloads={})
                    except (OSError, SdlError, ValueError, TypeError, RecursionError, AttributeError, KeyError) as exc:
                        errors.append({"package": group.name + "/" + version.name, "code": getattr(exc, "code", "PLUGIN_PACKAGE_INVALID")})
            except (OSError, SdlError):
                errors.append({"package": group.name, "code": "PLUGIN_PACKAGE_INVALID"})
        self.packages, self.errors = packages, errors[:128]

    def approved(self, package: Package) -> bool:
        grant = self.trust.get(package.key, {})
        return isinstance(grant, dict) and grant.get("fingerprint") == package.fingerprint and grant.get("interpreterIdentity") == package.interpreter_identity and grant.get("enabled") is True

    def select(self, params: dict) -> Package:
        p = self.packages.get(params["pluginId"] + "@" + params["pluginVersion"])
        if p is None:
            fail("Plugin package is not installed.", "PLUGIN_UNAVAILABLE")
        return p

    def approve(self, params: dict) -> None:
        p = self.select(params)
        current = verify(p.root)
        if params.get("confirmed") is not True:
            fail("Trusted native code requires explicit approval.", "CONFIRMATION_REQUIRED")
        if current.fingerprint != params["fingerprint"] or current.interpreter_identity != params["interpreterIdentity"]:
            fail("Package or interpreter changed; rescan and review again.", "PLUGIN_APPROVAL_STALE")
        self.packages[p.key] = replace(current, payloads={})
        self.trust[p.key] = {"fingerprint": current.fingerprint, "interpreterIdentity": current.interpreter_identity, "enabled": True}
        atomic_write(self.trust_path, dumps(self.trust))

    def enabled(self, params: dict) -> None:
        p = self.select(params)
        grant = self.trust.get(p.key, {})
        grant = grant if isinstance(grant, dict) else {}
        if params["enabled"] and (grant.get("fingerprint") != p.fingerprint or grant.get("interpreterIdentity") != p.interpreter_identity):
            fail("Review this package before enabling it.", "PLUGIN_APPROVAL_REQUIRED")
        self.trust[p.key] = {**grant, "enabled": params["enabled"]}
        atomic_write(self.trust_path, dumps(self.trust))

    def snapshot(self, package: Package) -> Path:
        current = verify(package.root)
        if not self.approved(current) or current.fingerprint != package.fingerprint or current.interpreter_identity != package.interpreter_identity:
            fail("Package changed or approval was revoked.", "PLUGIN_APPROVAL_STALE")
        path = private_directory(self.snapshot_root / str(uuid4()))
        for name, raw in current.payloads.items():
            destination = path / name
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_write(destination, raw)
        entry = current.manifest["entrypoint"]
        if entry["kind"] == "executable":
            (path / entry["path"]).chmod(0o500)
        return path

    def describe(self) -> list[dict]:
        return [{"pluginId": p.manifest["id"], "pluginVersion": p.manifest["version"],
                 "fingerprint": p.fingerprint, "interpreterIdentity": p.interpreter_identity,
                 "approved": self.approved(p), "manifest": p.manifest} for p in self.packages.values()]
