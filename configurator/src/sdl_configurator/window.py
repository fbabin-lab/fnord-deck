"""Main Configurator window: all hardware/state writes go through Backend."""
from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
    QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget,
)

from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, digest, dumps
from sdl_core.model import pages_by_id

from . import __version__
from .backend import Backend
from .bridge import Bridge
from .dialogs import (
    ExecutionDialog, ReviewDialog, SettingsDialog, TextDialog, ask, notice, report_text,
)
from .document import Draft, asset_ids
from .inspector import Inspector
from .plugin_dialogs import PluginBindingDialog, PluginManagerDialog
from .storage import Workspace
from .widgets import KeyButton, combo


class MainWindow(QMainWindow):
    def __init__(self, workspace: Workspace, tr, *, draft: Draft | None = None,
                 initial_file: Path | None = None, auto_load: bool = True, parent=None) -> None:
        super().__init__(parent)
        self.workspace, self.tr = workspace, tr
        self.backend = Backend(workspace)
        self.bridge = Bridge(self)
        self.draft = draft or Draft()
        self.page_id = self.draft.document["rootPageId"]
        self.selected = 0
        self.frames: list[bytes] = []
        self.clipboard: dict | None = None
        self.token = str(uuid4())
        self.snapshot: dict | None = None
        self.connected = False
        self.plugin_catalog = {"packages": []}
        self.loading_fields = False
        self._busy = 0
        self._polling = False
        self._rendering = False
        self._render_again = False
        self._autosaving = False
        self._autosave_again = False
        self._closing = False
        self._allow_close = False
        self._autosave_error_shown = False
        self.actions: dict[str, QAction] = {}
        self.setWindowTitle(tr("app_title"))
        self.resize(1530, 920)
        self._build()
        self._menus()
        self.render_timer = QTimer(self)
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(140)
        self.render_timer.timeout.connect(self.render_draft)
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.setInterval(800)
        self.autosave_timer.timeout.connect(self.autosave)
        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(3000)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.refresh()
        QTimer.singleShot(0, self.poll)
        if initial_file:
            QTimer.singleShot(0, lambda: self.may_replace(lambda: self.open_path(initial_file)))
        elif draft is None and auto_load:
            QTimer.singleShot(0, lambda: self.load_controller(initial=True))

    def _build(self) -> None:
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(20, 16, 20, 12)
        layout.setSpacing(12)
        header = QHBoxLayout()
        brand = QVBoxLayout()
        eyebrow = QLabel(self.tr("product_eyebrow"))
        eyebrow.setObjectName("Eyebrow")
        title = QLabel(self.tr("configurator"))
        title.setObjectName("Brand")
        brand.addWidget(eyebrow)
        brand.addWidget(title)
        header.addLayout(brand)
        header.addStretch()
        self.connection = QLabel(self.tr("controller_offline"))
        self.connection.setObjectName("Status")
        self.connection.setWordWrap(True)
        self.connection.setToolTip(str(self.workspace.paths.socket))
        header.addWidget(self.connection)
        self.load_button = QPushButton(self.tr("load_controller"))
        self.load_button.clicked.connect(lambda: self.load_controller())
        header.addWidget(self.load_button)
        self.save_button = QPushButton(self.tr("save_draft"))
        self.save_button.clicked.connect(lambda: self.save_draft())
        header.addWidget(self.save_button)
        self.apply_button = QPushButton(self.tr("review_apply"))
        self.apply_button.setObjectName("Primary")
        self.apply_button.clicked.connect(self.apply)
        header.addWidget(self.apply_button)
        layout.addLayout(header)
        self.banner = QLabel(self.tr("draft_only_hint"))
        self.banner.setObjectName("Banner")
        self.banner.setWordWrap(True)
        layout.addWidget(self.banner)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        sidebar = QFrame()
        sidebar.setObjectName("Panel")
        side = QVBoxLayout(sidebar)
        section_title = QLabel(self.tr("sections"))
        section_title.setObjectName("SectionTitle")
        side.addWidget(section_title)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(140)
        self.tree.currentItemChanged.connect(self.tree_selected)
        side.addWidget(self.tree, 1)
        for key, action in (("create_section", self.add_section), ("rename_section", self.rename_section),
                            ("move_section", self.move_section), ("duplicate_section", self.duplicate_section)):
            btn = QPushButton(self.tr(key))
            btn.clicked.connect(action)
            side.addWidget(btn)
        side.addSpacing(10)
        settings = QPushButton(self.tr("deck_settings"))
        settings.clicked.connect(self.settings)
        side.addWidget(settings)
        plugins = QPushButton(self.tr("plugins"))
        plugins.clicked.connect(self.manage_plugins)
        side.addWidget(plugins)
        splitter.addWidget(sidebar)
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(14, 4, 14, 0)
        top = QHBoxLayout()
        self.breadcrumb = QLabel()
        self.breadcrumb.setObjectName("SectionTitle")
        self.breadcrumb.setTextFormat(Qt.TextFormat.PlainText)
        self.breadcrumb.setWordWrap(True)
        top.addWidget(self.breadcrumb, 1)
        self.zoom = QComboBox()
        for size in (56, 64, 80, 96):
            self.zoom.addItem(f"{size} px", size)
        self.zoom.setCurrentIndex(2)
        self.zoom.currentIndexChanged.connect(self.paint_frames)
        self.zoom.setToolTip(self.tr("grid_zoom"))
        top.addWidget(self.zoom)
        center_layout.addLayout(top)
        subtitle = QLabel(self.tr("grid_hint"))
        subtitle.setObjectName("Hint")
        subtitle.setWordWrap(True)
        center_layout.addWidget(subtitle)
        deck = QFrame()
        deck.setObjectName("Deck")
        grid = QGridLayout(deck)
        grid.setContentsMargins(14, 16, 14, 14)
        grid.setSpacing(8)
        self.keys: list[KeyButton] = []
        self.key_labels: list[QLabel] = []
        for i in range(32):
            cell = QWidget()
            cell.setStyleSheet("background: transparent;")
            box = QVBoxLayout(cell)
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(3)
            key = KeyButton(i, self.token)
            key.set_image(None, 80)
            key.selected.connect(self.select)
            key.opened.connect(self.open_key)
            key.moved.connect(self.drop_move)
            key.imageDropped.connect(lambda index, path: self.import_image(Path(path), index))
            key.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            key.customContextMenuRequested.connect(lambda pos, n=i: self.key_menu(n, pos))
            key.setAccessibleName(self.tr("key_number", number=i + 1))
            label = QLabel(str(i + 1))
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("Hint")
            box.addWidget(key, alignment=Qt.AlignmentFlag.AlignCenter)
            box.addWidget(label)
            grid.addWidget(cell, i // 8, i % 8)
            self.keys.append(key)
            self.key_labels.append(label)
        deck_scroll = QScrollArea()
        deck_scroll.setWidget(deck)
        deck_scroll.setWidgetResizable(True)
        deck_scroll.setMinimumHeight(395)
        center_layout.addWidget(deck_scroll, 1)
        edit_row = QHBoxLayout()
        for key, action in (("undo", self.undo), ("redo", self.redo), ("copy_button", self.copy_button),
                            ("paste_button", self.paste_button)):
            btn = QPushButton(self.tr(key))
            btn.clicked.connect(action)
            edit_row.addWidget(btn)
        center_layout.addLayout(edit_row)
        self.revision_label = QLabel()
        self.revision_label.setWordWrap(True)
        self.revision_label.setObjectName("Hint")
        center_layout.addWidget(self.revision_label)
        self.runtime_label = QLabel()
        self.runtime_label.setObjectName("Hint")
        self.runtime_label.setWordWrap(True)
        center_layout.addWidget(self.runtime_label)
        splitter.addWidget(center)
        inspector_scroll = QScrollArea()
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setMinimumWidth(300)
        self.inspector = Inspector(self.tr)
        inspector_scroll.setWidget(self.inspector)
        self.inspector.appearanceChanged.connect(self.edit_appearance)
        self.inspector.enabledChanged.connect(lambda value: self.edit(lambda: self.draft.enabled(self.page_id, self.selected, value)))
        self.inspector.imageRequested.connect(lambda: self.import_image())
        self.inspector.removeImageRequested.connect(lambda: self.edit_appearance({"iconAssetId": None}, reload=True))
        self.inspector.actionRequested.connect(self.edit_execution)
        self.inspector.pluginRequested.connect(self.edit_plugin)
        self.inspector.sectionRequested.connect(self.add_section)
        self.inspector.homeRequested.connect(lambda: self.edit(lambda: self.draft.set_action(self.page_id, self.selected, {"type": "core.home"})))
        self.inspector.noActionRequested.connect(lambda: self.edit(lambda: self.draft.set_action(self.page_id, self.selected, {"type": "core.none"})))
        self.inspector.clearRequested.connect(self.clear_button)
        self.inspector.testRequested.connect(self.test_action)
        splitter.addWidget(inspector_scroll)
        splitter.setSizes([180, 980, 350])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(outer)
        self.statusBar().showMessage(self.tr("ready"))

    def _menus(self) -> None:
        def action(menu, key, callback, shortcut=None):
            control = QAction(self.tr(key), self)
            control.triggered.connect(callback)
            if shortcut:
                control.setShortcut(shortcut)
            menu.addAction(control)
            self.actions[key] = control
            return control
        file_menu = self.menuBar().addMenu(self.tr("file_menu"))
        for key, callback, shortcut in (
            ("new_draft", self.new_draft, QKeySequence.StandardKey.New),
            ("open_draft", self.open_dialog, QKeySequence.StandardKey.Open),
            ("open_saved", self.open_saved, None),
            ("save_draft", self.save_draft, QKeySequence.StandardKey.Save),
            ("export_bundle", self.export_bundle, None),
            ("export_json", self.export_json, None),
            ("export_preview", self.export_preview, None),
            ("close", self.close, QKeySequence.StandardKey.Quit),
        ):
            action(file_menu, key, callback, shortcut)
        edit_menu = self.menuBar().addMenu(self.tr("edit_menu"))
        # Keyboard shortcuts on text widgets retain their native copy/paste/undo.
        action(edit_menu, "undo", self.undo, "Ctrl+Alt+Z")
        action(edit_menu, "redo", self.redo, "Ctrl+Alt+Y")
        action(edit_menu, "copy_button", self.copy_button, "Ctrl+Shift+C")
        action(edit_menu, "paste_button", self.paste_button, "Ctrl+Shift+V")
        action(edit_menu, "move_button", self.move_button)
        action(edit_menu, "duplicate_button", self.duplicate_button)
        action(edit_menu, "clear_button", self.clear_button)
        action(edit_menu, "validate", self.validate)
        action(edit_menu, "deck_settings", self.settings)
        controller_menu = self.menuBar().addMenu(self.tr("controller_menu"))
        action(controller_menu, "load_controller", lambda: self.load_controller())
        action(controller_menu, "review_apply", self.apply, "Ctrl+Return")
        action(controller_menu, "resolve_apply", self.resolve_apply)
        action(controller_menu, "discard_pending", self.discard_pending)
        action(controller_menu, "pause_resume", self.pause_resume)
        action(controller_menu, "retry_device", lambda: self.run(self.backend.rpc("device.retry"), lambda _: self.poll(), "retry_device"))
        action(controller_menu, "show_section", self.show_section)
        action(controller_menu, "executions", self.execution_history)
        action(controller_menu, "diagnostics", self.export_diagnostics)
        language = self.menuBar().addMenu(self.tr("language"))
        action(language, "english", lambda: self.language("en"))
        action(language, "french", lambda: self.language("fr"))
        help_menu = self.menuBar().addMenu(self.tr("help"))
        action(help_menu, "about", lambda: notice(self, self.tr, self.tr("about_text", version=__version__)))

    def _set_busy(self, delta: int) -> None:
        self._busy = max(0, self._busy + delta)
        self.centralWidget().setEnabled(self._busy == 0)
        self.menuBar().setEnabled(self._busy == 0)
        if self._busy == 0:
            self.update_status()

    def run(self, coroutine, success, label: str = "working", failure=None, *, busy: bool = True) -> None:
        if busy:
            self._set_busy(1)
            self.statusBar().showMessage(self.tr(label))
        def done(value):
            if busy:
                self._set_busy(-1)
            if not self._closing:
                success(value)
        def failed(error):
            if busy:
                self._set_busy(-1)
            if not self._closing:
                (failure or self.show_error)(error)
        self.bridge.submit(coroutine, done, failed)

    def show_error(self, error: dict | Exception) -> None:
        if isinstance(error, SdlError):
            error = {"code": error.code, "message": error.message, "details": error.details}
        elif not isinstance(error, dict):
            error = {"code": "LOCAL_ERROR", "message": str(error), "details": None}
        text = f"{error['code']}\n{error['message']}"
        if isinstance(error.get("details"), dict) and "errors" in error["details"]:
            text += "\n\n" + report_text(error["details"])
        if error["code"] in {"CONNECTION_LOST", "PENDING_APPLY"} and self.workspace.pending_path.exists():
            text += "\n\n" + self.tr("pending_hint")
        self.statusBar().showMessage(error["code"])
        notice(self, self.tr, text, error=True)

    def edit(self, operation, *, structure: bool = True, inspector: bool = True) -> None:
        try:
            operation()
        except (SdlError, KeyError, ValueError) as exc:
            self.show_error(exc)
            self.refresh()
            return
        self.refresh(structure=structure, inspector=inspector)
        self.autosave_timer.start()

    def refresh(self, *, structure: bool = True, inspector: bool = True) -> None:
        pages = pages_by_id(self.draft.document)
        if self.page_id not in pages:
            self.page_id = self.draft.document["rootPageId"]
            self.selected = 0
        if structure:
            self.tree.blockSignals(True)
            self.tree.clear()
            items = {}
            root = self.draft.document["rootPageId"]
            pending = [root]
            children = {}
            for page in self.draft.document["pages"]:
                children.setdefault(page["parentPageId"], []).append(page["id"])
            while pending:
                pid = pending.pop()
                page = pages[pid]
                item = QTreeWidgetItem([page["name"] or self.tr("untitled_section")])
                item.setData(0, Qt.ItemDataRole.UserRole, pid)
                if page["parentPageId"] is None:
                    self.tree.addTopLevelItem(item)
                else:
                    items[page["parentPageId"]].addChild(item)
                items[pid] = item
                pending.extend(reversed(children.get(pid, [])))
            self.tree.expandAll()
            self.tree.setCurrentItem(items[self.page_id])
            self.tree.blockSignals(False)
        self.breadcrumb.setText(self.page_path(self.page_id))
        page = pages[self.page_id]
        assigned = {b["keyIndex"]: b for b in page["buttons"]}
        for i, key in enumerate(self.keys):
            key.page_id = self.page_id
            key.reserved = i == 0 and page["parentPageId"] is not None
            key.occupied = i in assigned
            key.setProperty("reserved", key.reserved)
            key.style().unpolish(key)
            key.style().polish(key)
            key.setChecked(i == self.selected)
            item = assigned.get(i)
            key.setToolTip(self.tr("back_reserved") if key.reserved else (item["appearance"]["text"] if item else self.tr("empty_button")))
        if inspector:
            self.load_inspector()
        self.update_status()
        self.render_timer.start()

    def page_path(self, page_id: str) -> str:
        pages = pages_by_id(self.draft.document)
        names = []
        while page_id is not None:
            page = pages[page_id]
            names.append(page["name"] or self.tr("untitled_section"))
            page_id = page["parentPageId"]
        return " / ".join(reversed(names))

    def load_inspector(self) -> None:
        reserved = self.selected == 0 and self.draft.page(self.page_id)["parentPageId"] is not None
        self.inspector.set_item(self.draft.item(self.page_id, self.selected), self.selected,
                                reserved=reserved, test_allowed=self.test_allowed())
        self.inspector.preview(self.frames[self.selected] if len(self.frames) == 32 else None)

    def applied_matches(self) -> bool:
        return bool(self.connected and self.snapshot and not self.draft.differs_from_applied
                    and self.draft.base_revision == self.snapshot["revision"]
                    and self.draft.source_socket == str(self.workspace.paths.socket))

    def test_allowed(self) -> bool:
        return bool(self.applied_matches() and self.snapshot.get("executionEnabled")
                    and not self.snapshot.get("paused"))

    def select(self, index: int) -> None:
        self.selected = index
        for i, key in enumerate(self.keys):
            key.setChecked(i == index)
        self.load_inspector()

    def tree_selected(self, current, previous) -> None:
        if current is not None:
            self.navigate_local(current.data(0, Qt.ItemDataRole.UserRole))

    def navigate_local(self, page_id: str) -> None:
        self.page_id = page_id
        self.selected = 0 if self.draft.page(page_id)["parentPageId"] is None else 1
        self.frames = []
        self.refresh()

    def open_key(self, index: int) -> None:
        self.select(index)
        page = self.draft.page(self.page_id)
        if index == 0 and page["parentPageId"]:
            self.navigate_local(page["parentPageId"])
        else:
            item = self.draft.item(self.page_id, index)
            if item and item["action"]["type"] == "core.navigate":
                self.navigate_local(item["action"]["pageId"])
            elif item and item["action"]["type"] == "core.home":
                self.navigate_local(self.draft.document["rootPageId"])
            # Double-clicking an executable only selects it. Never execute from a grid.

    def edit_appearance(self, values: dict, *, reload: bool = False) -> None:
        self.edit(lambda: self.draft.appearance(self.page_id, self.selected, values), structure=False, inspector=reload)

    def render_draft(self) -> None:
        if self._rendering:
            self._render_again = True
            return
        self._rendering = True
        identity = (id(self.draft), self.draft.generation, self.page_id)
        document, page_id = copy.deepcopy(self.draft.document), self.page_id
        def done(frames):
            self._rendering = False
            if identity == (id(self.draft), self.draft.generation, self.page_id):
                self.frames = frames
                self.paint_frames()
            if self._render_again:
                self._render_again = False
                self.render_timer.start()
        def failed(error):
            self._rendering = False
            if identity == (id(self.draft), self.draft.generation, self.page_id):
                self.frames = []
                self.paint_frames()
                self.statusBar().showMessage(self.tr("preview_failed") + " " + error["code"])
            if self._render_again:
                self._render_again = False
                self.render_timer.start()
        self.run(self.backend.render(document, page_id), done, failure=failed, busy=False)

    def paint_frames(self, *args) -> None:
        size = self.zoom.currentData()
        for i, key in enumerate(self.keys):
            key.set_image(self.frames[i] if len(self.frames) == 32 else None, size)
        self.inspector.preview(self.frames[self.selected] if len(self.frames) == 32 else None)

    def update_status(self) -> None:
        changed = self.tr("unsaved") if self.draft.dirty else self.tr("saved")
        self.setWindowTitle(f"{self.draft.document['name'] or self.tr('untitled')} — {self.tr('app_title')}" + (" *" if self.draft.dirty else ""))
        base = self.draft.base_revision if self.draft.base_revision is not None else "—"
        current = self.snapshot["revision"] if self.snapshot else "—"
        self.revision_label.setText(self.tr("revision_status", saved=changed, base=base, current=current,
                                           applied=self.tr("matches_applied") if self.applied_matches() else self.tr("draft_differs")))
        self.apply_button.setEnabled(self.connected and self._busy == 0)
        missing = sum(not self.workspace.assets.exists(i) for i in asset_ids(self.draft.document))
        if self.workspace.pending_path.exists():
            self.banner.setText(self.tr("pending_hint"))
        elif missing:
            self.banner.setText(self.tr("missing_images", count=missing))
        else:
            self.banner.setText(self.tr("draft_only_hint"))
        if self.connected and self.snapshot:
            device = self.snapshot["device"]
            state = device["state"]
            translated = self.tr.catalog.get("device_" + state, state)
            self.connection.setText(self.tr("connected_status", state=translated))
            self.runtime_label.setText(self.tr("runtime_status", synchronized=self.tr("yes") if device["deviceSynchronized"] else self.tr("not_yet"),
                                               paused=self.tr("yes") if self.snapshot["paused"] else self.tr("no")))
            if device.get("error"):
                self.runtime_label.setText(self.runtime_label.text() + "\n" + json.dumps(device["error"], ensure_ascii=False))
        else:
            self.connection.setText(self.tr("controller_offline"))
            self.runtime_label.setText(self.tr("offline_hint"))
        item = self.draft.item(self.page_id, self.selected)
        self.inspector.test_button.setEnabled(bool(item and item["action"]["type"] == "core.execute"
                                                   and item["enabled"] and self.test_allowed()))
        if "undo" in self.actions:
            self.actions["undo"].setEnabled(bool(self.draft.undo_stack))
            self.actions["redo"].setEnabled(bool(self.draft.redo_stack))

    def poll(self) -> None:
        if self._polling or self._closing:
            return
        self._polling = True
        def done(snapshot):
            self._polling = False
            self.snapshot, self.connected = snapshot, True
            self.update_status()
        def failed(error):
            self._polling = False
            self.connected = False
            self.update_status()
            self.connection.setToolTip(str(self.workspace.paths.socket) + "\n" + error["code"])
        self.run(self.backend.snapshot(), done, failure=failed, busy=False)

    def autosave(self) -> None:
        if self._autosaving:
            self._autosave_again = True
            return
        self._autosaving = True
        def done(_):
            self._autosaving = False
            self._autosave_error_shown = False
            if self._autosave_again:
                self._autosave_again = False
                self.autosave_timer.start()
        def failed(error):
            self._autosaving = False
            if not self._autosave_error_shown:
                self._autosave_error_shown = True
                self.show_error(error)
        self.run(self.workspace.save(self.draft.envelope()), done, failure=failed, busy=False)

    def save_draft(self, *args, after=None) -> None:
        document_hash = digest(self.draft.document)
        envelope = self.draft.envelope()
        envelope["savedHash"] = document_hash
        self.autosave_timer.stop()
        def done(_):
            self.draft.mark_saved(document_hash)
            self.update_status()
            self.statusBar().showMessage(self.tr("draft_saved"))
            if after:
                after()
        self.run(self.workspace.save_named(envelope), done, "saving")

    def may_replace(self, next_step) -> None:
        if self.draft.dirty:
            dialog = QDialog(self)
            dialog.setWindowTitle(self.tr("unsaved_changes"))
            layout = QVBoxLayout(dialog)
            label = QLabel(self.tr("unsaved_question"))
            label.setWordWrap(True)
            layout.addWidget(label)
            buttons = QDialogButtonBox()
            save = buttons.addButton(self.tr("save_and_continue"), QDialogButtonBox.ButtonRole.AcceptRole)
            discard = buttons.addButton(self.tr("discard_changes"), QDialogButtonBox.ButtonRole.DestructiveRole)
            cancel = buttons.addButton(self.tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
            cancel.setDefault(True)
            layout.addWidget(buttons)
            choice = []
            save.clicked.connect(lambda: (choice.append("save"), dialog.accept()))
            discard.clicked.connect(lambda: (choice.append("discard"), dialog.accept()))
            cancel.clicked.connect(dialog.reject)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            if choice == ["save"]:
                # Keep a separate recovery copy; opening another draft must not erase a saved one.
                envelope = self.draft.envelope()
                envelope["savedHash"] = digest(self.draft.document)
                self.run(self.workspace.save_named(envelope), lambda _: next_step(), "saving")
            else:
                next_step()
        else:
            next_step()

    def replace_draft(self, draft: Draft) -> None:
        self.draft = draft
        self.page_id = draft.document["rootPageId"]
        self.selected = 0
        self.frames = []
        self.clipboard = None
        self.refresh()
        self.autosave_timer.start()

    def new_draft(self) -> None:
        self.may_replace(lambda: self.replace_draft(Draft()))

    def open_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("open_draft"), str(Path.home()), self.tr("bundle_filter"))
        if path:
            self.may_replace(lambda: self.open_path(Path(path)))

    def open_saved(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("open_saved"),
                                             str(self.workspace.paths.state / "drafts"), self.tr("json_filter"))
        if not path:
            return
        selected = Path(path)
        trusted = selected.resolve().parent == (self.workspace.paths.state / "drafts").resolve()
        def start():
            if not trusted:
                self.open_path(selected)
                return
            async def read():
                from sdl_core.jsonutil import read_json
                from sdl_core.limits import CONFIG_BYTES
                return Draft.from_envelope(await asyncio.to_thread(read_json, selected, CONFIG_BYTES + 4096))
            self.run(read(), self.replace_draft, "opening")
        self.may_replace(start)

    def open_path(self, path: Path) -> None:
        def done(document):
            draft = Draft(document)
            draft.saved_hash = ""  # Imported data is a new, unapplied draft.
            self.replace_draft(draft)
            self.statusBar().showMessage(self.tr("imported_draft"))
        self.run(self.workspace.open_external(path), done, "opening")

    def load_controller(self, *, initial: bool = False) -> None:
        def start():
            def done(record):
                draft = Draft(record["document"], base_revision=record["revision"],
                              base_hash=digest(record["document"]), source_socket=str(self.workspace.paths.socket))
                self.replace_draft(draft)
                self.poll()
            self.run(self.backend.pull(), done, "loading", failure=(lambda _: None) if initial else None)
        if initial:
            start()
        else:
            self.may_replace(start)

    def export_bundle(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("export_bundle"), str(Path.home() / "my-streamdeck.sdlbundle"), self.tr("export_filter"))
        if path:
            if not ask(self, self.tr, self.tr("export_privacy")):
                return
            self.run(self.workspace.export(Path(path), copy.deepcopy(self.draft.document)),
                     lambda _: self.statusBar().showMessage(self.tr("exported")), "exporting")

    def export_json(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("export_json"), str(Path.home() / "my-streamdeck.json"), self.tr("json_filter"))
        if path:
            self.run(self.workspace.write_json(Path(path), copy.deepcopy(self.draft.document)),
                     lambda _: notice(self, self.tr, self.tr("json_not_portable")), "exporting")

    def export_preview(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("export_preview"), str(Path.home() / "deck-preview.png"), self.tr("png_filter"))
        if path:
            self.run(self.backend.export_preview(Path(path), copy.deepcopy(self.draft.document), self.page_id),
                     lambda _: self.statusBar().showMessage(self.tr("exported")), "exporting")

    def undo(self) -> None:
        self.edit(self.draft.undo)

    def redo(self) -> None:
        self.edit(self.draft.redo)

    def copy_button(self) -> None:
        try:
            self.clipboard = self.draft.copy(self.page_id, self.selected)
            self.statusBar().showMessage(self.tr("button_copied"))
        except SdlError as exc:
            self.show_error(exc)

    def paste_button(self) -> None:
        if self.clipboard is not None:
            self.edit(lambda: self.draft.paste(self.clipboard, self.page_id, self.selected))

    def drop_move(self, source_page: str, source_index: int, target_index: int) -> None:
        self.edit(lambda: self.draft.move(source_page, source_index, self.page_id, target_index))
        self.select(target_index)

    def clear_button(self) -> None:
        item = self.draft.item(self.page_id, self.selected)
        if item is None:
            return
        if item["action"]["type"] == "core.navigate":
            count = len(self.draft.descendants(item["action"]["pageId"]))
            text = self.tr("delete_subtree", count=count)
        else:
            text = self.tr("delete_button_question")
        if ask(self, self.tr, text):
            self.edit(lambda: self.draft.remove(self.page_id, self.selected))

    def add_section(self) -> None:
        page = self.draft.page(self.page_id)
        if self.selected == 0 and page["parentPageId"]:
            notice(self, self.tr, self.tr("back_reserved"), error=True)
            return
        name, accepted = QInputDialog.getText(self, self.tr("create_section"), self.tr("section_name"))
        if accepted and name:
            self.edit(lambda: self.draft.add_section(self.page_id, self.selected, name))

    def rename_section(self) -> None:
        name, accepted = QInputDialog.getText(self, self.tr("rename_section"), self.tr("section_name"),
                                               text=self.draft.page(self.page_id)["name"])
        if accepted:
            self.edit(lambda: self.draft.rename_section(self.page_id, name))

    def section_source(self) -> tuple[str, int] | None:
        page = self.draft.page(self.page_id)
        parent_id = page["parentPageId"]
        if parent_id is None:
            notice(self, self.tr, self.tr("root_not_movable"))
            return None
        item = next(b for b in self.draft.page(parent_id)["buttons"]
                    if b["action"].get("pageId") == self.page_id)
        return parent_id, item["keyIndex"]

    def destination(self, title: str, excluded: set[str] | None = None) -> tuple[str, int] | None:
        excluded = excluded or set()
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(520, 220)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        pages, slots = QComboBox(), QComboBox()
        for page in self.draft.document["pages"]:
            if page["id"] not in excluded:
                pages.addItem(self.page_path(page["id"]), page["id"])
        def changed(*_):
            slots.clear()
            pid = pages.currentData()
            if pid is None:
                return
            page = self.draft.page(pid)
            occupied = {b["keyIndex"] for b in page["buttons"]}
            for i in range(0 if page["parentPageId"] is None else 1, 32):
                if i not in occupied:
                    slots.addItem(self.tr("key_number", number=i + 1), i)
        pages.currentIndexChanged.connect(changed)
        changed()
        form.addRow(self.tr("section"), pages)
        form.addRow(self.tr("empty_destination"), slots)
        layout.addLayout(form)
        buttons = QDialogButtonBox()
        buttons.addButton(self.tr("continue"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(self.tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() == QDialog.DialogCode.Accepted and slots.currentData() is not None:
            return pages.currentData(), slots.currentData()
        return None

    def move_from(self, page_id: str, index: int) -> None:
        item = self.draft.item(page_id, index)
        if item is None:
            return
        excluded = self.draft.descendants(item["action"]["pageId"]) if item["action"]["type"] == "core.navigate" else set()
        result = self.destination(self.tr("move_button"), excluded)
        if result:
            self.edit(lambda: self.draft.move(page_id, index, *result))

    def duplicate_from(self, page_id: str, index: int) -> None:
        item = self.draft.item(page_id, index)
        if item is None:
            return
        result = self.destination(self.tr("duplicate_button"))
        if result:
            self.edit(lambda: self.draft.paste(self.draft.copy(page_id, index), *result))

    def move_button(self) -> None:
        self.move_from(self.page_id, self.selected)

    def duplicate_button(self) -> None:
        self.duplicate_from(self.page_id, self.selected)

    def move_section(self) -> None:
        source = self.section_source()
        if source:
            self.move_from(*source)

    def duplicate_section(self) -> None:
        source = self.section_source()
        if source:
            self.duplicate_from(*source)

    def key_menu(self, index: int, position) -> None:
        self.select(index)
        menu = QMenu(self)
        for key, callback in (("application_script", self.edit_execution), ("import_image", lambda: self.import_image()),
                              ("create_section", self.add_section), ("copy_button", self.copy_button),
                              ("paste_button", self.paste_button), ("move_button", self.move_button),
                              ("duplicate_button", self.duplicate_button), ("clear_button", self.clear_button)):
            menu.addAction(self.tr(key), callback)
        if self.keys[index].reserved:
            menu.setEnabled(False)
        menu.exec(self.keys[index].mapToGlobal(position))

    def import_image(self, path: Path | None = None, index: int | None = None) -> None:
        index = self.selected if index is None else index
        page_id = self.page_id
        if index == 0 and self.draft.page(page_id)["parentPageId"]:
            return
        if path is None:
            value, _ = QFileDialog.getOpenFileName(self, self.tr("import_image"), str(Path.home()), self.tr("image_filter"))
            if not value:
                return
            path = Path(value)
        def done(result):
            item = self.draft.item(page_id, index)
            appearance = {"iconAssetId": result["assetId"]}
            if item is None or item["appearance"]["layout"] == "textOnly":
                appearance["layout"] = "iconAboveText"
            self.edit(lambda: self.draft.appearance(page_id, index, appearance), structure=False)
            self.select(index)
            self.statusBar().showMessage(self.tr("image_imported"))
            if result.get("warnings"):
                notice(self, self.tr, json.dumps(result["warnings"], ensure_ascii=False, indent=2))
        self.run(self.workspace.import_image(path), done, "importing_image")

    def edit_execution(self) -> None:
        item = self.draft.item(self.page_id, self.selected)
        if self.selected == 0 and self.draft.page(self.page_id)["parentPageId"]:
            return
        if item and item["action"]["type"] == "core.navigate":
            notice(self, self.tr, self.tr("navigation_action_hint"))
            return
        dialog = ExecutionDialog(self, self.tr, item["action"] if item and item["action"]["type"] == "core.execute" else None)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.edit(lambda: self.draft.set_action(self.page_id, self.selected, dialog.result_action))

    def manage_plugins(self) -> None:
        def show(catalog):
            self.plugin_catalog = catalog
            PluginManagerDialog(self, self.tr, catalog).exec()
        self.run(self.backend.plugin_catalog(), show, "loading")

    def edit_plugin(self) -> None:
        page_id, index = self.page_id, self.selected
        if index == 0 and self.draft.page(page_id)["parentPageId"]:
            return
        item = copy.deepcopy(self.draft.item(page_id, index))
        def show(catalog):
            self.plugin_catalog = catalog
            dialog = PluginBindingDialog(self, self.tr, catalog, item, offline=bool(catalog.get("cached")))
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.edit(lambda: self.draft.set_plugin(page_id, index, dialog.result_binding, dialog.result_dynamic))
        self.run(self.backend.plugin_catalog(cached=not self.connected), show, "loading")

    def settings(self) -> None:
        def show(devices):
            dialog = SettingsDialog(self, self.tr, self.draft.document, devices)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.edit(lambda: self.draft.settings(**dialog.values()))
        if self.connected:
            self.run(self.backend.rpc("device.list"), lambda value: show(value["devices"]),
                     "loading", failure=lambda _: show([]))
        else:
            show([])

    def validate(self) -> None:
        def done(report):
            TextDialog(self, self.tr, self.tr("validation"), report_text(report) or self.tr("validation_passed")).exec()
        self.run(self.backend.validate_local(copy.deepcopy(self.draft.document)), done, "validating")

    def apply(self) -> None:
        if self.workspace.pending_path.exists():
            notice(self, self.tr, self.tr("pending_hint"), error=True)
            return
        expected = self.draft.base_revision if self.draft.source_socket == str(self.workspace.paths.socket) else None
        self.prepare_apply(expected)

    def prepare_apply(self, expected: int | None) -> None:
        document = copy.deepcopy(self.draft.document)
        def reviewed(prepared):
            dialog = ReviewDialog(self, self.tr, prepared)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.run(self.backend.apply_reviewed(prepared, confirmed=True),
                         lambda result: self.applied(result, prepared["document"]), "applying", failure=self.apply_failed)
        self.run(self.backend.prepare_apply(document, expected), reviewed, "preparing_apply", failure=self.apply_failed)

    def apply_failed(self, error: dict) -> None:
        if error["code"] == "REVISION_CONFLICT":
            if ask(self, self.tr, self.tr("revision_conflict")):
                # Explicit replacement, followed by a fresh review tied to the newest revision.
                # No automatic merge, baseline bump, or silent overwrite.
                self.prepare_apply(None)
            return
        self.show_error(error)
        self.poll()

    def applied(self, result: dict, document: dict) -> None:
        self.draft.mark_applied(result["revision"], document, str(self.workspace.paths.socket))
        self.autosave_timer.start()
        self.update_status()
        self.poll()
        self.statusBar().showMessage(self.tr("applied_pending_display", revision=result["revision"]))

    def resolve_apply(self) -> None:
        if not self.workspace.pending_path.exists():
            notice(self, self.tr, self.tr("no_pending"))
            return
        if ask(self, self.tr, self.tr("resolve_apply_question")):
            self.run(self.backend.resolve_pending(), lambda value: self.applied(value["result"], value["document"]), "resolving")

    def discard_pending(self) -> None:
        if self.workspace.pending_path.exists() and ask(self, self.tr, self.tr("discard_pending_question")):
            self.run(self.workspace.clear_pending(), lambda _: self.poll(), "working")

    def pause_resume(self) -> None:
        if self.connected and self.snapshot:
            self.run(self.backend.rpc("runtime.pause", {"paused": not self.snapshot["paused"]}), lambda _: self.poll(), "working")

    def show_section(self) -> None:
        if not self.applied_matches():
            notice(self, self.tr, self.tr("apply_first"), error=True)
            return
        self.run(self.backend.rpc("runtime.navigate", {"pageId": self.page_id, "expectedRevision": self.draft.base_revision}),
                 lambda _: self.poll(), "working")

    def test_action(self) -> None:
        item = self.draft.item(self.page_id, self.selected)
        if item is None or item["action"]["type"] != "core.execute":
            return
        def prepared(value):
            action = copy.deepcopy(value["action"])
            action["environment"] = {key: self.tr("value_hidden") for key in action["environment"]}
            question = self.tr("execute_warning") + "\n\n" + json.dumps(action, ensure_ascii=False, indent=2)
            if ask(self, self.tr, question):
                self.run(self.backend.execute_confirmed(value, confirmed=True),
                         lambda _: (self.poll(), notice(self, self.tr, self.tr("execution_submitted"))),
                         "executing", failure=self.test_failed)
        self.run(self.backend.prepare_test(copy.deepcopy(self.draft.document), item["id"]), prepared, "loading")

    def test_failed(self, error: dict) -> None:
        if error["code"] in {"CONNECTION_LOST", "CONTROLLER_UNAVAILABLE", "OUTCOME_UNKNOWN"}:
            error = {**error, "message": error["message"] + "\n\n" + self.tr("execution_unknown")}
        self.show_error(error)

    def execution_history(self) -> None:
        def done(value):
            dialog = TextDialog(self, self.tr, self.tr("executions"), json.dumps(value, ensure_ascii=False, indent=2))
            cancel = QPushButton(self.tr("cancel_task"))
            dialog.layout().insertWidget(1, cancel)
            def cancel_task():
                executions = value.get("executions", [])
                ids = [e["runId"] for e in executions if e.get("mode") == "task" and e.get("state") in {"running", "starting", "queued"}]
                if not ids:
                    notice(dialog, self.tr, self.tr("no_active_tasks"))
                    return
                run_id, accepted = QInputDialog.getItem(dialog, self.tr("cancel_task"), self.tr("run_id"), ids, editable=False)
                if accepted and ask(dialog, self.tr, self.tr("cancel_task_question")):
                    self.run(self.backend.rpc("execution.cancel", {"runId": run_id}), lambda _: dialog.accept(), "working")
            cancel.clicked.connect(cancel_task)
            dialog.exec()
        self.run(self.backend.rpc("execution.list", {"cursor": 0, "limit": 50, "includeOutput": False}), done, "loading")

    def export_diagnostics(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, self.tr("diagnostics"), str(Path.home() / "streamdeck-diagnostics.json"), self.tr("json_filter"))
        if not path:
            return
        async def export():
            value = await self.backend.rpc("system.diagnostics")
            value = {"configuratorVersion": __version__, "controller": value}
            await asyncio.to_thread(atomic_write, Path(path), dumps(value))
        self.run(export(), lambda _: notice(self, self.tr, self.tr("diagnostics_saved")), "exporting")

    def language(self, locale: str) -> None:
        async def save():
            await asyncio.to_thread(atomic_write, self.workspace.paths.state / "preferences.json", dumps({"locale": locale}))
        self.run(save(), lambda _: notice(self, self.tr, self.tr("language_restart")), "saving")

    def closeEvent(self, event) -> None:
        if self._allow_close:
            event.accept()
            return
        if self._closing:
            event.ignore()
            return
        if self.draft.dirty and not ask(self, self.tr, self.tr("close_recovery_question")):
            event.ignore()
            return
        if self._busy and not ask(self, self.tr, self.tr("close_busy_question")):
            event.ignore()
            return
        event.ignore()
        self._closing = True
        self.render_timer.stop()
        self.poll_timer.stop()
        self.autosave_timer.stop()
        def done(_):
            self._allow_close = True
            self.close()
        def failed(error):
            self._closing = False
            self.show_error(error)
            self.poll_timer.start()
        # Last durable recovery write completes before the event loop is closed.
        self.bridge.submit(self.workspace.save(self.draft.envelope()), done, failed)

    def shutdown(self) -> None:
        self.bridge.close()
