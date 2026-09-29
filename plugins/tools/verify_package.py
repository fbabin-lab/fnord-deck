#!/usr/bin/env python3
"""Data-only verifier for the reference package; never imports plugin code.

Not a replacement for the production registry's full manifest-schema, approval,
filesystem-ownership, race-protection, and capability checks.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct

MAX_FILE = 2 * 1024 * 1024


def verify(root: Path) -> tuple[dict, str]:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Package root must be a real directory.")
    root = root.resolve()
    paths = list(root.rglob("*"))
    if len(paths) > 128 or any(p.is_symlink() or not (p.is_file() or p.is_dir()) for p in paths):
        raise ValueError("Too many files, symlinks, or special files in package.")
    raw = (root / "manifest.json").read_bytes()
    if len(raw) > 262_144:
        raise ValueError("Manifest too large.")

    def unique(pairs):
        result = {}
        for k, v in pairs:
            if k in result:
                raise ValueError("Duplicate manifest field.")
            result[k] = v
        return result

    def bad_constant(value):
        raise ValueError("Non-finite manifest value: " + value)

    manifest = json.loads(raw.decode("utf-8"), object_pairs_hook=unique, parse_constant=bad_constant)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), dict):
        raise ValueError("Manifest/files must be objects.")
    declared = manifest["files"]
    actual = {p.relative_to(root).as_posix() for p in paths if p.is_file()} - {"manifest.json"}
    if set(declared) != actual:
        raise ValueError("Missing or unlisted package file.")
    digest = hashlib.sha256(b"fnord-deck-plugin-v1.1\0")
    digest.update(struct.pack(">Q", len(raw)))
    digest.update(raw)
    for name in sorted(declared):
        relative = PurePosixPath(name)
        if relative.is_absolute() or any(x in (".", "..") for x in relative.parts) or relative.as_posix() != name:
            raise ValueError("Invalid package path.")
        item = root / name
        if item.stat().st_size > MAX_FILE:
            raise ValueError("Package file too large.")
        payload = item.read_bytes()
        expected = declared[name]
        if not isinstance(expected, str) or hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("Hash mismatch: " + name)
        encoded = name.encode("utf-8")
        digest.update(struct.pack(">Q", len(encoded)))
        digest.update(encoded)
        digest.update(bytes.fromhex(expected))
    return manifest, digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    args = parser.parse_args()
    try:
        manifest, fingerprint = verify(args.package)
    except (OSError, ValueError, RecursionError) as exc:
        parser.exit(1, f"Verification failed: {exc}\n")
    print(f"Verified {manifest.get('id')} {manifest.get('version')}\nFingerprint: {fingerprint}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
