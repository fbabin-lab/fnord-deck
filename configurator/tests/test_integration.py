"""Real socket integration with the Controller v0.2.0 subprocess.

Set SDL_CONTROLLER_SOURCE to the extracted Controller tree. Tests are skipped only
when that optional external application source is unavailable. No USB is opened.
"""
from __future__ import annotations

import asyncio
import base64
import copy
import io
import os
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

from PIL import Image
import pytest
import pytest_asyncio

from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest
from sdl_configurator.backend import Backend
from sdl_configurator.document import Draft
from sdl_configurator.storage import EditorPaths, Workspace

ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = Path(os.environ.get('SDL_CONTROLLER_SOURCE', str(ROOT.parent/'controller')))
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not (CONTROLLER/'src/sdl_controller/main.py').is_file(), reason='Controller source not available; set SDL_CONTROLLER_SOURCE')]


@pytest_asyncio.fixture
async def live(tmp_path, request, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(ROOT/"src"))
    with tempfile.TemporaryDirectory(prefix='sdl-ci-') as runtime:
        runtime = Path(runtime)
        runtime.chmod(0o700)
        env = os.environ.copy()
        env.update({
            'XDG_RUNTIME_DIR': str(runtime),
            'XDG_CONFIG_HOME': str(tmp_path/'controller-config'),
            'XDG_DATA_HOME': str(tmp_path/'controller-data'),
            'XDG_STATE_HOME': str(tmp_path/'controller-state'),
            'XDG_CACHE_HOME': str(tmp_path/'controller-cache'),
            'PYTHONPATH': str(ROOT/'src')+os.pathsep+str(CONTROLLER/'src'),
        })
        flags = ['--simulate']
        if request.node.get_closest_marker('real_plugins'):
            import shutil
            flags.extend(['--allow-plugins','--plugin-mode','development'])
            plugin_root=tmp_path/'controller-data/streamdeck-linux-simulator/plugins'
            plugin_root.mkdir(mode=0o700,parents=True)
            plugin_root.parent.chmod(0o700)
            shutil.copytree(ROOT.parent/'plugins/cpu',plugin_root/'org.fnord.cpu/0.2.0')
        if request.node.get_closest_marker('real_execution'):
            flags.append('--allow-execution')
        with (tmp_path/'controller-output.log').open('wb') as output:
            process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'sdl_controller.main', *flags,
                                                          env=env, stdout=output, stderr=output)
            workspace = Workspace(EditorPaths(tmp_path/'editor-data', tmp_path/'editor-state',
                                              runtime/'streamdeck-linux-simulator/controller.sock'))
            backend = Backend(workspace)
            try:
                for _ in range(150):
                    if process.returncode is not None:
                        pytest.fail((tmp_path/'controller-output.log').read_text())
                    try:
                        snapshot = await backend.snapshot()
                        if snapshot['device']['deviceSynchronized']:
                            break
                    except SdlError:
                        pass
                    await asyncio.sleep(0.05)
                else:
                    pytest.fail('Controller failed to become synchronized: '+(tmp_path/'controller-output.log').read_text())
                yield backend, workspace, process, tmp_path
            finally:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 10)
                    except TimeoutError:
                        process.kill()
                        await process.wait()


async def applied_draft(backend):
    record = await backend.pull()
    draft = Draft(record['document'], base_revision=record['revision'], base_hash=digest(record['document']),
                  source_socket=str(backend.workspace.paths.socket))
    return draft


async def commit(backend, draft):
    prepared = await backend.prepare_apply(draft.document, draft.base_revision)
    result = await backend.apply_reviewed(prepared, confirmed=True)
    draft.mark_applied(result['revision'], draft.document, str(backend.workspace.paths.socket))
    return result


def task_action(marker):
    return {'type': 'core.execute', 'path': sys.executable,
            'arguments': ['-c', 'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("explicit test only")', str(marker)],
            'interpreter': None, 'workingDirectory': str(marker.parent), 'environment': {},
            'mode': 'task', 'timeoutMs': 10000, 'concurrency': 'ignoreWhileRunning', 'maxParallel': 1}


async def test_real_handshake_pull_and_independent_controller(live):
    backend, workspace, process, _ = live
    snapshot = await backend.snapshot()
    assert snapshot['runtimeVersion'] == '0.2.0'
    assert snapshot['simulator'] and not snapshot['executionEnabled']
    draft = await applied_draft(backend)
    assert not draft.differs_from_applied
    # Ending individual API client connections does not terminate the Controller.
    assert process.returncode is None
    assert (await backend.snapshot())['revision'] == draft.base_revision


