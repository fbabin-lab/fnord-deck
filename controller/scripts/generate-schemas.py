#!/usr/bin/env python3
"""Generate the shared schema shipped inside the core wheel and at repository root."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def obj(properties, required=None, **extra):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False, **extra}

def enum(*values):
    return {"enum": list(values)}

def string(maximum=4096):
    return {"type": "string", "maxLength": maximum, "pattern": r"^[^\u0000]*$"}

def nullable(value):
    return {"anyOf": [{"type": "null"}, value]}

uid = {"type": "string", "format": "uuid", "pattern": "^[0-9a-f-]{36}$"}
path = {**string(), "pattern": r"^/[^\u0000]*$"}
arguments = {"type": "array", "items": string(), "maxItems": 128}
extensions = {"type": "object", "maxProperties": 64,
              "propertyNames": {"pattern": r"^[A-Za-z0-9_-]+(?:[.:][A-Za-z0-9_-]+)+$"},
              "additionalProperties": {"type": "object"}}
execute = obj({
    "type": {"const": "core.execute"}, "path": path, "arguments": arguments,
    "interpreter": nullable(obj({"path": path, "arguments": arguments})),
    "workingDirectory": path,
    "environment": {"type": "object", "maxProperties": 64,
                    "propertyNames": {"pattern": "^[A-Za-z_][A-Za-z0-9_]*$"},
                    "additionalProperties": string(8192)},
    "mode": enum("application", "task"),
    "timeoutMs": nullable({"type": "integer", "minimum": 1000, "maximum": 86400000}),
    "concurrency": enum("ignoreWhileRunning", "parallel"),
    "maxParallel": {"type": "integer", "minimum": 1, "maximum": 4},
})
appearance = obj({
    "iconAssetId": nullable({"type": "string", "pattern": "^sha256:[0-9a-f]{64}$"}),
    "text": string(1024), "layout": enum("iconOnly", "textOnly", "iconAboveText", "textOverlay"),
    "backgroundColor": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
    "textColor": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
    "fontFamily": string(128), "fontSizePx": {"type": "integer", "minimum": 8, "maximum": 128},
    "fontWeight": enum("normal", "bold"), "textAlign": enum("left", "center", "right"),
    "imageFit": enum("contain", "cover", "stretch"),
    "paddingPx": {"type": "integer", "minimum": 0, "maximum": 20},
    "dynamic": nullable({"type": "object", "maxProperties": 32}),
})
binding = nullable(obj({
    "instanceId": uid, "pluginId": string(128), "pluginVersion": string(64),
    "contributionId": string(128), "settingsVersion": {"type": "integer", "minimum": 1},
    "settings": {"type": "object"}, "secretRefs": {"type": "object", "additionalProperties": string(256)},
    "refreshIntervalMs": {"type": "integer", "minimum": 1},
}))
button = obj({
    "id": uid, "keyIndex": {"type": "integer", "minimum": 0, "maximum": 255},
    "enabled": {"type": "boolean"}, "appearance": appearance,
    "action": {"oneOf": [obj({"type": {"const": "core.none"}}),
                          obj({"type": {"const": "core.home"}}),
                          obj({"type": {"const": "core.navigate"}, "pageId": uid}),
                          execute,
                          obj({"type": {"const": "core.plugin.invoke"}, "commandId": string(128)})]},
    "pluginBinding": binding, "extensions": extensions,
})
schema = obj({
    "schemaVersion": {"const": "1.0"}, "configurationId": uid, "name": string(256),
    "target": obj({"adapterId": {"const": "elgato.streamdeck"}, "vendorId": {"const": 4057},
                   "productId": {"const": 108}, "serialNumber": nullable(string(128))}),
    "layout": obj({"rows": {"type": "integer", "minimum": 1, "maximum": 16},
                   "columns": {"type": "integer", "minimum": 1, "maximum": 16},
                   "keyWidth": {"type": "integer", "minimum": 32, "maximum": 512},
                   "keyHeight": {"type": "integer", "minimum": 32, "maximum": 512}}),
    "rootPageId": uid,
    "settings": obj({"brightnessPercent": {"type": "integer", "minimum": 0, "maximum": 100},
                     "locale": enum("en", "fr")}),
    "pages": {"type": "array", "minItems": 1, "maxItems": 1024,
              "items": obj({"id": uid, "name": string(256), "parentPageId": nullable(uid),
                            "buttons": {"type": "array", "maxItems": 256, "items": button}})},
    "extensions": extensions,
})
schema.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "$id": "urn:sdl:configuration:1.0"})
for path in (ROOT / "schemas/configuration-v1.schema.json", ROOT / "src/sdl_core/configuration-v1.schema.json"):
    path.write_text(json.dumps(schema, indent=2) + "\n")
