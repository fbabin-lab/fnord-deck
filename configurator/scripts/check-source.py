"""Static boundaries, exact core provenance, catalogs, and Python 3.12 grammar."""
import ast
import hashlib
import json
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]
source = ROOT / 'src/sdl_configurator'
for path in source.glob('*.py'):
    tree = ast.parse(path.read_text(), filename=str(path), feature_version=(3, 12))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported = [node.module or '']
        else:
            imported = []
        assert not any(name.split('.')[0] in {'StreamDeck', 'sdl_controller', 'subprocess'} for name in imported), path
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {'eval', 'exec'}, (path, node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {'system', 'popen'}, (path, node.lineno)
manifest = json.loads((ROOT/'docs/shared-core-origin.json').read_text())
for name, checksum in manifest['files'].items():
    assert hashlib.sha256((ROOT/'src/sdl_core'/name).read_bytes()).hexdigest() == checksum, name
catalogs = {lang: json.loads((source/f'locales/{lang}.json').read_text()) for lang in ('en', 'fr')}
assert set(catalogs['en']) == set(catalogs['fr'])
for key in catalogs['en']:
    fields = lambda value: {field for _, field, _, _ in Formatter().parse(value) if field is not None}
    assert fields(catalogs['en'][key]) == fields(catalogs['fr'][key]), key
for path in source.glob('*.py'):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id == 'tr') or
                                          (isinstance(node.func, ast.Attribute) and node.func.attr == 'tr')):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                assert node.args[0].value in catalogs['en'], (path.name, node.args[0].value)
assert not list(ROOT.rglob('*.ttf')) and not list(ROOT.rglob('*.otf')), 'Do not ship fonts.'
print(f'PASS: Python 3.12 grammar, editor execution/USB boundaries, unchanged shared core, {len(catalogs["en"])} EN/FR messages, no bundled fonts.')
