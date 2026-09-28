"""Reusable Qt widgets. Images are supplied by the shared renderer, not drawn here."""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

MIME_BUTTON = "application/x-streamdeck-linux-editor-position"


def png_pixmap(raw: bytes) -> QPixmap:
    image = QPixmap()
    if not image.loadFromData(raw, "PNG"):
        raise ValueError("Invalid preview image.")
    return image


class KeyButton(QPushButton):
    selected = Signal(int)
    opened = Signal(int)
    moved = Signal(str, int, int)
    imageDropped = Signal(int, str)

    def __init__(self, index: int, token: str, parent=None) -> None:
        super().__init__(parent)
        self.index, self.token = index, token
        self.page_id = ""
        self.reserved = False
        self.occupied = False
        self.origin = QPoint()
        self.setObjectName("DeckKey")
        self.setCheckable(True)
        self.setAcceptDrops(True)
        self.setMinimumSize(52, 52)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(lambda: self.selected.emit(self.index))

    def set_image(self, raw: bytes | None, size: int) -> None:
        self.setFixedSize(size + 12, size + 12)
        self.setIconSize(QSize(size, size))
        self.setIcon(QIcon(png_pixmap(raw)) if raw else QIcon())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.opened.emit(self.index)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if (event.buttons() & Qt.MouseButton.LeftButton and not self.reserved and self.occupied
                and (event.position().toPoint() - self.origin).manhattanLength() >= QApplication.startDragDistance()):
            mime = QMimeData()
            mime.setData(MIME_BUTTON, json.dumps({"token": self.token, "pageId": self.page_id, "index": self.index}).encode())
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.setPixmap(self.icon().pixmap(self.iconSize()))
            drag.exec(Qt.DropAction.MoveAction)
            self.setDown(False)
            return
        super().mouseMoveEvent(event)

    def dragEnterEvent(self, event) -> None:
        mime = event.mimeData()
        if not self.reserved and (mime.hasFormat(MIME_BUTTON) or
                                  (mime.hasUrls() and len(mime.urls()) == 1 and mime.urls()[0].isLocalFile())):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        if self.reserved:
            return
        mime = event.mimeData()
        if mime.hasFormat(MIME_BUTTON):
            try:
                data = bytes(mime.data(MIME_BUTTON))
                if len(data) > 2048:
                    return
                source = json.loads(data)
                if source.get("token") != self.token or type(source.get("index")) is not int:
                    return
                self.moved.emit(source["pageId"], source["index"], self.index)
            except (ValueError, TypeError, KeyError):
                return
        elif mime.hasUrls() and len(mime.urls()) == 1 and mime.urls()[0].isLocalFile():
            self.imageDropped.emit(self.index, mime.urls()[0].toLocalFile())
        else:
            return
        event.acceptProposedAction()


class PathField(QWidget):
    changed = Signal()

    def __init__(self, tr, *, directory: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.tr, self.directory = tr, directory
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        self.edit.setMaxLength(4096)
        browse = QPushButton(tr("browse"))
        layout.addWidget(self.edit, 1)
        layout.addWidget(browse)
        browse.clicked.connect(self.browse)
        self.edit.textChanged.connect(lambda: self.changed.emit())

    def browse(self) -> None:
        start = self.edit.text() or str(Path.home())
        if self.directory:
            value = QFileDialog.getExistingDirectory(self, self.tr("working_directory"), start)
        else:
            value, _ = QFileDialog.getOpenFileName(self, self.tr("choose_file"), start)
        if value:
            self.edit.setText(value)

    def value(self) -> str:
        return self.edit.text()

    def set_value(self, value: str) -> None:
        self.edit.setText(value)


class RowsEditor(QWidget):
    """One argument per row; no shell parsing, trimming, or space splitting."""
    changed = Signal()

    def __init__(self, tr, headers: list[str], *, limit: int = 128, parent=None) -> None:
        super().__init__(parent)
        self.tr, self.limit = tr, limit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setMinimumHeight(130)
        layout.addWidget(self.table)
        row = QHBoxLayout()
        for key, handler in (("add_row", self.add_row), ("remove_row", self.remove_row),
                             ("row_up", lambda: self.move(-1)), ("row_down", lambda: self.move(1))):
            control = QPushButton(tr(key))
            control.clicked.connect(handler)
            row.addWidget(control)
        layout.addLayout(row)
        self.table.cellChanged.connect(lambda *_: self.changed.emit())

    def set_rows(self, rows: list[list[str]]) -> None:
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, values in enumerate(rows):
            for c, value in enumerate(values):
                self.table.setItem(r, c, QTableWidgetItem(value))
        self.table.blockSignals(False)

    def rows(self) -> list[list[str]]:
        return [[self.table.item(r, c).text() if self.table.item(r, c) else ""
                 for c in range(self.table.columnCount())] for r in range(self.table.rowCount())]

    def add_row(self) -> None:
        if self.table.rowCount() >= self.limit:
            return
        r = self.table.rowCount()
        self.table.insertRow(r)
        for c in range(self.table.columnCount()):
            self.table.setItem(r, c, QTableWidgetItem(""))
        self.table.setCurrentCell(r, 0)
        self.table.editItem(self.table.item(r, 0))
        self.changed.emit()

    def remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.changed.emit()

    def move(self, delta: int) -> None:
        r = self.table.currentRow()
        target = r + delta
        if r < 0 or not 0 <= target < self.table.rowCount():
            return
        rows = self.rows()
        rows[r], rows[target] = rows[target], rows[r]
        self.set_rows(rows)
        self.table.setCurrentCell(target, 0)
        self.changed.emit()


def combo(tr, values: list[tuple[str, str]]) -> QComboBox:
    widget = QComboBox()
    for value, key in values:
        widget.addItem(tr(key), value)
    return widget


def set_combo(widget: QComboBox, value) -> None:
    index = widget.findData(value)
    if index >= 0:
        widget.setCurrentIndex(index)
