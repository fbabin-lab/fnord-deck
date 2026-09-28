import copy

import pytest

from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest
from sdl_core.model import validate
from sdl_configurator.document import Draft, renew_identities


def tree():
    draft = Draft()
    root = draft.document['rootPageId']
    child = draft.add_section(root, 2, 'Tools')
    grandchild = draft.add_section(child, 1, 'More')
    return draft, root, child, grandchild


def test_default_is_valid():
    draft = Draft()
    assert not validate(draft.document)['errors']
    assert not draft.dirty
    assert draft.differs_from_applied


@pytest.mark.parametrize('operation', ['appearance', 'enabled', 'action', 'remove', 'add', 'move', 'paste'])
def test_generated_back_cannot_be_replaced(operation):
    draft, root, child, _ = tree()
    draft.appearance(root, 1, {'text': 'copy'})
    before = copy.deepcopy(draft.document)
    with pytest.raises(SdlError):
        if operation == 'appearance': draft.appearance(child, 0, {'text': 'bad'})
        if operation == 'enabled': draft.enabled(child, 0, False)
        if operation == 'action': draft.set_action(child, 0, {'type': 'core.home'})
        if operation == 'remove': draft.remove(child, 0)
        if operation == 'add': draft.add_section(child, 0, 'bad')
        if operation == 'move': draft.move(root, 1, child, 0)
        if operation == 'paste': draft.paste(draft.copy(root, 1), child, 0)
    assert draft.document == before


def test_section_creation_parent_link_is_atomic():
    draft, root, child, grandchild = tree()
    assert draft.item(root, 2)['action']['pageId'] == child
    assert draft.page(grandchild)['parentPageId'] == child
    assert not validate(draft.document)['errors']


def test_delete_subtree_and_undo_restore_all_ids():
    draft, root, _, _ = tree()
    before = copy.deepcopy(draft.document)
    draft.remove(root, 2)
    assert len(draft.document['pages']) == 1
    draft.undo()
    assert draft.document == before
    draft.redo()
    assert len(draft.document['pages']) == 1


def test_invalid_edit_does_not_enter_history():
    draft, _, child, _ = tree()
    n, old = len(draft.undo_stack), copy.deepcopy(draft.document)
    with pytest.raises(SdlError): draft.appearance(child, 99, {'text': 'bad'})
    assert len(draft.undo_stack) == n and draft.document == old


def test_move_section_updates_parent_and_rejects_cycle():
    draft, root, child, grandchild = tree()
    other = draft.add_section(root, 3, 'Other')
    before = copy.deepcopy(draft.document)
    with pytest.raises(SdlError): draft.move(root, 2, grandchild, 1)
    assert draft.document == before
    draft.move(child, 1, other, 1)
    assert draft.page(grandchild)['parentPageId'] == other
    assert not validate(draft.document)['errors']


def test_cross_page_occupied_destination_rejected():
    draft, root, child, _ = tree()
    draft.appearance(root, 5, {'text': 'existing'})
    with pytest.raises(SdlError): draft.move(child, 1, root, 5)


def test_same_page_drag_swaps_slots_and_stable_button_ids():
    draft = Draft(); root = draft.document['rootPageId']
    draft.appearance(root, 0, {'text': 'a'})
    draft.appearance(root, 1, {'text': 'b'})
    a, b = draft.item(root, 0)['id'], draft.item(root, 1)['id']
    draft.move(root, 0, root, 1)
    assert draft.item(root, 0)['id'] == b
    assert draft.item(root, 1)['id'] == a


def test_deep_copy_has_fresh_ids_and_valid_links():
    draft, root, _, _ = tree()
    before = {p['id'] for p in draft.document['pages']}
    draft.paste(draft.copy(root, 2), root, 3)
    assert len(draft.document['pages']) == 5
    new = {p['id'] for p in draft.document['pages']} - before
    assert len(new) == 2
    assert draft.item(root, 2)['id'] != draft.item(root, 3)['id']
    assert not validate(draft.document)['errors']


def test_paste_refuses_to_discard_existing_button():
    draft = Draft(); root = draft.document['rootPageId']
    draft.appearance(root, 0, {'text': 'kept'})
    with pytest.raises(SdlError): draft.paste(draft.copy(root, 0), root, 0)


def test_opaque_future_fields_survive_edits_and_history():
    draft = Draft(); root = draft.document['rootPageId']
    draft.appearance(root, 0, {'text': 'CPU', 'dynamic': {'source': 'future.cpu', 'fallback': 7}})
    draft.change('future', lambda d: d['pages'][0]['buttons'][0]['extensions'].update({'vendor.future': {'a': [1, 2]}}))
    draft.appearance(root, 0, {'text': 'Renamed'})
    draft.undo(); draft.redo()
    item = draft.item(root, 0)
    assert item['appearance']['dynamic'] == {'source': 'future.cpu', 'fallback': 7}
    assert item['extensions'] == {'vendor.future': {'a': [1, 2]}}


def test_import_renews_identity_and_unbinds_serial():
    draft, _, _, _ = tree()
    draft.document['target']['serialNumber'] = 'ABC'
    imported = renew_identities(draft.document)
    assert imported['configurationId'] != draft.document['configurationId']
    assert imported['rootPageId'] != draft.document['rootPageId']
    assert imported['target']['serialNumber'] is None
    assert not validate(imported)['errors']
    assert draft.document['target']['serialNumber'] == 'ABC'


def test_depth_limit_64_atomic():
    draft = Draft(); pid = draft.document['rootPageId']
    for level in range(63): pid = draft.add_section(pid, 1, f'L{level+2}')
    before = copy.deepcopy(draft.document)
    with pytest.raises(SdlError): draft.add_section(pid, 1, 'too deep')
    assert draft.document == before


def test_navigation_action_cannot_be_replaced():
    draft, root, _, _ = tree()
    with pytest.raises(SdlError): draft.set_action(root, 2, {'type': 'core.home'})


def test_copy_into_descendant_is_a_finite_new_tree():
    draft, root, _, grandchild = tree()
    draft.paste(draft.copy(root, 2), grandchild, 1)
    assert len(draft.document['pages']) == 5
    assert not validate(draft.document)['errors']


def test_save_and_apply_are_separate_states():
    draft = Draft(); root = draft.document['rootPageId']
    draft.appearance(root, 0, {'text': 'change'})
    assert draft.dirty
    draft.mark_saved()
    assert not draft.dirty and draft.differs_from_applied
    draft.mark_applied(3, draft.document, '/run/a.sock')
    assert not draft.differs_from_applied
    draft.appearance(root, 0, {'text': 'new'})
    assert draft.differs_from_applied and draft.base_revision == 3


def test_envelope_roundtrip_and_base_revision_rejection():
    draft = Draft(); draft.mark_applied(0, draft.document, '/tmp/controller.sock')
    loaded = Draft.from_envelope(draft.envelope())
    assert loaded.document == draft.document and loaded.base_revision == 0
    data = draft.envelope(); data['baseRevision'] = 'wrong'
    with pytest.raises(SdlError): Draft.from_envelope(data)


def test_typing_coalesces_undo_without_losing_clean_marker():
    draft = Draft(); root = draft.document['rootPageId']; original = digest(draft.document)
    for value in ('a', 'ab', 'abc'): draft.appearance(root, 0, {'text': value})
    assert len(draft.undo_stack) == 1
    draft.undo(); assert digest(draft.document) == original
    draft.redo(); assert draft.item(root, 0)['appearance']['text'] == 'abc'
