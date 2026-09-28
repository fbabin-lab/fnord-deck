import copy
import json
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator

from sdl_cli.main import demo_configuration
from sdl_core.errors import SdlError
from sdl_core.jsonutil import loads
from sdl_core.model import SCHEMA, button, command_argv, empty_configuration, validate


def test_schema_and_example():
    Draft202012Validator.check_schema(SCHEMA)
    assert validate(demo_configuration())["errors"] == []


@pytest.mark.parametrize("value", [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b'"\\ud800"', b'\xff', b'[' * 65 + b']' * 65])
def test_strict_json(value):
    with pytest.raises(SdlError):
        loads(value)


def test_strings_do_not_count_as_json_nesting():
    assert loads(json.dumps({"text": "[" * 200}))["text"] == "[" * 200


@pytest.mark.parametrize("mutation", [
    lambda d: d["pages"][0]["buttons"].append(copy.deepcopy(d["pages"][0]["buttons"][0])),
    lambda d: d["pages"][1]["buttons"].append(button(0, "Cannot replace Back")),
    lambda d: d["pages"][0]["buttons"][0]["action"].update(pageId=str(uuid4())),
    lambda d: d["pages"][1].update(parentPageId=d["pages"][2]["id"]),
    lambda d: d.update(schemaVersion="2.0"),
    lambda d: d["pages"][0]["buttons"][1]["action"].update(type="unknown.action"),
    lambda d: d["pages"][0]["buttons"][1].update(keyIndex=32),
    lambda d: d["layout"].update(columns=4),
])
def test_invalid_configurations(mutation):
    doc = demo_configuration()
    mutation(doc)
    assert validate(doc)["errors"]


def test_64_levels_iteratively():
    doc = empty_configuration()
    for index in range(63):
        parent = doc["pages"][-1]
        child = str(uuid4())
        parent["buttons"].append(button(1, "Next", {"type": "core.navigate", "pageId": child}))
        doc["pages"].append({"id": child, "name": str(index), "parentPageId": parent["id"], "buttons": []})
    assert not validate(doc)["errors"]
    parent = doc["pages"][-1]
    child = str(uuid4())
    parent["buttons"].append(button(1, "Too deep", {"type": "core.navigate", "pageId": child}))
    doc["pages"].append({"id": child, "name": "Too deep", "parentPageId": parent["id"], "buttons": []})
    assert validate(doc)["errors"]


def test_literal_argv():
    action = {"path": "/path with spaces/script", "arguments": ["$HOME", "a;b", "*.txt"],
              "interpreter": {"path": "/usr/bin/python3", "arguments": ["-u"]}}
    assert command_argv(action) == ["/usr/bin/python3", "-u", "/path with spaces/script", "$HOME", "a;b", "*.txt"]


def test_deferred_plugin_is_preserved():
    doc = demo_configuration()
    doc["pages"][0]["buttons"][1]["action"] = {"type": "core.plugin.invoke", "commandId": "future"}
    result = validate(doc)
    assert not result["errors"]
    assert any(w["code"] == "PLUGIN_DEFERRED" for w in result["warnings"])
