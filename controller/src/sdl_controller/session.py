"""Explicit desktop-session context; no guessed displays and no full environment copying."""
import os

from sdl_core.errors import SdlError

ALLOWLIST = {
    "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
    "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP", "DESKTOP_SESSION",
    "HOME", "USER", "LOGNAME", "PATH", "LANG", "LANGUAGE", "LC_ALL", "LC_CTYPE",
}


class SessionContextProvider:
    def __init__(self) -> None:
        self.variables = {k: v for k, v in os.environ.items() if k in ALLOWLIST}
        self.variables.setdefault("HOME", os.path.expanduser("~"))
        self.variables.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")

    def update(self, values: dict) -> dict:
        if not isinstance(values, dict) or any(k not in ALLOWLIST for k in values):
            raise SdlError("INVALID_PARAMS", "Only allowlisted session variables can be updated.")
        for key, value in values.items():
            if not isinstance(value, str) or "\0" in value or len(value) > 8192:
                raise SdlError("INVALID_PARAMS", "Invalid session variable.")
            if key == "XDG_RUNTIME_DIR" and value != os.environ.get("XDG_RUNTIME_DIR"):
                raise SdlError("INVALID_PARAMS", "Cannot replace this Controller's runtime directory.")
        # Session refresh replaces graphical variables, so stale X11/Wayland context does not linger.
        for key in ("DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS"):
            self.variables.pop(key, None)
        self.variables.update(values)
        return {"ok": True, "graphicalSessionAvailable": self.graphical}

    @property
    def graphical(self) -> bool:
        return bool(self.variables.get("DISPLAY") or self.variables.get("WAYLAND_DISPLAY"))

    def environment(self, overrides: dict) -> dict[str, str]:
        return {**self.variables, **overrides}
