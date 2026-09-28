import asyncio
import hashlib
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from PIL import Image

from .errors import SdlError
from .jsonutil import atomic_write, dumps, read_json
from .limits import ASSET_BYTES, CONVERSION_SECONDS


class AssetStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.lock = asyncio.Lock()

    def directory(self, asset_id: str) -> Path:
        if not isinstance(asset_id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", asset_id):
            raise SdlError("ASSET_INVALID", "Invalid content-addressed asset identifier.")
        return self.root / asset_id[7:]

    def exists(self, asset_id: str) -> bool:
        try:
            directory = self.directory(asset_id)
            return (directory / "original").is_file() and (directory / "canonical.png").is_file() and (directory / "metadata.json").is_file()
        except SdlError:
            return False

    def verify(self, asset_id: str) -> None:
        directory = self.directory(asset_id)
        try:
            if (directory / "original").stat().st_size > ASSET_BYTES:
                raise ValueError()
            if hashlib.sha256((directory / "original").read_bytes()).hexdigest() != asset_id[7:]:
                raise ValueError()
            metadata = read_json(directory / "metadata.json")
            if hashlib.sha256((directory / "canonical.png").read_bytes()).hexdigest() != metadata["canonicalSha256"]:
                raise ValueError()
        except (OSError, ValueError, KeyError) as exc:
            raise SdlError("ASSET_INVALID", "Managed asset integrity check failed.") from exc

    def image(self, asset_id: str) -> Image.Image:
        with Image.open(self.directory(asset_id) / "canonical.png") as source:
            return source.convert("RGBA")

    async def import_bytes(self, raw: bytes, name: str = "image") -> dict:
        if not raw or len(raw) > ASSET_BYTES:
            raise SdlError("ASSET_INVALID", "Images must be nonempty and no larger than 20 MiB.")
        asset_id = "sha256:" + hashlib.sha256(raw).hexdigest()
        async with self.lock:
            if self.exists(asset_id):
                await asyncio.to_thread(self.verify, asset_id)
                return read_json(self.directory(asset_id) / "metadata.json")
            temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=self.root))
            try:
                atomic_write(temporary / "original", raw)
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "sdl_core.convert_worker", str(temporary / "original"),
                    str(temporary / "canonical.png"), str(temporary / "metadata.json"),
                    stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
                try:
                    _, stderr = await asyncio.wait_for(process.communicate(), CONVERSION_SECONDS)
                except (TimeoutError, asyncio.CancelledError):
                    process.kill()
                    await process.wait()
                    raise SdlError("ASSET_INVALID", "Image conversion exceeded its time budget.") from None
                if process.returncode:
                    raise SdlError("ASSET_INVALID", stderr.decode("utf-8", "replace")[:500] or "Image converter failed.")
                metadata = read_json(temporary / "metadata.json")
                metadata.update({"assetId": asset_id, "name": Path(name).name[:256], "originalBytes": len(raw),
                                 "canonicalSha256": hashlib.sha256((temporary / "canonical.png").read_bytes()).hexdigest()})
                atomic_write(temporary / "metadata.json", dumps(metadata))
                with (temporary / "canonical.png").open("rb") as stream:
                    os.fsync(stream.fileno())
                os.rename(temporary, self.directory(asset_id))
                directory_fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
                return metadata
            finally:
                shutil.rmtree(temporary, ignore_errors=True)
