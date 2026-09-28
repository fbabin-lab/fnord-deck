import os
from pathlib import Path

import pytest

from sdl_configurator.storage import EditorPaths, Workspace


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    # Asset conversion subprocesses need the same exact shared-core source.
    source = str(Path(__file__).resolve().parents[1] / 'src')
    monkeypatch.setenv('PYTHONPATH', source + (os.pathsep + os.environ['PYTHONPATH'] if os.environ.get('PYTHONPATH') else ''))
    return Workspace(EditorPaths(tmp_path/'editor-data', tmp_path/'editor-state', tmp_path/'controller.sock'))
