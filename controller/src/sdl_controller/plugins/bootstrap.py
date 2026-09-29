"""Trusted launch gate, executed by absolute path with isolated stdlib Python.

No plugin bytes execute until the host verifies the live cgroup and sends GO.
Development mode has parent-death protection but is NOT cgroup containment.
"""
import ctypes
import json
import os
import resource
import select
import signal
import sys


def main() -> None:
    parent, mode, *command = sys.argv[1:]
    if not command or mode not in {"systemd", "development"}:
        raise SystemExit(2)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if mode == "development":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != int(parent):
            raise SystemExit(3)
        os.nice(10)
    # A bounded timeout also handles cancellation while systemd is launching us.
    os.write(1, json.dumps({"gate": "ready", "pid": os.getpid()}).encode() + b"\n")
    if not select.select([0], [], [], 5)[0]:
        raise SystemExit(4)
    if os.read(0, 3) != b"GO\n":
        raise SystemExit(5)
    env = {"HOME": os.environ.get("HOME", "/"), "PATH": "/usr/bin:/bin",
           "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    os.execve(command[0], command, env)


if __name__ == "__main__":
    main()
