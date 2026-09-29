"""Data-only plugin dialogs. All runtime operations use the Controller IPC API."""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget)

from sdl_core.dynamic import DEFAULT_DYNAMIC
from sdl_core.errors import SdlError
from sdl_core.jsonutil import loads
from .dialogs import ask, notice
from .plugin_configuration import binding_for, checked_schema, display_policy, label


def plain(text):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class SettingsForm(QWidget):
    def __init__(self, contribution, values, locale="en", parent=None):
        super().__init__(parent)
        self.fields = {}
        schema = checked_schema(contribution["settingsSchema"])
        layout = QFormLayout(self)
        for name, field in schema.get("properties", {}).items():
            kind = field["type"]
            value = values.get(name, copy.deepcopy(field.get("default")))
            if "enum" in field:
                widget = QComboBox()
                for option in field["enum"]:
                    widget.addItem(str(option), option)
                position = widget.findData(value)
                widget.setCurrentIndex(max(0, position))
            elif kind == "boolean":
                widget = QCheckBox(); widget.setChecked(bool(value))
            elif kind in {"integer", "number"}:
                # Literal text handles the full JSON numeric range without QSpinBox overflow.
                widget = QLineEdit("" if value is None else str(value))
                widget.setMaxLength(128)
            elif kind == "array":
                widget = QLineEdit(json.dumps(value if value is not None else [], ensure_ascii=False))
                widget.setMaxLength(8192)
            else:
                widget = QLineEdit("" if value is None else str(value))
                widget.setMaxLength(min(1024, field.get("maxLength", 1024)))
            caption = label(contribution.get("settingsLabels", {}).get(name, field.get("title", name)), locale)
            layout.addRow(plain(caption), widget)
            self.fields[name] = (widget, field)

    def values(self):
        result = {}
        for name, (widget, field) in self.fields.items():
            if "enum" in field:
                value = widget.currentData()
            elif field["type"] == "boolean":
                value = widget.isChecked()
            elif field["type"] in {"number", "integer", "array"}:
                value = loads(widget.text())
            else:
                value = widget.text()
            result[name] = value
        return result


class PluginBindingDialog(QDialog):
    def __init__(self, parent, tr, catalog, item=None, *, offline=False):
        super().__init__(parent)
        self.tr, self.catalog = tr, catalog
        self.old = copy.deepcopy((item or {}).get("pluginBinding"))
        self.old_dynamic = copy.deepcopy((item or {}).get("appearance", {}).get("dynamic") or DEFAULT_DYNAMIC)
        self.result_binding, self.result_dynamic = None, None
        self.choice_map = [None]
        self.form = None
        self.setWindowTitle(tr("plugin_button")); self.resize(620, 760)
        layout = QVBoxLayout(self)
        layout.addWidget(plain(tr("plugin_offline_hint") if offline else tr("plugin_edit_hint")))
        self.choice = QComboBox(); self.choice.addItem(tr("plugin_none"))
        selected = 0
        for package in catalog.get("packages", []):
            for contribution in package["manifest"]["contributions"]:
                if contribution.get("capabilities") != ["display"]:
                    continue
                text = label(package["manifest"]["name"], tr.locale) + " / " + label(contribution["name"], tr.locale) + " " + package["pluginVersion"]
                if not package.get("approved"):
                    text += " — " + tr("plugin_not_approved")
                self.choice.addItem(text)
                self.choice_map.append((package, contribution))
                if self.old and all(self.old[k] == value for k, value in (("pluginId", package["pluginId"]), ("pluginVersion", package["pluginVersion"]), ("contributionId", contribution["id"]))):
                    selected = len(self.choice_map)-1
        if self.old and not selected:
            self.choice.addItem(tr("plugin_preserve_missing"))
            self.choice_map.append("preserve"); selected = len(self.choice_map)-1
        layout.addWidget(self.choice)
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        content = QWidget(); self.body = QVBoxLayout(content)
        self.description = plain(""); self.body.addWidget(self.description)
        self.fields_host = QWidget(); self.fields_layout = QVBoxLayout(self.fields_host)
        self.fields_layout.setContentsMargins(0, 0, 0, 0); self.body.addWidget(self.fields_host)
        form = QFormLayout()
        self.interval = QSpinBox(); self.interval.setRange(1000, 3600000); self.interval.setSuffix(" ms")
        form.addRow(tr("plugin_interval"), self.interval)
        self.enabled = QCheckBox(tr("plugin_dynamic_enabled")); self.enabled.setChecked(self.old_dynamic.get("enabled", True))
        form.addRow(self.enabled)
        self.template = QPlainTextEdit(self.old_dynamic.get("textTemplate", DEFAULT_DYNAMIC["textTemplate"])); self.template.setMaximumHeight(80)
        form.addRow(tr("plugin_template"), self.template)
        self.precision = QSpinBox(); self.precision.setRange(0,3); self.precision.setValue(self.old_dynamic.get("numberDecimals",0))
        form.addRow(tr("plugin_precision"), self.precision)
        self.stale = QSpinBox(); self.stale.setRange(1000,3600000); self.stale.setSuffix(" ms"); self.stale.setValue(self.old_dynamic.get("staleAfterMs",10000))
        form.addRow(tr("plugin_stale"), self.stale)
        self.overrides = {}
        for key in ("text", "icon", "progress"):
            box = QCheckBox(tr("plugin_override_" + key)); box.setChecked(key in self.old_dynamic.get("allowedOverrides", ["text", "progress"]))
            self.overrides[key] = box; form.addRow(box)
        self.body.addLayout(form); self.body.addWidget(plain(tr("plugin_limits_hint"))); self.body.addStretch()
        scroll.setWidget(content); layout.addWidget(scroll)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.choice.currentIndexChanged.connect(self.changed)
        self.choice.setCurrentIndex(selected); self.changed()

    def changed(self, *_):
        if self.form:
            self.fields_layout.removeWidget(self.form); self.form.deleteLater(); self.form = None
        choice = self.choice_map[self.choice.currentIndex()]
        self.fields_host.setEnabled(isinstance(choice, tuple))
        if not isinstance(choice, tuple):
            self.description.setText(self.tr("plugin_preserve_missing") if choice else self.tr("plugin_none"))
            return
        package, contribution = choice
        self.description.setText(label(package["manifest"]["description"], self.tr.locale))
        same = self.old and all(self.old.get(k) == v for k, v in (("pluginId",package["pluginId"]),("pluginVersion",package["pluginVersion"]),("contributionId",contribution["id"])))
        self.form = SettingsForm(contribution, self.old["settings"] if same else {}, self.tr.locale)
        self.fields_layout.addWidget(self.form)
        self.interval.setMinimum(max(1000, contribution["minRefreshIntervalMs"]))
        self.interval.setValue(self.old["refreshIntervalMs"] if same else contribution["defaultRefreshIntervalMs"])

    def accept(self):
        choice = self.choice_map[self.choice.currentIndex()]
        if choice == "preserve":
            self.result_binding, self.result_dynamic = self.old, self.old_dynamic
        elif choice:
            try:
                package, contribution = choice
                self.result_binding = binding_for(package, contribution, self.form.values(), self.interval.value(), self.old)
                self.result_dynamic = display_policy(self.enabled.isChecked(), self.template.toPlainText(), self.precision.value(), self.stale.value(), [k for k,v in self.overrides.items() if v.isChecked()])
            except (SdlError, ValueError, TypeError) as exc:
                notice(self, self.tr, str(exc), error=True); return
        else:
            self.result_binding = self.result_dynamic = None
        super().accept()


