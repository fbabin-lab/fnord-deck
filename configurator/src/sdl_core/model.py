"""Configuration contracts and iterative tree validation; GUI-independent."""
import os
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker

from .errors import SdlError
from .dynamic import validate_dynamic
from .jsonutil import digest, dumps, loads
from .limits import CONFIG_BYTES, MAX_DEPTH


@dataclass(frozen=True)
class Capabilities:
    rows: int = 4
    columns: int = 8
    keyWidth: int = 96
    keyHeight: int = 96

    @property
    def key_count(self) -> int:
        return self.rows * self.columns

    def json(self) -> dict:
        return asdict(self)


XL = Capabilities()
DEFAULT_APPEARANCE = {
    "iconAssetId": None, "text": "", "layout": "iconAboveText",
    "backgroundColor": "#000000", "textColor": "#FFFFFF", "fontFamily": "DejaVu Sans",
    "fontSizePx": 16, "fontWeight": "normal", "textAlign": "center",
    "imageFit": "contain", "paddingPx": 4, "dynamic": None,
}
SCHEMA = loads(files("sdl_core").joinpath("configuration-v1.schema.json").read_bytes())
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=FormatChecker())


def uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise SdlError("INVALID_PARAMS", "A UUID is required.") from exc


def empty_configuration() -> dict:
    root = str(uuid4())
    return {
        "schemaVersion": "1.0", "configurationId": str(uuid4()), "name": "My Stream Deck",
        "target": {"adapterId": "elgato.streamdeck", "vendorId": 4057, "productId": 108, "serialNumber": None},
        "layout": XL.json(), "rootPageId": root,
        "settings": {"brightnessPercent": 50, "locale": "en"},
        "pages": [{"id": root, "name": "Home", "parentPageId": None, "buttons": []}],
        "extensions": {},
    }


def button(index: int, text: str, action: dict | None = None) -> dict:
    return {
        "id": str(uuid4()), "keyIndex": index, "enabled": True,
        "appearance": {**DEFAULT_APPEARANCE, "text": text, "layout": "textOnly"},
        "action": action or {"type": "core.none"}, "pluginBinding": None, "extensions": {},
    }


def pages_by_id(document: dict) -> dict[str, dict]:
    return {p["id"]: p for p in document["pages"]}


def all_buttons(document: dict):
    for page in document["pages"]:
        for item in page["buttons"]:
            yield page, item


def execution_fingerprints(document: dict) -> set[str]:
    return {digest({"buttonId": b["id"], "action": b["action"]}) for _, b in all_buttons(document) if b["action"]["type"] == "core.execute"}


def execution_summary(document: dict) -> list[dict]:
    return [
        {"pageId": p["id"], "page": p["name"], "buttonId": b["id"], "keyIndex": b["keyIndex"],
         "path": b["action"]["path"], "arguments": b["action"]["arguments"],
         "interpreter": b["action"]["interpreter"], "workingDirectory": b["action"]["workingDirectory"],
         "mode": b["action"]["mode"], "environmentKeys": sorted(b["action"]["environment"])}
        for p, b in all_buttons(document) if b["action"]["type"] == "core.execute"
    ]


def command_argv(action: dict) -> list[str]:
    interpreter = action.get("interpreter")
    if interpreter:
        return [interpreter["path"], *interpreter["arguments"], action["path"], *action["arguments"]]
    return [action["path"], *action["arguments"]]


def validate_executable(action: dict) -> list[str]:
    issues = []
    path = Path(action["path"])
    interpreted = action["interpreter"] is not None
    if not path.is_file() or not os.access(path, os.R_OK if interpreted else os.X_OK):
        issues.append("Script is not readable or executable is unavailable; check its absolute path and permissions.")
    if interpreted:
        ipath = Path(action["interpreter"]["path"])
        if not ipath.is_file() or not os.access(ipath, os.X_OK):
            issues.append("Interpreter is not an executable regular file.")
    if not Path(action["workingDirectory"]).is_dir() or not os.access(action["workingDirectory"], os.X_OK):
        issues.append("Working directory is not accessible.")
    return issues