async def test_draft_save_preview_do_not_change_controller(live):
    backend, workspace, _, _ = live
    draft = await applied_draft(backend)
    original = await backend.rpc('configuration.get')
    root = draft.document['rootPageId']
    draft.appearance(root, 0, {'text': 'Édition privée', 'backgroundColor': '#152334'})
    await workspace.save_named(draft.envelope())
    assert len(await backend.render(draft.document, root)) == 32
    current = await backend.rpc('configuration.get')
    assert current == original
    assert not (await backend.rpc('execution.list', {'limit': 50}))['executions']


async def test_image_upload_apply_pixel_identical_and_download(live):
    backend, workspace, _, tmp = live
    draft = await applied_draft(backend)
    path = tmp/'large-image.png'
    Image.new('RGBA', (240, 180), (29, 115, 201, 170)).save(path)
    image = await workspace.import_image(path)
    root = draft.document['rootPageId']
    draft.appearance(root, 0, {'iconAssetId': image['assetId'], 'text': 'Montréal', 'layout': 'iconAboveText'})
    result = await commit(backend, draft)
    assert result['revision'] > 0 and result['configurationApplied']
    local_preview = tmp/'preview.png'
    await backend.export_preview(local_preview, draft.document, root)
    remote = await backend.rpc('render.preview', {'pageId': root})
    with Image.open(local_preview) as left, Image.open(io.BytesIO(base64.b64decode(remote['data']))) as right:
        assert left.size == right.size == (768, 384)
        assert left.tobytes() == right.tobytes()
    fresh_workspace = Workspace(EditorPaths(tmp/'second-data', tmp/'second-state', workspace.paths.socket))
    pulled = await Backend(fresh_workspace).pull()
    assert pulled['document'] == draft.document
    assert fresh_workspace.assets.exists(image['assetId'])
    fresh_workspace.assets.verify(image['assetId'])


async def test_stale_revision_is_rejected_then_new_review_can_replace(live):
    backend, _, _, _ = live
    first = await applied_draft(backend)
    stale = copy.deepcopy(first)
    first.settings(name='First commit', brightness=70, locale='en', serial=None)
    await commit(backend, first)
    stale.settings(name='Stale draft', brightness=20, locale='fr', serial=None)
    with pytest.raises(SdlError) as err:
        await backend.prepare_apply(stale.document, stale.base_revision)
    assert err.value.code == 'REVISION_CONFLICT'
    assert (await backend.rpc('configuration.get'))['document']['name'] == 'First commit'
    prepared = await backend.prepare_apply(stale.document, None)  # UI explicitly asks before force/review.
    assert prepared['previousName'] == 'First commit'
    await backend.apply_reviewed(prepared, confirmed=True)
    assert (await backend.rpc('configuration.get'))['document']['name'] == 'Stale draft'


async def test_explicit_apply_confirmation_and_idempotent_pending_recovery(live):
    backend, workspace, _, _ = live
    draft = await applied_draft(backend)
    draft.appearance(draft.document['rootPageId'], 0, {'text': 'New version'})
    prepared = await backend.prepare_apply(draft.document, draft.base_revision)
    with pytest.raises(SdlError):
        await backend.apply_reviewed(prepared, confirmed=False)
    assert workspace.pending() is None
    # Reproduce successful commit with a lost client response.
    params = {k: prepared[k] for k in ('document', 'expectedRevision', 'operationId', 'reviewToken')}
    params['confirmed'] = True
    await workspace.remember_pending(params)
    applied = await backend.rpc('configuration.apply', params)
    resolved = await backend.resolve_pending()
    assert resolved['result']['deduplicated']
    assert resolved['result']['revision'] == applied['revision']
    assert workspace.pending() is None
    assert (await backend.rpc('configuration.get'))['revision'] == applied['revision']


async def test_pending_never_committed_executable_cannot_bypass_review(live):
    backend, workspace, _, tmp = live
    draft = await applied_draft(backend)
    draft.set_action(draft.document['rootPageId'], 0, task_action(tmp/'not-created'))
    prepared = await backend.prepare_apply(draft.document, draft.base_revision)
    await workspace.remember_pending(prepared)
    with pytest.raises(SdlError) as err:
        await backend.resolve_pending()
    assert err.value.code == 'REVIEW_REQUIRED'
    assert workspace.pending() is not None
    assert (await backend.rpc('configuration.get'))['revision'] == draft.base_revision


