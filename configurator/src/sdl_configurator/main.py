"""Desktop entry point. --check works without Qt, USB, or a running Controller."""
from __future__ import annotations

import argparse
import json
import os
import sys
from importlib.resources import files
from pathlib import Path
from uuid import uuid4

from sdl_core.errors import SdlError
from sdl_core.jsonutil import read_json
from . import __version__
from .document import Draft
from .i18n import Messages
from .storage import EditorPaths, Workspace, WorkspaceLock


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Stream Deck Linux Configurator — edits drafts, never owns USB.")
    result.add_argument("file", nargs="?", type=Path, help="Open JSON or a portable .sdlbundle as an unapplied draft")
    result.add_argument("--simulator", action="store_true", help="Use the separate simulator socket and editor workspace")
    result.add_argument("--socket", type=Path, help="Explicit absolute Controller Unix socket path")
    result.add_argument("--workspace", type=Path, help="Separate absolute editor-only data/state directory")
    result.add_argument("--language", choices=("en", "fr"), help="Interface language (overrides saved preference)")
    result.add_argument("--check", action="store_true", help="Check core imports/font support and paths without starting the GUI")
    result.add_argument("--version", action="version", version=__version__)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    os.umask(0o077)
    try:
        paths = EditorPaths.discover(simulator=args.simulator, workspace=args.workspace, socket_path=args.socket)
        if args.check:
            from sdl_core.render import font_path
            qt = {"qtAvailable": False, "qtVersion": None, "qtImportError": None}
            try:
                from PySide6 import QtCore, QtWidgets
                qt.update(qtAvailable=True, qtVersion=QtCore.qVersion())
            except (ImportError, OSError) as exc:
                qt["qtImportError"] = str(exc)
            print(json.dumps({"version": __version__, "python": sys.version.split()[0],
                              **qt,
                              "fontAvailable": font_path().is_file(), "socket": str(paths.socket),
                              "editorData": str(paths.data), "editorState": str(paths.state),
                              "plugins": "deferred", "usbOwnership": False}, indent=2))
            return 0
        if os.getuid() == 0:
            raise SdlError("ROOT_NOT_ALLOWED", "Run the Configurator as your normal desktop user, not with sudo.")
        try:
            from PySide6.QtWidgets import QApplication
            from .dialogs import notice
            from .window import MainWindow
        except ImportError as exc:
            print(f"Qt dependencies are unavailable: {exc}\nRun ./scripts/install-ubuntu.sh as your desktop user.", file=sys.stderr)
            return 2
        workspace = Workspace(paths)
        lock = WorkspaceLock(paths.state / "configurator.lock")
        language = args.language
        prefs = paths.state / "preferences.json"
        if not language and prefs.exists():
            try:
                language = read_json(prefs, 4096).get("locale")
            except (SdlError, ValueError, AttributeError):
                pass
        tr = Messages(language or ("fr" if os.environ.get("LANG", "").lower().startswith("fr") else "en"))
        draft: Draft | None = None
        recovery_error = None
        try:
            draft = workspace.load()
        except (SdlError, ValueError, OSError, KeyError, TypeError) as exc:
            # Preserve a corrupt file before any automatic recovery writes can replace it.
            backup = paths.state / f"damaged-draft-{uuid4()}.json"
            workspace.draft_path.rename(backup)
            recovery_error = tr("corrupt_draft", path=str(backup)) + "\n" + str(exc)
            draft = Draft()
        app = QApplication([sys.argv[0]])
        app.setApplicationName("Stream Deck Configurator")
        app.setOrganizationName("Stream Deck Linux")
        app.setApplicationVersion(__version__)
        app.setStyle("Fusion")
        app.setStyleSheet(files("sdl_configurator").joinpath("theme.qss").read_text())
        window = MainWindow(workspace, tr, draft=draft, initial_file=args.file)
        screen = app.primaryScreen()
        if screen:
            geometry = screen.availableGeometry()
            window.resize(min(1530, max(800, geometry.width() - 70)), min(920, max(580, geometry.height() - 70)))
        window.show()
        if recovery_error:
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: notice(window, tr, recovery_error, error=True))
        try:
            return app.exec()
        finally:
            window.shutdown()
            lock.close()
    except (SdlError, OSError) as exc:
        print(f"Configurator startup failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
