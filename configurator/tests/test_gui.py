"""Run on Ubuntu after installing Qt. No USB/Controller required.

These tests intentionally skip, rather than pretend to exercise a GUI, when
PySide6 is unavailable. QT_QPA_PLATFORM=offscreen is sufficient for automation.
"""
import copy
import os
import time
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pytest
pytest.importorskip('PySide6.QtWidgets', reason='Qt not installed; run GUI tests on Ubuntu after installation')
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetItem
from sdl_core.jsonutil import digest
from sdl_core.model import execution_summary
from sdl_configurator.dialogs import ExecutionDialog, ReviewDialog
from sdl_configurator.document import Draft
from sdl_configurator.i18n import Messages
from sdl_configurator.widgets import RowsEditor
from sdl_configurator.window import MainWindow

pytestmark = pytest.mark.gui


@pytest.fixture(scope='module')
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(app, workspace, monkeypatch):
    monkeypatch.setattr(MainWindow, 'poll', lambda self: None)
    # An unexpected error in a headless test must fail, not open a blocking dialog.
    def unexpected_notice(*args, **kwargs):
        raise AssertionError('Unexpected notice: '+str(args[2:]))
    monkeypatch.setattr('sdl_configurator.window.notice', unexpected_notice)
    result = MainWindow(workspace, Messages('en'), draft=Draft(), auto_load=False)
    result.poll_timer.stop()
    result.show()
    try:
        yield result
    finally:
        result.render_timer.stop(); result.autosave_timer.stop(); result.poll_timer.stop()
        result._closing = True
        result.shutdown()
        result._allow_close = True
        result.close()
        app.processEvents()


def wait_for(app, condition, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if condition(): return
        QTest.qWait(10)
    assert condition(), 'Qt callback did not complete'


def test_grid_shape_and_real_previews(window, app):
    assert len(window.keys) == 32
    wait_for(app, lambda: len(window.frames) == 32)
    assert not window.keys[0].icon().isNull()
    assert not window.inspector.actual.pixmap().isNull()


def test_text_edit_is_local_and_unicode_preserved(window):
    window.select(3)
    window.inspector.text.setPlainText('Montréal\nCafé')
    assert window.draft.item(window.page_id,3)['appearance']['text'] == 'Montréal\nCafé'
    assert window.draft.dirty
    assert not window.apply_button.isEnabled()
    window.undo()
    assert window.draft.item(window.page_id,3) is None
    window.redo()
    assert window.draft.item(window.page_id,3)['appearance']['text'] == 'Montréal\nCafé'


def test_nested_doubleclick_back_local_and_protected(window):
    root = window.page_id
    child = window.draft.add_section(root, 4, 'Tools')
    grandchild = window.draft.add_section(child, 2, 'More')
    window.refresh()
    window.open_key(4)
    assert window.page_id == child
    window.select(0)
    assert not window.inspector.fields.isEnabled()
    assert window.keys[0].reserved
    window.open_key(2)
    assert window.page_id == grandchild
    window.open_key(0)
    assert window.page_id == child
    window.open_key(0)
    assert window.page_id == root


def test_grid_doubleclick_does_not_execute(window, monkeypatch):
    root = window.page_id
    action = {'type':'core.execute','path':'/bin/true','arguments':[], 'interpreter':None,
              'workingDirectory':'/tmp','environment':{},'mode':'task','timeoutMs':1000,
              'concurrency':'ignoreWhileRunning','maxParallel':1}
    window.draft.set_action(root, 1, action)
    window.refresh()
    def prohibited(*args, **kwargs): raise AssertionError('Grid attempted execution')
    monkeypatch.setattr(window.backend, 'prepare_test', prohibited)
    monkeypatch.setattr(window.backend, 'execute_confirmed', prohibited)
    window.open_key(1)
    assert window.page_id == root and window.selected == 1


def test_apply_requires_unchecked_acknowledgement(app):
    draft = Draft()
    prepared = {'document':draft.document,'previousName':'Existing','expectedRevision':2,'warnings':[],
                'actions':execution_summary(draft.document)}
    dialog = ReviewDialog(None, Messages('en'), prepared)
    assert not dialog.apply_button.isEnabled()
    assert not dialog.confirmation.isChecked()
    dialog.confirmation.setChecked(True)
    assert dialog.apply_button.isEnabled()
    dialog.reject()


def test_argument_editor_preserves_spaces_empty_and_metacharacters(app):
    rows = RowsEditor(Messages('en'), ['Argument'])
    values = [['two words'],[''],['$(touch /do-not-execute)'],['a;b'],['é']]
    rows.set_rows(values)
    assert rows.rows() == values
    rows.table.setCurrentCell(2,0)
    rows.move(-1)
    assert rows.rows()[1] == values[2]
    rows.close()


def test_execution_dialog_collects_interpreter_and_environment_literal(app):
    dialog = ExecutionDialog(None, Messages('en'))
    dialog.path.set_value('/tmp/my script.py')
    dialog.use_interpreter.setChecked(True)
    dialog.interpreter.set_value('/usr/bin/python3')
    dialog.interpreter_args.set_rows([['-u']])
    dialog.arguments.set_rows([['two words'],[''],['$HOME']])
    dialog.environment.set_rows([['NAME','literal $value']])
    action = dialog.collect()
    assert action['arguments'] == ['two words','','$HOME']
    assert action['interpreter'] == {'path':'/usr/bin/python3','arguments':['-u']}
    assert action['environment'] == {'NAME':'literal $value'}
    assert action['timeoutMs'] is None
    dialog.reject()


def test_autosave_completes_before_reload(window, app):
    window.inspector.text.setPlainText('Autosaved draft')
    window.autosave()
    wait_for(app, lambda: window.workspace.draft_path.exists() and not window._autosaving)
    assert window.workspace.load().document == window.draft.document
    assert window.workspace.load().dirty


def test_simulator_execution_disabled_even_for_applied_action(window):
    window.connected = True
    window.snapshot = {'revision':2,'executionEnabled':False,'paused':False}
    window.draft.mark_applied(2, window.draft.document, str(window.workspace.paths.socket))
    assert window.applied_matches()
    assert not window.test_allowed()


def test_french_catalog_controls_and_back(app, workspace, monkeypatch):
    monkeypatch.setattr(MainWindow,'poll',lambda self:None)
    window = MainWindow(workspace, Messages('fr'), draft=Draft(), auto_load=False)
    try:
        assert window.save_button.text() == Messages('fr')('save_draft')
        child = window.draft.add_section(window.page_id,1,'Outils')
        window.navigate_local(child)
        window.select(0)
        assert window.inspector.note.text() == Messages('fr')('back_reserved')
    finally:
        window.render_timer.stop(); window.autosave_timer.stop(); window.poll_timer.stop()
        window._closing=True; window.shutdown(); window._allow_close=True; window.close()
