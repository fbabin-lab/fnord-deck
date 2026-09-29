"""Selected button editor. Appearance changes are draft-only signals."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QFormLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget,
)

from sdl_core.model import DEFAULT_APPEARANCE
from .widgets import combo, png_pixmap, set_combo


class Inspector(QWidget):
    appearanceChanged = Signal(dict)
    enabledChanged = Signal(bool)
    imageRequested = Signal()
    removeImageRequested = Signal()
    actionRequested = Signal()
    pluginRequested = Signal()
    sectionRequested = Signal()
    homeRequested = Signal()
    noActionRequested = Signal()
    clearRequested = Signal()
    testRequested = Signal()

    def __init__(self, tr, parent=None) -> None:
        super().__init__(parent)
        self.tr, self.loading = tr, False
        self.background, self.foreground = "#000000", "#FFFFFF"
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        self.title = QLabel(tr("select_button"))
        self.title.setObjectName("SectionTitle")
        layout.addWidget(self.title)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setObjectName("Hint")
        layout.addWidget(self.note)
        previews = QHBoxLayout()
        self.actual = QLabel()
        self.actual.setFixedSize(96, 96)
        self.actual.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.large = QLabel()
        self.large.setFixedSize(176, 176)
        self.large.setAlignment(Qt.AlignmentFlag.AlignCenter)
        previews.addWidget(self.actual, alignment=Qt.AlignmentFlag.AlignTop)
        previews.addWidget(self.large)
        layout.addLayout(previews)
        hint = QLabel(tr("preview_sizes"))
        hint.setObjectName("Hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.fields = QWidget()
        form = QFormLayout(self.fields)
        form.setContentsMargins(0, 8, 0, 0)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.enabled = QCheckBox(tr("enabled"))
        self.enabled.toggled.connect(self._enabled)
        form.addRow(self.enabled)
        self.text = QPlainTextEdit()
        self.text.setObjectName("buttonText")
        self.text.setMaximumHeight(86)
        self.text.setPlaceholderText(tr("button_text_hint"))
        self.text.textChanged.connect(self._text)
        form.addRow(tr("text"), self.text)
        self.layout_choice = combo(tr, [("iconAboveText", "icon_above_text"), ("textOnly", "text_only"),
                                        ("iconOnly", "icon_only"), ("textOverlay", "text_overlay")])
        self.layout_choice.currentIndexChanged.connect(lambda: self._emit({"layout": self.layout_choice.currentData()}))
        form.addRow(tr("layout"), self.layout_choice)
        image_row = QHBoxLayout()
        self.import_button = QPushButton(tr("import_image"))
        self.import_button.clicked.connect(self.imageRequested)
        self.remove_image = QPushButton(tr("remove"))
        self.remove_image.clicked.connect(self.removeImageRequested)
        image_row.addWidget(self.import_button)
        image_row.addWidget(self.remove_image)
        form.addRow(image_row)
        self.asset_label = QLabel()
        self.asset_label.setObjectName("Hint")
        self.asset_label.setWordWrap(True)
        form.addRow(self.asset_label)
        self.fit = combo(tr, [("contain", "contain"), ("cover", "cover"), ("stretch", "stretch")])
        self.fit.currentIndexChanged.connect(lambda: self._emit({"imageFit": self.fit.currentData()}))
        form.addRow(tr("image_fit"), self.fit)
        color_row = QHBoxLayout()
        self.bg_button = QPushButton(tr("background"))
        self.fg_button = QPushButton(tr("text_color"))
        self.bg_button.clicked.connect(lambda: self._color(True))
        self.fg_button.clicked.connect(lambda: self._color(False))
        color_row.addWidget(self.bg_button)
        color_row.addWidget(self.fg_button)
        form.addRow(color_row)
        font_row = QHBoxLayout()
        self.font_size = QSpinBox()
        self.font_size.setRange(8, 128)
        self.font_size.setSuffix(" px")
        self.bold = QCheckBox(tr("bold"))
        font_row.addWidget(self.font_size)
        font_row.addWidget(self.bold)
        self.font_size.valueChanged.connect(lambda value: self._emit({"fontSizePx": value}))
        self.bold.toggled.connect(lambda value: self._emit({"fontWeight": "bold" if value else "normal"}))
        form.addRow(tr("font"), font_row)
        self.align = combo(tr, [("left", "left"), ("center", "center"), ("right", "right")])
        self.align.currentIndexChanged.connect(lambda: self._emit({"textAlign": self.align.currentData()}))
        form.addRow(tr("alignment"), self.align)
        self.padding = QSpinBox()
        self.padding.setRange(0, 20)
        self.padding.setSuffix(" px")
        self.padding.valueChanged.connect(lambda value: self._emit({"paddingPx": value}))
        form.addRow(tr("padding"), self.padding)
        self.action_label = QLabel()
        self.action_label.setTextFormat(Qt.TextFormat.PlainText)
        self.action_label.setWordWrap(True)
        form.addRow(tr("action"), self.action_label)
        for key, signal in (("plugin_button", self.pluginRequested), ("application_script", self.actionRequested), ("create_section", self.sectionRequested),
                            ("go_home", self.homeRequested), ("no_action", self.noActionRequested)):
            control = QPushButton(tr(key))
            control.clicked.connect(signal)
            form.addRow(control)
        self.test_button = QPushButton(tr("test_action"))
        self.test_button.setObjectName("Danger")
        self.test_button.clicked.connect(self.testRequested)
        form.addRow(self.test_button)
        clear = QPushButton(tr("clear_button"))
        clear.clicked.connect(self.clearRequested)
        form.addRow(clear)
        layout.addWidget(self.fields)
        layout.addStretch()
        self.set_item(None, 0, reserved=False)

    def _emit(self, values: dict) -> None:
        if not self.loading:
            self.appearanceChanged.emit(values)

    def _enabled(self, value: bool) -> None:
        if not self.loading:
            self.enabledChanged.emit(value)

    def _text(self) -> None:
        if self.loading:
            return
        value = self.text.toPlainText()
        if len(value) > 1024 or "\x00" in value:
            self.loading = True
            position = self.text.textCursor().position()
            self.text.setPlainText(value.replace("\x00", "")[:1024])
            cursor = self.text.textCursor()
            cursor.setPosition(min(position, len(self.text.toPlainText())))
            self.text.setTextCursor(cursor)
            self.loading = False
        self._emit({"text": self.text.toPlainText()})

    def _color(self, background: bool) -> None:
        value = QColorDialog.getColor(QColor(self.background if background else self.foreground), self)
        if value.isValid():
            field = "backgroundColor" if background else "textColor"
            if background:
                self.background = value.name()
            else:
                self.foreground = value.name()
            self._colors()
            self._emit({field: value.name()})

    def _colors(self) -> None:
        self.bg_button.setToolTip(self.background)
        self.fg_button.setToolTip(self.foreground)

    def set_item(self, item: dict | None, index: int, *, reserved: bool, test_allowed: bool = False) -> None:
        self.loading = True
        self.title.setText(self.tr("key_number", number=index + 1))
        self.fields.setEnabled(not reserved)
        self.note.setText(self.tr("back_reserved") if reserved else self.tr("draft_only_hint"))
        appearance = item["appearance"] if item else {**DEFAULT_APPEARANCE, "layout": "textOnly"}
        self.enabled.setChecked(item["enabled"] if item else True)
        self.text.setPlainText(appearance["text"])
        set_combo(self.layout_choice, appearance["layout"])
        set_combo(self.fit, appearance["imageFit"])
        set_combo(self.align, appearance["textAlign"])
        self.font_size.setValue(appearance["fontSizePx"])
        self.bold.setChecked(appearance["fontWeight"] == "bold")
        self.padding.setValue(appearance["paddingPx"])
        self.background, self.foreground = appearance["backgroundColor"], appearance["textColor"]
        self._colors()
        asset = appearance["iconAssetId"]
        self.asset_label.setText((self.tr("managed_image") + " · " + asset[7:19]) if asset else self.tr("no_image"))
        self.asset_label.setToolTip(asset or "")
        self.remove_image.setEnabled(asset is not None)
        action = item["action"] if item else {"type": "core.none"}
        keys = {"core.none": "no_action", "core.home": "go_home", "core.navigate": "subsection",
                "core.execute": "application_script", "core.plugin.invoke": "plugin_deferred"}
        self.action_label.setText(self.tr(keys[action["type"]]) + ("\n" + action["path"] if action["type"] == "core.execute" else ""))
        if item and (item.get("pluginBinding") or appearance.get("dynamic") or action["type"] == "core.plugin.invoke"):
            self.note.setText(self.tr("plugin_preserved"))
        self.test_button.setEnabled(test_allowed and action["type"] == "core.execute" and bool(item and item["enabled"]) and not reserved)
        self.loading = False

    def preview(self, raw: bytes | None) -> None:
        if raw:
            image = png_pixmap(raw)
            self.actual.setPixmap(image)
            self.large.setPixmap(image.scaled(176, 176, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.actual.clear()
            self.large.clear()
