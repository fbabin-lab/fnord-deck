import copy
import json
from pathlib import Path
from uuid import UUID

import pytest
from sdl_core.dynamic import DEFAULT_DYNAMIC
from sdl_core.errors import SdlError
from sdl_configurator.document import Draft
from sdl_configurator.plugin_configuration import binding_for, checked_schema, display_policy, label

ROOT = Path(__file__).resolve().parents[2]


def package():
    manifest = json.loads((ROOT/'plugins/cpu/manifest.json').read_text())
    return {'pluginId':manifest['id'], 'pluginVersion':manifest['version'], 'manifest':manifest}


def binding(previous=None, **settings):
    p = package(); contribution = p['manifest']['contributions'][0]
    return binding_for(p, contribution, settings, 10, previous)


def test_two_buttons_same_plugin_distinct_ids_independent_settings():
    a = binding(scope='total',label='All'); b = binding(scope='logicalCpu',logicalCpu=0,label='Core')
    assert UUID(a['instanceId']) != UUID(b['instanceId'])
    assert a['refreshIntervalMs'] == 1000
    assert a['settings'] != b['settings']
    updated = binding(a,scope='total',label='Renamed')
    assert updated['instanceId'] == a['instanceId']
    assert a['settings']['label'] == 'All'


def test_setting_form_rejects_remote_or_executable_constructs():
    for bad in ({'type':'object','$ref':'https://example.invalid/schema','additionalProperties':False},
                {'type':'object','additionalProperties':False,'properties':{'a':{'type':'string','pattern':'(a+)+$'}}}):
        with pytest.raises(SdlError): checked_schema(bad)
    with pytest.raises(SdlError): binding(scope='invalid')
    with pytest.raises(SdlError): binding(logicalCpu=-1)


def test_template_is_literal_substitution_not_expression():
    assert display_policy(template='{{label}}\n{{value}}{{unit}}')['enabled']
    for expression in ('{{value.__class__}}', '{{__import__("os")}}', '{{value+1}}'):
        with pytest.raises(SdlError): display_policy(template=expression)


def test_binding_retains_action_undo_copy_and_reserved_back():
    d = Draft(); root = d.document['rootPageId']
    d.set_action(root,0,{'type':'core.home'})
    d.set_plugin(root,0,binding(scope='total'),copy.deepcopy(DEFAULT_DYNAMIC))
    original=copy.deepcopy(d.item(root,0))
    assert original['action'] == {'type':'core.home'}
    d.paste(d.copy(root,0),root,1)
    assert d.item(root,1)['pluginBinding']['instanceId'] != original['pluginBinding']['instanceId']
    d.set_plugin(root,0,None,None)
    assert d.item(root,0)['action'] == original['action']
    d.undo(); assert d.item(root,0) == original
    child=d.add_section(root,2,'Subsection')
    with pytest.raises(SdlError): d.set_plugin(child,0,binding(),DEFAULT_DYNAMIC)


def test_i18n_manifest_setting_labels():
    p=package()
    assert label(p['manifest']['name'],'fr') == 'Utilisation du processeur'
    assert label(p['manifest']['name'],'xx') == 'CPU Usage'
    assert label(p['manifest']['contributions'][0]['settingsLabels']['label'],'fr') == 'Libellé'
