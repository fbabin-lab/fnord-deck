"""Transient systemd unit entrypoint: preserve literal argv via a private request file."""
import os
import sys
from pathlib import Path

from sdl_core.jsonutil import read_json


def main() -> None:
    request = Path(sys.argv[1])
    spec = read_json(request)
    request.unlink()
    os.chdir(spec["cwd"])
    os.execve(spec["argv"][0], spec["argv"], spec["environment"])


if __name__ == "__main__":
    main()
