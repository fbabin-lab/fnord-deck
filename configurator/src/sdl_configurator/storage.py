"""Editor-owned files only. Controller revision/asset directories are never touched."""
from __future__ import annotations

import asyncio
import fcntl
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from sdl_core.assets import AssetStore
from sdl_core.bundle import MAX_BUNDLE_BYTES, export_bundle, import_bundle
from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, dumps, loads, read_json
from sdl_core.limits import ASSET_BYTES, CONFIG_BYTES
from sdl_core.model import require_valid
from sdl_core.paths import private_directory

from .document import Draft, asset_ids, renew_identities


def read_bounded(path: Path, limit: int) -> bytes:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise SdlError("FILE_INVALID", "Choose a regular file, not a directory, device, or pipe.")
    if info.st_size > limit:
        raise SdlError("LIMIT_EXCEEDED", f"File exceeds its {limit}-byte limit.")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise SdlError("LIMIT_EXCEEDED", "File changed while reading and exceeds its size limit.")
    return raw


@dataclass(frozen=True)
class EditorPaths:
    data: Path
    state: Path
    socket: Path

    @classmethod
    def discover(cls, *, simulator: bool = False, workspace: Path | None = None,
                 socket_path: Path | None = None) -> "EditorPaths":
        name = "streamdeck-linux-configurator" + ("-simulator" if simulator else "")
        if workspace is not None:
            if not workspace.is_absolute():
                raise SdlError("UNSAFE_PATH", "Workspace path must be absolute.")
            data, state = workspace / "data", workspace / "state"
        else:
            data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / name
            state = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / name
        if not data.is_absolute() or not state.is_absolute():
            raise SdlError("UNSAFE_PATH", "XDG paths must be absolute.")
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
        target = "streamdeck-linux-simulator" if simulator else "streamdeck-linux"
        socket_path = socket_path or runtime / target / "controller.sock"
        if not socket_path.is_absolute():
            raise SdlError("UNSAFE_PATH", "Controller socket path must be absolute.")
        return cls(data, state, socket_path)

    def prepare(self) -> None:
        for path in (self.data, self.state, self.data / "assets", self.state / "drafts"):
            private_directory(path)


class WorkspaceLock:
    def __init__(self, path: Path) -> None:
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.fd)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            os.close(self.fd)
            raise SdlError("UNSAFE_PATH", "Unsafe Configurator workspace lock.")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            raise SdlError("ALREADY_RUNNING", "A Configurator already uses this workspace.") from exc

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class Workspace:
    def __init__(self, paths: EditorPaths) -> None:
        self.paths = paths
        paths.prepare()
        self.assets = AssetStore(paths.data / "assets")
        self._write_lock = asyncio.Lock()

    @property
    def draft_path(self) -> Path:
        return self.paths.state / "draft.json"

    @property
    def pending_path(self) -> Path:
        return self.paths.state / "pending-apply.json"

    async def save(self, envelope: dict) -> None:
        # All GUI saves enter the same worker loop; this lock preserves submission order.
        raw = dumps(envelope)
        async with self._write_lock:
            await asyncio.to_thread(atomic_write, self.draft_path, raw)

    async def save_named(self, envelope: dict) -> None:
        draft = Draft.from_envelope(envelope)
        path = self.paths.state / "drafts" / (draft.document["configurationId"] + ".json")
        async with self._write_lock:
            await asyncio.to_thread(atomic_write, path, dumps(envelope))
            await asyncio.to_thread(atomic_write, self.draft_path, dumps(envelope))

    def load(self) -> Draft | None:
        if not self.draft_path.exists():
            return None
        return Draft.from_envelope(read_json(self.draft_path, CONFIG_BYTES + 4096))

    async def import_image(self, path: Path) -> dict:
        raw = await asyncio.to_thread(read_bounded, path, ASSET_BYTES)
        return await self.assets.import_bytes(raw, path.name)

    async def open_external(self, path: Path) -> dict:
        raw = await asyncio.to_thread(read_bounded, path, MAX_BUNDLE_BYTES)
        if raw[:2] == b"PK":
            document, images = await asyncio.to_thread(import_bundle, raw)
            for identifier, image in images.items():
                result = await self.assets.import_bytes(image, identifier)
                if result["assetId"] != identifier:
                    raise SdlError("ASSET_INVALID", "Imported image identity does not match its bundle.")
            return document  # import_bundle already renews identities and clears device/secret references.
        document = loads(raw, CONFIG_BYTES + 4096)
        if isinstance(document, dict) and "draftFormatVersion" in document:
            document = Draft.from_envelope(document).document
        require_valid(document)
        return renew_identities(document)

    async def export(self, path: Path, document: dict) -> None:
        require_valid(document, self.assets.exists)
        def build():
            images = {}
            for identifier in asset_ids(document):
                self.assets.verify(identifier)
                images[identifier] = read_bounded(self.assets.directory(identifier) / "original", ASSET_BYTES)
            return export_bundle(document, images)
        raw = await asyncio.to_thread(build)
        await asyncio.to_thread(atomic_write, path, raw)

    async def write_json(self, path: Path, document: dict) -> None:
        require_valid(document)
        await asyncio.to_thread(atomic_write, path, dumps(document))

    async def remember_pending(self, params: dict) -> None:
        # Do not persist transient review tokens. On replay a durable outcome is looked up
        # before approval checks by Controller 0.1.0; uncommitted executable actions need review.
        safe = {k: params[k] for k in ("document", "expectedRevision", "operationId")}
        safe["socket"] = str(self.paths.socket)
        await asyncio.to_thread(atomic_write, self.pending_path, dumps(safe))

    def pending(self) -> dict | None:
        if not self.pending_path.exists():
            return None
        data = read_json(self.pending_path, CONFIG_BYTES + 4096)
        if not isinstance(data, dict) or data.get("socket") != str(self.paths.socket):
            raise SdlError("PENDING_INVALID", "Pending Apply belongs to a different Controller socket.")
        require_valid(data.get("document"))
        return data

    async def clear_pending(self) -> None:
        await asyncio.to_thread(self.pending_path.unlink, missing_ok=True)
