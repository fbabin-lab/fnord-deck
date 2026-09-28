"""Shipped examples and machine-readable contracts must match the implementation."""
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from sdl_core.api_contract import METHODS
from sdl_core.model import SCHEMA, require_valid

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('name', ['demo.json', 'execute-demo.json', 'key-map.json'])
def test_complete_examples_validate(name):
    document = json.loads((ROOT / 'examples' / name).read_text())
    assert not require_valid(document)
    if name != 'execute-demo.json':
        assert all(b['action']['type'] != 'core.execute' for p in document['pages'] for b in p['buttons'])


def test_generated_configuration_and_parameter_contracts_match_runtime():
    assert json.loads((ROOT / 'schemas/configuration-v1.schema.json').read_text()) == SCHEMA
    exported = json.loads((ROOT / 'schemas/local-api-parameters-v1.json').read_text())
    assert exported['$defs'] == METHODS
    envelopes = json.loads((ROOT / 'schemas/local-api-envelopes-v1.json').read_text())
    Draft202012Validator.check_schema(envelopes)
    assert set(envelopes['x-methodResults']) == set(METHODS)


def test_wire_envelope_examples():
    schema = json.loads((ROOT / 'schemas/local-api-envelopes-v1.json').read_text())
    validator = Draft202012Validator(schema)
    validator.validate({'jsonrpc':'2.0','id':'1','method':'system.snapshot','params':{}})
    validator.validate({'jsonrpc':'2.0','id':'1','result':{}})
    validator.validate({'jsonrpc':'2.0','id':'1','error':{'code':-32000,'message':'Failure','data':{'code':'TEST','messageKey':'test','details':None}}})
    validator.validate({'jsonrpc':'2.0','method':'runtime.event','params':{'runtimeEpoch':'epoch','eventSequence':1,'revision':1,'type':'runtime.paused','payload':{'paused':True}}})
    assert not validator.is_valid({'jsonrpc':'2.0','method':'configuration.apply','params':{}})
    assert not validator.is_valid({'jsonrpc':'2.0','id':'1','result':{},'error':{}})
