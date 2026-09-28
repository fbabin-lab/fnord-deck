import copy
import io
from uuid import uuid4

from PIL import Image
import pytest

from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest
from sdl_core.model import execution_summary
from sdl_configurator.backend import Backend
from sdl_configurator.document import Draft


class FakeClient:
    def __init__(self, server):
        self.server = server
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def call(self, method, params=None):
        self.server['calls'].append((method, copy.deepcopy(params)))
        if method == 'configuration.get':
            return {'revision': self.server['revision'], 'document': copy.deepcopy(self.server['document'])}
        if method == 'configuration.validate':
            return {'errors': [], 'warnings': []}
        if method == 'configuration.review':
            return {'contentHash': digest(params['document']), 'reviewToken': str(uuid4()),
                    'actions': execution_summary(params['document'])}
        if method == 'configuration.apply':
            error = self.server.get('apply_error')
            if error: raise error
            return {'revision': self.server['revision'] + 1, 'configurationApplied': True}
        if method == 'execution.test':
            raise self.server.get('execute_error') or SdlError('EXECUTION_DISABLED', 'disabled')
        raise AssertionError('Unexpected RPC: '+method)


def fake_backend(workspace):
    draft = Draft()
    server = {'document': copy.deepcopy(draft.document), 'revision': 7, 'calls': []}
    backend = Backend(workspace, lambda _: FakeClient(server))
    return backend, draft, server


async def test_no_confirmation_no_apply_rpc_or_pending_record(workspace):
    backend, draft, server = fake_backend(workspace)
    prepared = await backend.prepare_apply(draft.document, 7)
    with pytest.raises(SdlError): await backend.apply_reviewed(prepared, confirmed=False)
    assert workspace.pending() is None
    assert 'configuration.apply' not in [m for m, _ in server['calls']]


async def test_prepared_review_is_deep_copied(workspace):
    backend, draft, _ = fake_backend(workspace)
    prepared = await backend.prepare_apply(draft.document, 7)
    draft.appearance(draft.document['rootPageId'], 0, {'text':'Later edit'})
    assert not prepared['document']['pages'][0]['buttons']


async def test_revision_mismatch_precedes_upload_validate_review(workspace):
    backend, draft, server = fake_backend(workspace)
    with pytest.raises(SdlError) as exc: await backend.prepare_apply(draft.document, 6)
    assert exc.value.code == 'REVISION_CONFLICT'
    assert [m for m, _ in server['calls']] == ['configuration.get']


async def test_unknown_apply_outcome_retains_id_and_does_not_retry(workspace):
    backend, draft, server = fake_backend(workspace)
    prepared = await backend.prepare_apply(draft.document, 7)
    server['apply_error'] = SdlError('CONNECTION_LOST', 'no response')
    with pytest.raises(SdlError): await backend.apply_reviewed(prepared, confirmed=True)
    pending = workspace.pending()
    assert pending['operationId'] == prepared['operationId']
    assert 'reviewToken' not in pending and 'confirmed' not in pending
    assert [m for m, _ in server['calls']].count('configuration.apply') == 1
    with pytest.raises(SdlError) as exc: await backend.apply_reviewed(prepared, confirmed=True)
    assert exc.value.code == 'PENDING_APPLY'
    assert [m for m, _ in server['calls']].count('configuration.apply') == 1


@pytest.mark.parametrize('code', ['REVISION_CONFLICT', 'REVIEW_REQUIRED', 'VALIDATION_FAILED', 'OPERATION_CONFLICT'])
async def test_definite_rejection_clears_pending_record(workspace, code):
    backend, draft, server = fake_backend(workspace)
    prepared = await backend.prepare_apply(draft.document, 7)
    server['apply_error'] = SdlError(code, 'rejected')
    with pytest.raises(SdlError): await backend.apply_reviewed(prepared, confirmed=True)
    assert workspace.pending() is None


async def test_explicit_test_failure_is_never_retried(workspace):
    backend, _, server = fake_backend(workspace)
    server['execute_error'] = SdlError('CONNECTION_LOST', 'unknown result')
    prepared = {'buttonId': str(uuid4()), 'expectedRevision': 7, 'operationId': str(uuid4())}
    with pytest.raises(SdlError): await backend.execute_confirmed(prepared, confirmed=False)
    assert not server['calls']
    with pytest.raises(SdlError): await backend.execute_confirmed(prepared, confirmed=True)
    assert len(server['calls']) == 1


async def test_unknown_extension_fields_survive_edit_save_and_review(workspace):
    backend, draft, _ = fake_backend(workspace)
    draft.document['extensions']['future.version'] = {'enabled':True}
    prepared = await backend.prepare_apply(draft.document, 7)
    assert prepared['document']['extensions'] == draft.document['extensions']
    await workspace.save(draft.envelope())
    assert workspace.load().document['extensions'] == draft.document['extensions']


async def test_preview_renders_real_dimensions_with_accents(workspace):
    backend, draft, server = fake_backend(workspace)
    root = draft.document['rootPageId']
    draft.appearance(root, 2, {'text':'Récompenses', 'fontSizePx':16})
    frames = await backend.render(draft.document, root)
    assert len(frames) == 32
    with Image.open(io.BytesIO(frames[2])) as image:
        assert image.size == (96,96)
        assert image.getbbox()
    assert not server['calls']
