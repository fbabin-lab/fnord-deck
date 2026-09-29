#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export PYTHONDONTWRITEBYTECODE=1
python3 -B plugins/tools/verify_package.py plugins/cpu
python3 -B -m unittest discover -s plugins/tests -v
