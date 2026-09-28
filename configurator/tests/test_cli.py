import json
import subprocess
import sys
from pathlib import Path

from sdl_configurator.main import main
from sdl_configurator.i18n import Messages


def test_check_runs_without_gui_or_controller(capsys,tmp_path):
    assert main(['--check','--workspace',str(tmp_path/'workspace')]) == 0
    result=json.loads(capsys.readouterr().out)
    assert result['version']=='0.1.0'
    assert result['usbOwnership'] is False and result['plugins']=='deferred'
    assert result['fontAvailable']
    assert not (tmp_path/'workspace').exists()  # diagnostics are read-only


def test_invalid_relative_workspace_rejected(capsys):
    assert main(['--check','--workspace','relative']) == 1
    assert 'absolute' in capsys.readouterr().err


def test_language_catalogs_same_keys_and_placeholders():
    from string import Formatter
    english, french=Messages('en'),Messages('fr')
    assert english.catalog.keys()==french.catalog.keys()
    for key in english.catalog:
        fields=lambda value:{f for _,f,_,_ in Formatter().parse(value) if f is not None}
        assert fields(english.catalog[key])==fields(french.catalog[key])
    assert Messages('de').locale=='en'


def test_installer_shell_syntax():
    root=Path(__file__).resolve().parents[1]
    for path in (root/'scripts').glob('*.sh'):
        subprocess.run(['bash','-n',str(path)],check=True)
