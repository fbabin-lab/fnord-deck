"""Resource, transport, installation and refresh failure isolation regressions."""
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest
from conftest import eventually
from test_plugins import SOURCE, apply, installed, layout
from sdl_controller.controller import Controller
from sdl_controller.plugins.install import install
from sdl_controller.plugins.registry import PluginRegistry, verify
from sdl_controller.plugins.transport import Wire
from sdl_core.dynamic import decode_png, validate_state
from sdl_core.errors import SdlError
from sdl_core.jsonutil import dumps, loads


FAULT_WORKER = r'''
import sys,json,time,os
mode = sys.argv[1]
sequences = {}
for line in sys.stdin.buffer:
    req=json.loads(line)
    method,p=req['method'],req['params']
    if method=='plugin.initialize': result={'apiVersion':'1.1','pluginVersion':'0.2.0','capabilities':['display','refreshMany']}
    elif method=='instance.create': result={'ready':True}
    elif method=='instance.refreshMany':
        if mode=='hang': time.sleep(60)
        if mode=='stderr': os.write(2,b'x'*131072)
        result={'results':[]}
        for ctx in p['instances']:
            key=ctx['instanceId']; sequences[key]=sequences.get(key,0)+1
            state={'value':20,'label':'CPU','unit':'%','status':'ok','message':None,'progress':.2,'image':None}
            if mode=='badpng': state['image']={'kind':'png','dataBase64':'YWJj'}
            if mode=='nan': state['value']=float('nan')
            result['results'].append({**ctx,'sequence':sequences[key],'state':state})
    else: result={'ok':True}
    print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':result}),flush=True)
    if method=='plugin.shutdown': break
'''


async def faulty(paths, mode):
    source=paths.cache/'fixture'; shutil.copytree(SOURCE,source)
    raw=FAULT_WORKER.encode(); (source/'fixture.py').write_bytes(raw)
    manifest=json.loads((source/'manifest.json').read_text())
    manifest['files']['fixture.py']=hashlib.sha256(raw).hexdigest()
    manifest['entrypoint']['scriptPath']='fixture.py'; manifest['entrypoint']['arguments']=[mode]
    (source/'manifest.json').write_text(json.dumps(manifest))
    install(source,paths)
    c=Controller(paths,simulator=True,allow_plugins=True,plugin_mode='development')
    await c.start()
    p=(await c.extensions.api('plugins.list',{}))['packages'][0]
    await c.extensions.api('plugins.approve',{k:p[k] for k in ('pluginId','pluginVersion','fingerprint','interpreterIdentity')}|{'confirmed':True})
    await eventually(lambda:c.synchronized)
    return c


async def test_unchanged_samples_do_not_render_write_or_bypass_budget(paths):
    c=await faulty(paths,'static')
    try:
        d=layout(1);d['pages'][0]['buttons'][1]['pluginBinding']['refreshIntervalMs']=1000
        await apply(c,d)
        await eventually(lambda:c.extensions.paint_count>0)
        live=next(iter(c.extensions.instances.values()))
        before=c.extensions.paint_count
        await eventually(lambda:live.refreshes>=3,timeout=5)
        assert c.extensions.paint_count == before
        assert live.skipped_identical>=2
        assert live.last_measurement is not None
        # A new queued value cannot enter an unrelated whole-page/status repaint.
        painted=copy.deepcopy(c.extensions.states())
        live.state={**live.state,'value':99};c.extensions.queue(live)
        assert c.extensions.states() == painted
    finally:await c.close()


async def test_hanging_plugin_is_cancelled_when_hidden(paths):
    c=await faulty(paths,'hang')
    try:
        d=layout(1);await apply(c,d)
        await eventually(lambda:bool(c.extensions.sessions) and next(iter(c.extensions.sessions.values())).phase=='ready')
        session=next(iter(c.extensions.sessions.values()))
        await eventually(lambda:session.members and session.busy is not None)
        pid=session.worker.pid
        start=time.monotonic();c.navigate(d['pages'][1]['id'])
        await eventually(lambda:not c.extensions.sessions and not c.extensions.retiring,timeout=1.5)
        assert time.monotonic()-start<1.5
        assert not Path(f'/proc/{pid}').exists()
        assert c.state=='ready'
    finally:await c.close()


@pytest.mark.parametrize('mode', ['stderr','badpng','nan'])
async def test_invalid_or_flooding_worker_is_stopped_without_losing_deck(paths,mode):
    c=await faulty(paths,mode)
    try:
        d=layout(1)
        d['pages'][0]['buttons'][1]['appearance']['dynamic']['allowedOverrides']=['text','icon','progress']
        await apply(c,d)
        await eventually(lambda:bool(c.extensions.reasons),timeout=6)
        assert not c.closing and c.state=='ready'
        if mode!='nan':
            assert 'org.fnord.cpu@0.2.0' in c.extensions.quarantined
        c.navigate(d['pages'][1]['id'])
        await eventually(lambda:not c.extensions.sessions and not c.extensions.retiring)
        assert c.synchronized or c.redraw.is_set()
    finally:await c.close()


async def test_resume_invalidates_activation_and_held_input(paths,monkeypatch):
    c=await installed(paths)
    try:
        await apply(c,layout(1))
        await eventually(lambda:bool(c.extensions.instances))
        old=next(iter(c.extensions.instances.values()))
        offset=c._sleep_offset
        monkeypatch.setattr(c,'sleep_offset',lambda:offset+5)
        assert c.check_resume()
        assert not c.extensions.valid(old) and not c.gate.enabled
        await eventually(lambda:c.synchronized and bool(c.extensions.instances))
        assert next(iter(c.extensions.instances.values())).activation != old.activation
    finally:await c.close()


def test_installer_idempotent_data_only_and_immutable_version(paths):
    first=install(SOURCE,paths)
    assert first['changed']
    assert not (paths.config/'plugin-approvals.json').exists()
    assert not install(SOURCE,paths)['changed']
    changed=paths.cache/'changed';shutil.copytree(SOURCE,changed)
    manifest=json.loads((changed/'manifest.json').read_text());manifest['description']['en']='Different'
    (changed/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(SdlError) as e:install(changed,paths)
    assert e.value.code=='PLUGIN_VERSION_EXISTS'
    assert verify(Path(first['path'])).fingerprint==first['fingerprint']


def test_bad_manifest_cannot_crash_discovery_or_hold_payloads(paths):
    install(SOURCE,paths)
    registry=PluginRegistry(paths.data/'plugins',paths.config/'approvals.json',paths.runtime/'snapshots')
    registry.scan();assert not next(iter(registry.packages.values())).payloads
    target=paths.data/'plugins/org.fnord.cpu/0.2.0/manifest.json'
    original=target.read_bytes()
    for field,bad in [('entrypoint',[]),('contributions',[None]),('id',{}),('assets',{'x':[]})]:
        m=json.loads(original);m[field]=bad;target.write_text(json.dumps(m))
        registry.scan();assert not registry.packages and registry.errors
    target.write_bytes(original);registry.scan();assert len(registry.packages)==1


@pytest.mark.parametrize('raw',[b'{"value":NaN}',b'{"value":1e999}',b'{"value":1,"value":2}',b'{"value":"\\ud800"}'])
def test_wire_json_rejects_nonfinite_duplicates_and_surrogates(raw):
    with pytest.raises(SdlError):loads(raw)


@pytest.mark.parametrize('raw',[b'not an image',b'\x89PNG\r\n\x1a\n'+b'\0'*200000])
def test_dynamic_images_are_bounded_before_decode(raw):
    with pytest.raises(SdlError):decode_png(raw)
