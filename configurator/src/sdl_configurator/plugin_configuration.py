"""Pure, bounded plugin form validation and per-button binding construction."""
from __future__ import annotations

import copy
from uuid import uuid4

from jsonschema import Draft202012Validator
from sdl_core.dynamic import DEFAULT_DYNAMIC, validate_dynamic
from sdl_core.errors import SdlError
from sdl_core.jsonutil import dumps


def label(value, locale="en"):
    if isinstance(value, dict):
        return str(value.get(locale, value.get("en", "")))[:1024]
    return str(value)[:1024]


def checked_schema(schema):
    """Do not let even an offline catalog introduce remote refs or costly expressions."""
    if not isinstance(schema, dict) or len(dumps(schema)) > 65536 or schema.get("type") != "object" or schema.get("additionalProperties") is not False:
        raise SdlError("PLUGIN_SETTINGS_INVALID", "Unsupported settings schema.")
    props = schema.get("properties", {})
    if not isinstance(props, dict) or len(props) > 32:
        raise SdlError("PLUGIN_SETTINGS_INVALID", "Too many plugin settings.")
    allowed = {"type", "properties", "additionalProperties", "required", "title", "description", "default", "enum", "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "items", "pattern"}
    pending = [(schema, 0)]
    while pending:
        node, depth = pending.pop()
        if not isinstance(node, dict) or depth > 2 or set(node) - allowed or ("pattern" in node and node["pattern"] != r"^[^\u0000-\u001f\u007f]*$"):
            raise SdlError("PLUGIN_SETTINGS_INVALID", "Unsupported settings schema feature.")
        kind = node.get("type")
        if kind == "object" and depth == 0:
            pending.extend((field, 1) for field in props.values())
        elif kind == "array" and depth == 1:
            pending.append((node.get("items"), 2))
        elif kind not in {"string", "integer", "number", "boolean"}:
            raise SdlError("PLUGIN_SETTINGS_INVALID", "Only flat primitive settings and arrays are supported.")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        raise SdlError("PLUGIN_SETTINGS_INVALID", "Invalid settings schema.") from exc
    return schema


def binding_for(package, contribution, settings, interval_ms, previous=None):
    schema = checked_schema(contribution["settingsSchema"])
    if len(dumps(settings)) > 8192 or next(Draft202012Validator(schema).iter_errors(settings), None):
        raise SdlError("PLUGIN_SETTINGS_INVALID", "Settings do not match the plugin's schema.")
    if any(isinstance(v, list) and len(v) > 64 or isinstance(v, str) and len(v) > 1024 for v in settings.values()):
        raise SdlError("PLUGIN_SETTINGS_INVALID", "A plugin setting exceeds its size limit.")
    identity = (package["pluginId"], package["pluginVersion"], contribution["id"])
    old = tuple((previous or {}).get(k) for k in ("pluginId", "pluginVersion", "contributionId"))
    return {"instanceId": previous["instanceId"] if previous and old == identity else str(uuid4()),
            "pluginId": identity[0], "pluginVersion": identity[1], "contributionId": identity[2],
            "settingsVersion": contribution["settingsVersion"], "settings": copy.deepcopy(settings), "secretRefs": {},
            "refreshIntervalMs": max(1000, contribution["minRefreshIntervalMs"], min(3600000, int(interval_ms)))}


def display_policy(enabled=True, template=None, decimals=0, stale_after=10000, overrides=None):
    policy = {**DEFAULT_DYNAMIC, "enabled": enabled, "numberDecimals": decimals, "staleAfterMs": stale_after,
              "allowedOverrides": overrides if overrides is not None else ["text", "progress"]}
    if template is not None:
        policy["textTemplate"] = template
    validate_dynamic(policy)
    return policy
