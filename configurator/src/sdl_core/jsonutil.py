"""Strict JSON, canonical hashes, and atomic durable files."""
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from .errors import SdlError
from .limits import CONFIG_BYTES, MAX_DEPTH


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise SdlError("INVALID_JSON", "Duplicate JSON object key.")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise SdlError("INVALID_JSON", "Non-finite JSON number.")


def loads(data: bytes | str, limit: int = CONFIG_BYTES) -> Any:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > limit:
        raise SdlError("LIMIT_EXCEEDED", "JSON payload exceeds its size limit.")
    # Scan container depth before the parser; braces inside strings are not containers.
    depth = 0
    quoted = escaped = False
    for char in raw:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > MAX_DEPTH:
                raise SdlError("LIMIT_EXCEEDED", "JSON nesting exceeds 64 levels.")
        elif char in (93, 125):
            depth -= 1
    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise SdlError("INVALID_JSON", "Malformed UTF-8 JSON.") from exc
    stack = [result]
    while stack:
        item = stack.pop()
        if isinstance(item, float) and not math.isfinite(item):
            raise SdlError("INVALID_JSON", "Non-finite JSON number.")
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            try:
                item.encode("utf-8")
            except UnicodeError as exc:
                raise SdlError("INVALID_JSON", "Unpaired Unicode surrogate.") from exc
    return result


def dumps(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(dumps(value)).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def read_json(path: Path, limit: int = CONFIG_BYTES) -> Any:
    with path.open("rb") as stream:
        return loads(stream.read(limit + 1), limit)
