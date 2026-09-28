"""Graphical login hook: import only actual allowlisted variables, then refresh Controller."""
import argparse
import asyncio
import os
import subprocess
import time

from sdl_core.paths import Paths
from sdl_controller.session import ALLOWLIST

from .client import Client


async def update() -> None:
    values = {k: v for k, v in os.environ.items() if k in ALLOWLIST}
    async with Client(Paths.discover().socket) as client:
        await client.call("session.update", {"variables": values})


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", action="store_true")
    args = parser.parse_args()
    if args.start:
        keys = sorted(k for k in ALLOWLIST if k in os.environ)
        subprocess.run(["systemctl", "--user", "import-environment", *keys], check=True)
        subprocess.run(["systemctl", "--user", "start", "sdl-controller.service"], check=True)
    for attempt in range(20):
        try:
            asyncio.run(update())
            return
        except Exception:
            if attempt == 19:
                raise SystemExit("Unable to refresh Controller session. Check systemctl --user status sdl-controller.")
            time.sleep(0.25)


if __name__ == "__main__":
    main()
