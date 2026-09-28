"""Managed task supervisor. A private lifetime pipe cleans tasks after Controller SIGKILL.

The child has its own process group. The supervisor drains no output: stdout/stderr go
straight to the Controller's bounded readers. Detached descendants are outside the task
contract; the installed systemd service cgroup adds a second cleanup boundary.
"""
import os
import select
import signal
import subprocess
import sys
import time
from pathlib import Path

from sdl_core.jsonutil import read_json


def main() -> None:
    path = Path(sys.argv[1])
    lifetime = int(sys.argv[2])
    spec = read_json(path)
    path.unlink()
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    child = None
    try:
        # Check lifetime before launch: a dead Controller's intent must not start later.
        ready, _, _ = select.select([lifetime], [], [], 0)
        if ready and not os.read(lifetime, 1):
            raise SystemExit(125)
        child = subprocess.Popen(spec["argv"], cwd=spec["cwd"], env=spec["environment"],
                                 stdin=subprocess.DEVNULL, start_new_session=True, close_fds=True)
        while child.poll() is None and not stopping:
            ready, _, _ = select.select([lifetime], [], [], 0.05)
            if ready and not os.read(lifetime, 1):
                stopping = True
        code = child.poll()
        # Remove descendants even when the direct task exits normally.
        try:
            os.killpg(child.pid, signal.SIGTERM)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                child.poll()
                try:
                    os.killpg(child.pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.03)
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        except ProcessLookupError:
            pass
        child.wait()
        raise SystemExit(143 if stopping else (code if code is not None and code >= 0 else 1))
    except OSError as exc:
        # Do not emit argv or environments. The API exposes the configured path separately.
        print(f"Task launch failed ({type(exc).__name__}, errno={exc.errno}).", file=sys.stderr)
        raise SystemExit(126)
    finally:
        os.close(lifetime)
        if child is not None and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    main()
