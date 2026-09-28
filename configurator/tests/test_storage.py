import asyncio
import io
import os
import stat
from pathlib import Path

import pytest
from PIL import Image

from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest, read_json
from sdl_configurator.document import Draft
from sdl_configurator.storage import EditorPaths, WorkspaceLock, read_bounded


def image_bytes(fmt='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', (140, 60), '#4f7799').save(stream, fmt)
    return stream.getvalue()


def test_offline_paths_do_not_require_runtime_directory(monkeypatch, tmp_path):
    monkeypatch.delenv('XDG_RUNTIME_DIR', raising=False)
    p = EditorPaths.discover(workspace=tmp_path / 'offline')
    assert p.socket == Path(f'/run/user/{os.getuid()}/streamdeck-linux/controller.sock')
    p.prepare()
    assert p.data.is_dir()


def test_workspace_permissions_are_private(workspace):
    for path in (workspace.paths.data, workspace.paths.state, workspace.assets.root):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700


def test_workspace_lock_prevents_two_writers(workspace):
    a = WorkspaceLock(workspace.paths.state/'lock')
    try:
        with pytest.raises(SdlError): WorkspaceLock(workspace.paths.state/'lock')
    finally: a.close()


def test_reject_directory_input(tmp_path):
    with pytest.raises(SdlError): read_bounded(tmp_path, 500)


async def test_manual_save_survives_switching_recovery_draft(workspace):
    a = Draft(); a.appearance(a.document['rootPageId'], 0, {'text': 'saved'})
    env = a.envelope(); env['savedHash'] = digest(a.document)
    await workspace.save_named(env)
    b = Draft()
    await workspace.save(b.envelope())
    named = workspace.paths.state/'drafts'/f"{a.document['configurationId']}.json"
    assert read_json(named)['document'] == a.document
    assert workspace.load().document == b.document
    assert stat.S_IMODE(named.stat().st_mode) == 0o600


async def test_autosave_retains_unsaved_indicator(workspace):
    draft = Draft(); draft.appearance(draft.document['rootPageId'], 0, {'text': 'modified'})
    await workspace.save(draft.envelope())
    assert workspace.load().dirty
    assert not workspace.paths.socket.exists()


@pytest.mark.parametrize('fmt,extension', [('PNG','png'),('JPEG','jpg'),('BMP','bmp'),('WEBP','webp'),('GIF','gif')])
async def test_image_import_is_content_decoded_managed_and_converted(workspace, tmp_path, fmt, extension):
    original = tmp_path/f'original.{extension}'
    original.write_bytes(image_bytes(fmt))
    result = await workspace.import_image(original)
    original.unlink()
    assert workspace.assets.exists(result['assetId'])
    image = workspace.assets.image(result['assetId'])
    assert image.size == (140, 60) and image.mode == 'RGBA'
    workspace.assets.verify(result['assetId'])


async def test_image_conversion_runs_while_event_loop_responsive(workspace):
    ticks = []
    async def ticker():
        for i in range(4):
            ticks.append(i)
            await asyncio.sleep(0.01)
    result, _ = await asyncio.gather(workspace.assets.import_bytes(image_bytes()), ticker())
    assert len(ticks) == 4 and result['assetId']


async def test_svg_rejects_external_resources(workspace):
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><image href="https://example.com/x.png"/></svg>'
    with pytest.raises(SdlError): await workspace.assets.import_bytes(raw, 'external.svg')


async def test_safe_svg_import(workspace):
    raw = b'<svg xmlns="http://www.w3.org/2000/svg" width="100" height="60"><rect width="100" height="60" fill="#224466"/></svg>'
    result = await workspace.assets.import_bytes(raw, 'shape.svg')
    assert workspace.assets.exists(result['assetId'])


async def test_corrupt_image_rejected_without_partial_asset(workspace):
    with pytest.raises(SdlError): await workspace.assets.import_bytes(b'not an image', 'fake.png')
    assert list(workspace.assets.root.iterdir()) == []


async def test_bundle_roundtrip_keeps_icons_and_renews_ids(workspace, tmp_path):
    asset = await workspace.assets.import_bytes(image_bytes(), 'image.png')
    draft = Draft(); root = draft.document['rootPageId']
    draft.appearance(root, 0, {'text': 'Été', 'iconAssetId': asset['assetId']})
    filename = tmp_path/'deck.sdlbundle'
    await workspace.export(filename, draft.document)
    imported = await workspace.open_external(filename)
    assert imported['configurationId'] != draft.document['configurationId']
    assert imported['pages'][0]['buttons'][0]['appearance']['text'] == 'Été'
    assert imported['pages'][0]['buttons'][0]['appearance']['iconAssetId'] == asset['assetId']


async def test_export_missing_asset_rejected(workspace, tmp_path):
    draft = Draft(); draft.appearance(draft.document['rootPageId'], 0, {'iconAssetId': 'sha256:'+'1'*64})
    with pytest.raises(SdlError): await workspace.export(tmp_path/'bad.sdlbundle', draft.document)


async def test_json_import_ignores_external_base_revision(workspace, tmp_path):
    draft = Draft(); draft.mark_applied(999, draft.document, '/someone/else.sock')
    filename = tmp_path/'external.json'
    from sdl_core.jsonutil import dumps
    filename.write_bytes(dumps(draft.envelope()))
    result = await workspace.open_external(filename)
    assert result['configurationId'] != draft.document['configurationId']
    assert 'baseRevision' not in result


async def test_pending_apply_does_not_store_review_token(workspace):
    from uuid import uuid4
    params = {'document': Draft().document, 'expectedRevision': 0, 'operationId': str(uuid4()),
              'reviewToken': 'secret-review-token', 'confirmed': True}
    await workspace.remember_pending(params)
    saved = workspace.pending()
    assert 'reviewToken' not in saved and 'confirmed' not in saved
    assert saved['socket'] == str(workspace.paths.socket)
    await workspace.clear_pending()
    assert workspace.pending() is None
