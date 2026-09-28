#!/usr/bin/env python3
"""Dependency-free source checks, including Python 3.12 syntax and dangerous launch APIs.

This is not advertised as a replacement for a full type checker or Ruff.
"""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
errors = []
for path in list((ROOT/'src').rglob('*.py')) + list((ROOT/'tests').rglob('*.py')):
    source = path.read_text()
    try:
        tree = ast.parse(source, filename=str(path), feature_version=(3, 12))
    except SyntaxError as exc:
        errors.append(f'{path}: {exc}')
        continue
    if path.is_relative_to(ROOT/'src'):
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = ast.unparse(node.func)
                if name in {'eval', 'exec', 'os.system', 'os.popen', 'subprocess.getoutput', 'asyncio.create_subprocess_shell'}:
                    errors.append(f'{path}:{node.lineno}: prohibited runtime API {name}')
                if any(k.arg == 'shell' and not (isinstance(k.value, ast.Constant) and k.value.value is False) for k in node.keywords):
                    errors.append(f'{path}:{node.lineno}: shell=True prohibited')
for path in (ROOT/'schemas').rglob('*.json'):
    json.loads(path.read_text())
if errors:
    raise SystemExit('\n'.join(errors))
print('Python 3.12 syntax, JSON schemas and prohibited-launch-API checks: PASS')
