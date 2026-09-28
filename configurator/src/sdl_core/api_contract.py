"""Parameters for the local API; also exported as machine-readable JSON Schema."""
from jsonschema import Draft202012Validator

from .errors import SdlError
from .model import SCHEMA


def obj(properties=None, required=()):
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def string(maximum=4096):
    return {"type": "string", "maxLength": maximum, "pattern": r"^[^\u0000]*$"}


UUID = {"type": "string", "pattern": "^[0-9a-f-]{36}$"}
REV = {"type": "integer", "minimum": 0}
BOOL = {"type": "boolean"}
PAGE = {"cursor": {"type": "integer", "minimum": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 100}}
METHODS = {
    "system.hello": obj({"apiMajor": {"type": "integer"}, "apiMinor": {"type": "integer"}, "clientName": string(128)}, ("apiMajor", "apiMinor", "clientName")),
    "system.snapshot": obj(), "system.diagnostics": obj(),
    "events.subscribe": obj({"types": {"type": "array", "items": string(128), "maxItems": 64}}),
    "configuration.get": obj({"revision": REV}),
    "configuration.validate": obj({"document": {"type": "object"}}, ("document",)),
    "configuration.review": obj({"document": {"type": "object"}}, ("document",)),
    "configuration.apply": obj({"document": {"type": "object"}, "expectedRevision": REV,
                                "operationId": UUID, "reviewToken": string(256), "confirmed": BOOL},
                               ("document", "expectedRevision", "operationId")),
    "configuration.history": obj(PAGE),
    "asset.beginImport": obj({"name": string(256), "byteCount": {"type": "integer", "minimum": 1, "maximum": 20971520},
                              "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}, ("name", "byteCount", "sha256")),
    "asset.writeChunk": obj({"uploadId": UUID, "sequence": {"type": "integer", "minimum": 0}, "data": string(350000)}, ("uploadId", "sequence", "data")),
    "asset.finishImport": obj({"uploadId": UUID}, ("uploadId",)),
    "asset.readChunk": obj({"assetId": {"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"},
                            "offset": {"type": "integer", "minimum": 0},
                            "length": {"type": "integer", "minimum": 1, "maximum": 262144}}, ("assetId", "offset", "length")),
    "device.list": obj(), "device.retry": obj({"deviceId": string()}),
    "runtime.navigate": obj({"pageId": UUID, "expectedRevision": REV}, ("pageId", "expectedRevision")),
    "runtime.pause": obj({"paused": BOOL}, ("paused",)),
    "session.update": obj({"variables": {"type": "object", "maxProperties": 32, "additionalProperties": string(8192)}}, ("variables",)),
    "execution.test": obj({"buttonId": UUID, "expectedRevision": REV, "confirmed": BOOL, "operationId": UUID}, ("buttonId", "expectedRevision", "confirmed", "operationId")),
    "execution.list": obj({**PAGE, "includeOutput": BOOL}),
    "execution.cancel": obj({"runId": UUID}, ("runId",)),
    "plugins.list": obj(),
    "render.preview": obj({"pageId": UUID}),
    "simulator.key": obj({"keyIndex": {"type": "integer", "minimum": 0, "maximum": 31}, "down": BOOL}, ("keyIndex", "down")),
    "simulator.connection": obj({"connected": BOOL}, ("connected",)),
}


def validate_params(method: str, params: dict) -> None:
    if method not in METHODS:
        if method.startswith(("plugins.", "secret.")):
            raise SdlError("FEATURE_DEFERRED", "Plugin and secret-management APIs are deferred.")
        raise SdlError("METHOD_NOT_FOUND", "Unknown API method.")
    errors = list(Draft202012Validator(METHODS[method]).iter_errors(params))
    if errors:
        # Avoid validator messages that echo an arbitrary request value.
        raise SdlError("INVALID_PARAMS", "Invalid method parameters.", details={"path": list(errors[0].path), "validator": errors[0].validator})