class PluginManagerDialog(QDialog):
    def __init__(self, parent, tr, catalog):
        super().__init__(parent)
        self.owner, self.tr, self.catalog = parent, tr, catalog
        self.setWindowTitle(tr("plugins")); self.resize(850,680)
        layout = QVBoxLayout(self)
        layout.addWidget(plain(tr("plugin_trust_hint")))
        self.choice = QComboBox(); layout.addWidget(self.choice)
        self.details = QPlainTextEdit(); self.details.setReadOnly(True); layout.addWidget(self.details)
        actions = QHBoxLayout()
        for key, callback in (("plugin_approve",self.approve),("plugin_disable",self.disable),("plugin_retry",self.retry),("plugin_rescan",self.rescan)):
            button = QPushButton(tr(key)); button.clicked.connect(callback); actions.addWidget(button)
        layout.addLayout(actions)
        close = QPushButton(tr("close")); close.clicked.connect(self.reject); layout.addWidget(close)
        self.choice.currentIndexChanged.connect(self.display)
        self.replace(catalog)

    def replace(self, catalog):
        self.catalog = catalog
        self.owner.plugin_catalog = copy.deepcopy(catalog)
        self.choice.blockSignals(True); self.choice.clear()
        for package in catalog.get("packages",[]):
            self.choice.addItem(f"{label(package['manifest']['name'],self.tr.locale)} — {package['pluginId']} {package['pluginVersion']}")
        self.choice.blockSignals(False); self.display()

    def selected(self):
        index = self.choice.currentIndex()
        return self.catalog.get("packages",[])[index] if index >= 0 else None

    def display(self,*_):
        package = self.selected()
        runtime = {k:v for k,v in self.catalog.items() if k != "packages"}
        self.details.setPlainText(json.dumps({"package":package,"runtime":runtime},indent=2,ensure_ascii=False))

    def request(self,method,params=None):
        self.setEnabled(False)
        def done(value):
            self.setEnabled(True); self.replace(value)
        def failed(error):
            self.setEnabled(True); notice(self,self.tr,str(error),error=True)
        self.owner.run(self.owner.backend.rpc(method,params),done,failure=failed,busy=False)

    def approve(self):
        p = self.selected()
        if p and ask(self,self.tr,self.tr("plugin_approval_warning")+"\n\n"+json.dumps({"pluginId":p["pluginId"],"fingerprint":p["fingerprint"],"interpreterIdentity":p["interpreterIdentity"],"permissions":p["manifest"]["permissions"]},indent=2)):
            self.request("plugins.approve",{k:p[k] for k in ("pluginId","pluginVersion","fingerprint","interpreterIdentity")} | {"confirmed":True})

    def disable(self):
        p = self.selected()
        if p:
            self.request("plugins.setEnabled",{"pluginId":p["pluginId"],"pluginVersion":p["pluginVersion"],"enabled":False})

    def retry(self):
        p = self.selected()
        if p:
            self.request("plugins.restart",{"pluginId":p["pluginId"],"pluginVersion":p["pluginVersion"]})

    def rescan(self):
        self.request("plugins.rescan")
