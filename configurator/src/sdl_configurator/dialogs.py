"""Explicit edit/review dialogs; none execute local programs."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from sdl_core.errors import SdlError
from sdl_core.model import button, command_argv, empty_configuration, validate

from .widgets import PathField, RowsEditor, combo, set_combo


def notice(parent, tr, text: str, *, error: bool = False) -> None:
    box = QMessageBox(parent)
    box.setWindowTitle(tr("error" if error else "information"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    box.setIcon(QMessageBox.Icon.Warning if error else QMessageBox.Icon.Information)
    box.exec()


def ask(parent, tr, text: str) -> bool:
    box = QMessageBox(parent)
    box.setWindowTitle(tr("confirm"))
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(text)
    yes = box.addButton(tr("continue"), QMessageBox.ButtonRole.AcceptRole)
    cancel = box.addButton(tr("cancel"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel)
    box.exec()
    return box.clickedButton() is yes


def report_text(report: dict) -> str:
    lines = []
    for kind in ("errors", "warnings"):
        for issue in report.get(kind, []):
            where = issue.get("path", "")
            if issue.get("pageId"):
                where += f" | page={issue['pageId']}"
            if issue.get("buttonId"):
                where += f" | button={issue['buttonId']}"
            lines.append(f"{issue.get('code', kind)}  {where}\n{issue.get('message', '')}")
    return "\n\n".join(lines)


class TextDialog(QDialog):
    def __init__(self, parent, tr, title: str, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(780, 580)
        layout = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlainText(text)
        layout.addWidget(self.text)
        close = QPushButton(tr("close"))
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)


class ReviewDialog(QDialog):
    def __init__(self, parent, tr, prepared: dict) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("review_apply"))
        self.resize(860, 650)
        layout = QVBoxLayout(self)
        summary = QLabel(tr("review_summary", name=prepared["document"]["name"],
                            previous=prepared["previousName"], revision=prepared["expectedRevision"]))
        summary.setTextFormat(Qt.TextFormat.PlainText)
        summary.setWordWrap(True)
        layout.addWidget(summary)
        detail = QPlainTextEdit()
        detail.setReadOnly(True)
        actions = prepared["actions"]
        parts = [tr("apply_not_execute"), tr("plugin_apply_notice"), "", tr("execution_count", count=len(actions))]
        for action in actions:
            parts.extend(["", f"{action['page']} / {tr('key_number', number=action['keyIndex'] + 1)}",
                          tr("path") + ": " + action["path"],
                          tr("arguments") + ": " + json.dumps(action["arguments"], ensure_ascii=False),
                          tr("interpreter") + ": " + json.dumps(action["interpreter"], ensure_ascii=False),
                          tr("working_directory") + ": " + action["workingDirectory"],
                          tr("mode") + ": " + action["mode"],
                          tr("environment_keys") + ": " + ", ".join(action["environmentKeys"])])
        if prepared["warnings"]:
            parts.extend(["", tr("warnings"), report_text({"warnings": prepared["warnings"]})])
        detail.setPlainText("\n".join(parts))
        layout.addWidget(detail, 1)
        self.confirmation = QCheckBox(tr("review_acknowledgement") if actions else tr("replace_acknowledgement"))
        self.confirmation.setObjectName("reviewAcknowledgement")
        layout.addWidget(self.confirmation)
        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton(tr("apply_reviewed"), QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName("Primary")
        self.apply_button.setEnabled(False)
        buttons.addButton(tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        self.confirmation.toggled.connect(self.apply_button.setEnabled)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def default_execution() -> dict:
    return {"type": "core.execute", "path": "", "arguments": [], "interpreter": None,
            "workingDirectory": str(Path.home()), "environment": {}, "mode": "application",
            "timeoutMs": None, "concurrency": "ignoreWhileRunning", "maxParallel": 1}


class ExecutionDialog(QDialog):
    def __init__(self, parent, tr, action: dict | None = None) -> None:
        super().__init__(parent)
        self.tr = tr
        self.result_action = None
        self.loading = True
        action = copy.deepcopy(action or default_execution())
        self.setWindowTitle(tr("edit_execution"))
        self.resize(840, 760)
        outer = QVBoxLayout(self)
        hint = QLabel(tr("literal_arguments_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("Banner")
        outer.addWidget(hint)
        tabs = QTabWidget()
        basic = QWidget()
        form = QFormLayout(basic)
        self.path = PathField(tr)
        self.path.edit.setObjectName("executionPath")
        self.path.set_value(action["path"])
        form.addRow(tr("path"), self.path)
        self.mode = combo(tr, [("application", "application_mode"), ("task", "task_mode")])
        set_combo(self.mode, action["mode"])
        form.addRow(tr("mode"), self.mode)
        self.working = PathField(tr, directory=True)
        self.working.set_value(action["workingDirectory"])
        form.addRow(tr("working_directory"), self.working)
        self.arguments = RowsEditor(tr, [tr("argument")])
        self.arguments.set_rows([[v] for v in action["arguments"]])
        form.addRow(tr("arguments"), self.arguments)
        self.use_interpreter = QCheckBox(tr("use_interpreter"))
        self.use_interpreter.setChecked(action["interpreter"] is not None)
        form.addRow(self.use_interpreter)
        self.interpreter = PathField(tr)
        self.interpreter.set_value(action["interpreter"]["path"] if action["interpreter"] else "")
        form.addRow(tr("interpreter"), self.interpreter)
        self.interpreter_args = RowsEditor(tr, [tr("argument")])
        self.interpreter_args.set_rows([[v] for v in (action["interpreter"]["arguments"] if action["interpreter"] else [])])
        form.addRow(tr("interpreter_arguments"), self.interpreter_args)
        self.timeout = QSpinBox()
        self.timeout.setRange(1000, 86400000)
        self.timeout.setSingleStep(1000)
        self.timeout.setSuffix(" ms")
        self.timeout.setValue(action["timeoutMs"] or 30000)
        form.addRow(tr("timeout"), self.timeout)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(basic)
        tabs.addTab(scroll, tr("command"))
        advanced = QWidget()
        adv = QFormLayout(advanced)
        self.concurrency = combo(tr, [("ignoreWhileRunning", "ignore_running"), ("parallel", "parallel")])
        set_combo(self.concurrency, action["concurrency"])
        adv.addRow(tr("concurrency"), self.concurrency)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 4)
        self.parallel.setValue(action["maxParallel"])
        adv.addRow(tr("maximum_parallel"), self.parallel)
        self.environment = RowsEditor(tr, [tr("variable"), tr("value")], limit=64)
        self.environment.set_rows([[k, v] for k, v in action["environment"].items()])
        adv.addRow(tr("environment"), self.environment)
        note = QLabel(tr("environment_warning"))
        note.setWordWrap(True)
        adv.addRow(note)
        tabs.addTab(advanced, tr("advanced"))
        outer.addWidget(tabs, 1)
        self.summary = QPlainTextEdit()
        self.summary.setReadOnly(True)
        self.summary.setMaximumHeight(115)
        outer.addWidget(QLabel(tr("argv_preview")))
        outer.addWidget(self.summary)
        buttons = QDialogButtonBox()
        buttons.addButton(tr("save_action"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept_action)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        for widget in (self.path, self.working, self.interpreter, self.arguments, self.interpreter_args, self.environment):
            widget.changed.connect(self.update_summary)
        for widget in (self.mode, self.concurrency):
            widget.currentIndexChanged.connect(self.update_summary)
        self.use_interpreter.toggled.connect(self.update_summary)
        self.timeout.valueChanged.connect(self.update_summary)
        self.parallel.valueChanged.connect(self.update_summary)
        self.loading = False
        self.update_summary()

    def collect(self) -> dict:
        env = {}
        for key, value in self.environment.rows():
            if key in env:
                raise SdlError("INVALID_ACTION", self.tr("duplicate_environment", key=key))
            env[key] = value
        mode = self.mode.currentData()
        concurrent = self.concurrency.currentData()
        return {"type": "core.execute", "path": self.path.value(),
                "arguments": [r[0] for r in self.arguments.rows()],
                "interpreter": {"path": self.interpreter.value(), "arguments": [r[0] for r in self.interpreter_args.rows()]}
                if self.use_interpreter.isChecked() else None,
                "workingDirectory": self.working.value(), "environment": env, "mode": mode,
                "timeoutMs": self.timeout.value() if mode == "task" else None,
                "concurrency": concurrent, "maxParallel": self.parallel.value() if concurrent == "parallel" else 1}

    def update_summary(self, *args) -> None:
        if self.loading:
            return
        self.timeout.setEnabled(self.mode.currentData() == "task")
        self.parallel.setEnabled(self.concurrency.currentData() == "parallel")
        for field in (self.interpreter, self.interpreter_args):
            field.setEnabled(self.use_interpreter.isChecked())
        try:
            self.summary.setPlainText(json.dumps(command_argv(self.collect()), ensure_ascii=False, indent=2))
        except SdlError as exc:
            self.summary.setPlainText(exc.message)

    def accept_action(self) -> None:
        try:
            action = self.collect()
            document = empty_configuration()
            document["pages"][0]["buttons"] = [button(0, "", action)]
            report = validate(document)
            if report["errors"]:
                notice(self, self.tr, report_text(report), error=True)
                return
            if report["warnings"] and not ask(self, self.tr, self.tr("save_with_path_warning") + "\n\n" + report_text(report)):
                return
            self.result_action = action
            self.accept()
        except SdlError as exc:
            notice(self, self.tr, exc.message, error=True)


class SettingsDialog(QDialog):
    def __init__(self, parent, tr, document: dict, devices: list[dict]) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("deck_settings"))
        self.resize(660, 390)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(document["name"])
        self.name.setMaxLength(256)
        form.addRow(tr("configuration_name"), self.name)
        self.brightness = QSpinBox()
        self.brightness.setRange(0, 100)
        self.brightness.setSuffix(" %")
        self.brightness.setValue(document["settings"]["brightnessPercent"])
        form.addRow(tr("brightness"), self.brightness)
        self.locale = combo(tr, [("en", "english"), ("fr", "french")])
        set_combo(self.locale, document["settings"]["locale"])
        form.addRow(tr("deck_language"), self.locale)
        self.serial = QLineEdit(document["target"]["serialNumber"] or "")
        self.serial.setMaxLength(128)
        form.addRow(tr("device_serial"), self.serial)
        self.devices = combo(tr, [("", "automatic_device")])
        for item in devices:
            serial = item.get("serialNumber")
            if serial:
                self.devices.addItem(f"{item.get('model', '')} — {serial}", serial)
        self.devices.currentIndexChanged.connect(lambda: self.serial.setText(self.devices.currentData() or ""))
        form.addRow(tr("detected_devices"), self.devices)
        layout.addLayout(form)
        hint = QLabel(tr("settings_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox()
        buttons.addButton(tr("update_draft"), QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton(tr("cancel"), QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {"name": self.name.text(), "brightness": self.brightness.value(),
                "locale": self.locale.currentData(), "serial": self.serial.text() or None}