def validate(document: Any, asset_exists=None) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []

    def issue(code: str, message: str, path: str = "$", page=None, item=None) -> dict:
        return {"code": code, "message": message, "path": path,
                "pageId": page.get("id") if page else None, "buttonId": item.get("id") if item else None}

    try:
        if len(dumps(document)) > CONFIG_BYTES:
            return {"errors": [issue("LIMIT_EXCEEDED", "Configuration exceeds 8 MiB.")], "warnings": []}
    except (TypeError, ValueError, UnicodeError):
        return {"errors": [issue("INVALID_JSON", "Configuration is not valid JSON.")], "warnings": []}
    for error in VALIDATOR.iter_errors(document):
        errors.append(issue("SCHEMA_INVALID", error.message, "$" + "".join(f"/{p}" for p in error.absolute_path)))
        if len(errors) >= 100:
            break
    if errors:
        return {"errors": errors, "warnings": warnings}
    if document["layout"] != XL.json():
        errors.append(issue("LAYOUT_MISMATCH", "This release supports the Stream Deck XL 4x8, 96x96 layout only."))
    # Opaque extension data has no execution privileges and a deliberately small budget.
    for ext in [document["extensions"], *(b["extensions"] for _, b in all_buttons(document))]:
        if len(dumps(ext)) > 16384:
            errors.append(issue("LIMIT_EXCEEDED", "Extensions exceed 16 KiB."))
    seen: set[str] = {document["configurationId"]}
    instance_ids: set[str] = set()
    pages = pages_by_id(document)
    root = document["rootPageId"]
    if root not in pages:
        errors.append(issue("INVALID_TREE", "Root page does not exist."))
    incoming = {page: 0 for page in pages}
    children: dict[str, list[str]] = {page: [] for page in pages}
    count = document["layout"]["rows"] * document["layout"]["columns"]
    for page in document["pages"]:
        if page["id"] in seen:
            errors.append(issue("DUPLICATE_ID", "IDs must be unique.", page=page))
        seen.add(page["id"])
        parent = page["parentPageId"]
        if (page["id"] == root and parent is not None) or (page["id"] != root and parent not in pages):
            errors.append(issue("INVALID_TREE", "Invalid parent page.", page=page))
        positions: set[int] = set()
        for item in page["buttons"]:
            if item["id"] in seen:
                errors.append(issue("DUPLICATE_ID", "IDs must be unique.", page=page, item=item))
            seen.add(item["id"])
            index = item["keyIndex"]
            if index >= count or index in positions or (page["id"] != root and index == 0):
                errors.append(issue("INVALID_KEY", "Key is duplicate, outside the layout, or reserved for Back.", page=page, item=item))
            positions.add(index)
            action = item["action"]
            if action["type"] == "core.navigate":
                target = action["pageId"]
                if target not in pages or pages[target]["parentPageId"] != page["id"]:
                    errors.append(issue("INVALID_TREE", "Navigation must target a direct child.", page=page, item=item))
                else:
                    incoming[target] += 1
                    children[page["id"]].append(target)
            if action["type"] == "core.execute":
                if action["mode"] == "application" and action["timeoutMs"] is not None:
                    errors.append(issue("INVALID_ACTION", "Application timeout must be null.", page=page, item=item))
                if action["mode"] == "task" and action["timeoutMs"] is None:
                    errors.append(issue("INVALID_ACTION", "Task timeout is required.", page=page, item=item))
                if action["concurrency"] == "ignoreWhileRunning" and action["maxParallel"] != 1:
                    errors.append(issue("INVALID_ACTION", "ignoreWhileRunning requires maxParallel=1.", page=page, item=item))
                for message in validate_executable(action):
                    warnings.append(issue("PATH_UNAVAILABLE", message, page=page, item=item))
            if item["appearance"]["fontFamily"] != "DejaVu Sans":
                warnings.append(issue("FONT_FALLBACK", "This release uses DejaVu Sans with a Liberation Sans fallback.", page=page, item=item))
            asset = item["appearance"]["iconAssetId"]
            if asset and asset_exists is not None and not asset_exists(asset):
                errors.append(issue("ASSET_UNAVAILABLE", "Import the referenced image before applying.", page=page, item=item))
            binding = item["pluginBinding"]
            if binding is not None:
                if binding["instanceId"] in instance_ids:
                    errors.append(issue("DUPLICATE_PLUGIN_INSTANCE", "Each bound button needs its own plugin instance UUID.", page=page, item=item))
                instance_ids.add(binding["instanceId"])
                if binding["refreshIntervalMs"] < 1000:
                    warnings.append(issue("PLUGIN_INTERVAL_CLAMPED", "Plugin refresh is clamped to at least one second.", page=page, item=item))
            if action["type"] == "core.plugin.invoke":
                warnings.append(issue("PLUGIN_INVOKE_UNAVAILABLE", "Plugin commands are reserved; application/script actions remain independent.", page=page, item=item))
            if item["appearance"]["dynamic"] is not None and "enabled" in item["appearance"]["dynamic"]:
                try:
                    validate_dynamic(item["appearance"]["dynamic"])
                except SdlError as exc:
                    errors.append(issue(exc.code, exc.message, page=page, item=item))
    for page, total in incoming.items():
        if total != (0 if page == root else 1):
            errors.append(issue("INVALID_TREE", "Every non-root page needs exactly one incoming navigation button."))
    visited = set()
    pending = [(root, 1)] if root in pages else []
    while pending:
        current, depth = pending.pop()
        if current in visited:
            errors.append(issue("INVALID_TREE", "Cycle or shared child page detected."))
            continue
        visited.add(current)
        if depth > MAX_DEPTH:
            errors.append(issue("LIMIT_EXCEEDED", "Maximum section depth is 64."))
            break
        pending.extend((child, depth + 1) for child in children[current])
    if visited != set(pages):
        errors.append(issue("INVALID_TREE", "All pages must be reachable from the root."))
    return {"errors": errors[:100], "warnings": warnings[:100]}


def require_valid(document: dict, asset_exists=None) -> list[dict]:
    result = validate(document, asset_exists)
    if result["errors"]:
        raise SdlError("VALIDATION_FAILED", "Configuration validation failed.", details=result)
    return result["warnings"]