async def test_nested_sections_apply_navigation_and_back_preview(live):
    backend, _, _, _ = live
    draft = await applied_draft(backend)
    root = draft.document['rootPageId']
    child = draft.add_section(root, 5, 'Tools')
    grandchild = draft.add_section(child, 1, 'More')
    draft.set_action(grandchild, 1, {'type': 'core.home'})
    await commit(backend, draft)
    await backend.rpc('runtime.navigate', {'pageId': grandchild, 'expectedRevision': draft.base_revision})
    assert (await backend.snapshot())['currentPageId'] == grandchild
    frames = await backend.render(draft.document, grandchild)
    assert frames[0] != frames[31]  # generated Back vs empty key.
    assert not (await backend.rpc('execution.list', {'limit': 50}))['executions']


@pytest.mark.real_execution
async def test_safe_operations_never_launch_action_then_explicit_test_executes(live):
    backend, workspace, _, tmp = live
    marker = tmp/'action-proof.txt'
    draft = await applied_draft(backend)
    root = draft.document['rootPageId']
    draft.set_action(root, 0, task_action(marker))
    await workspace.save(draft.envelope())
    await backend.validate_local(draft.document)
    await backend.render(draft.document, root)
    await commit(backend, draft)
    await backend.rpc('runtime.navigate', {'pageId': root, 'expectedRevision': draft.base_revision})
    await backend.pull()
    await asyncio.sleep(0.15)
    assert not marker.exists()
    item = draft.item(root, 0)
    prepared = await backend.prepare_test(draft.document, item['id'])
    with pytest.raises(SdlError):
        await backend.execute_confirmed(prepared, confirmed=False)
    assert not marker.exists()
    result = await backend.execute_confirmed(prepared, confirmed=True)
    assert result['runId']
    for _ in range(150):
        if marker.exists():
            break
        await asyncio.sleep(0.05)
    assert marker.read_text() == 'explicit test only'
    await asyncio.sleep(0.1)
    assert len((await backend.rpc('execution.list', {'limit': 50}))['executions']) == 1
    draft.appearance(root, 1, {'text': 'Unapplied'})
    with pytest.raises(SdlError) as err:
        await backend.prepare_test(draft.document, item['id'])
    assert err.value.code == 'DRAFT_NOT_APPLIED'


@pytest.mark.real_plugins
async def test_editor_binding_apply_real_cpu_and_no_hidden_work(live):
    from sdl_configurator.plugin_configuration import binding_for, display_policy
    backend, workspace, process, _ = live
    catalog=await backend.plugin_catalog()
    p=catalog['packages'][0]
    assert not catalog['workers']
    await backend.rpc('plugins.approve',{k:p[k] for k in ('pluginId','pluginVersion','fingerprint','interpreterIdentity')}|{'confirmed':True})
    draft=await applied_draft(backend)
    root=draft.document['rootPageId']
    child=draft.add_section(root,0,'No metrics')
    binding=binding_for(p,p['manifest']['contributions'][0],{'scope':'total','label':'CPU'},2000)
    draft.set_plugin(root,1,binding,display_policy())
    # Offline/local rendering and saves never create demand.
    await backend.render(copy.deepcopy(draft.document),root)
    assert not (await backend.rpc('plugins.list'))['workers']
    await commit(backend,draft)
    for _ in range(140):
        state=await backend.rpc('plugins.list')
        if state['dynamicWrites'] and state['instances'] and state['instances'][0]['measurementAgeMs'] is not None:
            break
        await asyncio.sleep(.05)
    else:
        pytest.fail(str(state))
    assert len(state['workers']) == 1
    assert not state['resourceLimitsEnforced']  # Explicit development fixture only.
    await backend.rpc('runtime.navigate',{'pageId':child,'expectedRevision':draft.base_revision})
    for _ in range(50):
        state=await backend.rpc('plugins.list')
        if not state['workers'] and not state['stoppingWorkers']:break
        await asyncio.sleep(.02)
    assert not state['instances'] and not state['workers'] and not state['stoppingWorkers']
    cached=await backend.plugin_catalog(cached=True)
    assert cached['cached'] and cached['packages'][0]['pluginId'] == p['pluginId']
