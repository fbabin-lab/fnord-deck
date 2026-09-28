import fcntl
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .errors import SdlError


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise SdlError("UNSAFE_PATH", "Application directories must not be symlinks.")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.stat()
    if info.st_uid != os.getuid() or not stat.S_ISDIR(info.st_mode):
        raise SdlError("UNSAFE_PATH", "Application directory has an unexpected owner or type.")
    if info.st_mode & 0o077:
        # Do not silently change permissions of an existing directory.
        raise SdlError("UNSAFE_PATH", f"Directory must have mode 0700: {path}")
    return path


def runtime_base() -> Path:
    raw = os.environ.get("XDG_RUNTIME_DIR")
    if not raw or not Path(raw).is_absolute():
        raise SdlError("RUNTIME_UNAVAILABLE", "XDG_RUNTIME_DIR must refer to your private login runtime directory.")
    path = Path(raw)
    if path.is_symlink() or not path.is_dir():
        raise SdlError("UNSAFE_PATH", "Invalid XDG_RUNTIME_DIR.")
    info = path.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise SdlError("UNSAFE_PATH", "XDG_RUNTIME_DIR must be owned by this user with mode 0700.")
    return path


@dataclass(frozen=True)
class Paths:
    config: Path
    data: Path
    state: Path
    cache: Path
    runtime: Path

    @classmethod
    def discover(cls, instance: str = "default") -> "Paths":
        if instance not in ("default", "simulator"):
            raise SdlError("INVALID_INSTANCE", "Instance must be default or simulator.")
        name = "streamdeck-linux" if instance == "default" else "streamdeck-linux-simulator"
        home = Path.home()

        def base(var: str, fallback: Path) -> Path:
            value = Path(os.environ.get(var, str(fallback)))
            if not value.is_absolute():
                raise SdlError("UNSAFE_PATH", f"{var} must be absolute.")
            return value

        return cls(
            base("XDG_CONFIG_HOME", home / ".config") / name,
            base("XDG_DATA_HOME", home / ".local/share") / name,
            base("XDG_STATE_HOME", home / ".local/state") / name,
            base("XDG_CACHE_HOME", home / ".cache") / name,
            runtime_base() / name,
        )

    def prepare(self) -> None:
        for path in (self.config, self.data, self.state, self.cache, self.runtime):
            private_directory(path)
        for path in (self.config / "revisions", self.data / "assets", self.runtime / "jobs", self.runtime / "uploads"):
            private_directory(path)

    @property
    def socket(self) -> Path:
        return self.runtime / "controller.sock"


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self.fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        info = os.fstat(self.fd)
        if info.st_uid != os.getuid() or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            os.close(self.fd)
            raise SdlError("UNSAFE_PATH", "Unsafe Controller lock file.")
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            os.close(self.fd)
            raise SdlError("ALREADY_RUNNING", "Controller already running. Use sdlctl status or stop its user service.") from exc
        os.ftruncate(self.fd, 0)
        os.write(self.fd, str(os.getpid()).encode())

    def close(self) -> None:
        os.close(self.fd)
