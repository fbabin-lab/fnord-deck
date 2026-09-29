"""Explicit, data-only package installation. It never imports or approves a plugin."""
from __future__ import annotations

import argparse
import fcntl
import os
from pathlib import Path
import shutil
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write
from sdl_core.paths import Paths, private_directory
from .registry import verify


def install(source: Path, paths: Paths) -> dict:
    package = verify(source)
    root = private_directory(paths.data / "plugins")
    # Serialize local installations. Discovery ignores these dot-prefixed staging files.
    fd = os.open(root / ".install.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    staging = None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        group = private_directory(root / package.manifest["id"])
        destination = group / package.manifest["version"]
        if destination.exists() or destination.is_symlink():
            existing = verify(destination)
            if existing.fingerprint != package.fingerprint:
                raise SdlError("PLUGIN_VERSION_EXISTS", "A different package already has this version. Install a new version; do not overwrite it.")
            return {"installed": True, "changed": False, "path": str(destination), "fingerprint": existing.fingerprint}
        staging = private_directory(root / (".install-" + uuid4().hex))
        for name, raw in package.payloads.items():
            target = staging / name
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            atomic_write(target, raw)
        if package.manifest["entrypoint"]["kind"] == "executable":
            (staging / package.manifest["entrypoint"]["path"]).chmod(0o500)
        verify(staging)
        os.rename(staging, destination)
        directory = os.open(group, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        staging = None
        return {"installed": True, "changed": True, "path": str(destination), "fingerprint": package.fingerprint}
    finally:
        os.close(fd)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--simulator", action="store_true")
    args = parser.parse_args()
    paths = Paths.discover("simulator" if args.simulator else "default")
    paths.prepare()
    try:
        result = install(args.directory.absolute(), paths)
    except (SdlError, OSError, ValueError, TypeError) as exc:
        raise SystemExit(f"Package installation failed: {exc}") from exc
    print(f"Installed plugin files at {result['path']}. No approval was granted and no plugin was started.")


if __name__ == "__main__":
    main()
