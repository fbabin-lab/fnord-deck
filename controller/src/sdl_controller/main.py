import argparse
import asyncio
import logging
import logging.handlers
import os
import signal
import sys

from sdl_core.errors import SdlError
from sdl_core.paths import Paths

from .controller import Controller


async def run(args) -> None:
    if os.getuid() == 0 and not args.simulate:
        raise SdlError("ROOT_FORBIDDEN", "Run the Controller as your desktop user, not root. Install the udev rule for USB access.")
    os.umask(0o077)
    paths = Paths.discover("simulator" if args.simulate else "default")
    paths.prepare()
    log = logging.getLogger("sdl")
    log.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stderr = logging.StreamHandler()
    stderr.setFormatter(formatter)
    log.addHandler(stderr)
    rotating = logging.handlers.RotatingFileHandler(paths.state / "controller.log", maxBytes=2 * 1024 * 1024, backupCount=2)
    rotating.setFormatter(formatter)
    log.addHandler(rotating)
    controller = Controller(paths, simulator=args.simulate, allow_execution=args.allow_execution)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    try:
        await controller.start()
        await stop.wait()
    finally:
        await controller.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Stream Deck XL Controller (Ubuntu 24.04).")
    parser.add_argument("--simulate", action="store_true", help="Use an isolated simulator; never opens USB.")
    parser.add_argument("--allow-execution", action="store_true", help="Explicitly enable real commands in the simulator.")
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (SdlError, OSError) as exc:
        code = getattr(exc, "code", "OS_ERROR")
        print(f"{code}: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
