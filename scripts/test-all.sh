#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export PYTHONDONTWRITEBYTECODE=1
export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
# Independent processes avoid collisions between similarly named test/core modules.
(cd controller && PYTHONPATH="$PWD/src" python -B -m pytest -q && python -B scripts/check-source.py)
(cd configurator && PYTHONPATH="$PWD/src" python -B -m pytest -q && python -B scripts/check-source.py)
python -B plugins/tools/verify_package.py plugins/cpu
python -B -m unittest discover -s plugins/tests -v
bash -n controller/scripts/*.sh configurator/scripts/*.sh plugins/test.sh scripts/test-all.sh
