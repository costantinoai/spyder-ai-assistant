"""Shared helpers for live Spyder validation harnesses.

These scripts run against a real Spyder session in the `spyder-ai`
environment. They are intentionally small and explicit so the steps of each
validation remain easy to follow in the individual harnesses.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

from qtpy.QtCore import QTimer
from qtpy.QtWidgets import QApplication, QDialogButtonBox, QMessageBox

from spyder.api.plugins import Plugins
from spyder.app.mainwindow import MainWindow

from spyder_ai_assistant.utils.prompt_library import (
    normalize_chat_prompt_preset,
)
from spyder_ai_assistant.widgets.code_apply_dialog import CodeApplyDialog
from spyder_ai_assistant.widgets.exchange_delete_dialog import ExchangeDeleteDialog
from spyder_ai_assistant.widgets.chat_settings_dialog import ChatSettingsDialog
from spyder_ai_assistant.widgets.provider_profiles_dialog import (
    ProviderProfilesDialog,
)


ARTIFACT_ROOT = Path("/tmp/spyder-ai-assistant-validation")
DEFAULT_CHAT_MODEL = (
    "huihui_ai/qwen3-abliterated:30b-a3b-instruct-2507-q3_K_M"
)


def artifact_path(*parts):
    """Return one artifact path under the shared validation root."""
    path = ARTIFACT_ROOT.joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def wait_for(predicate, timeout_ms=5000, step_ms=50):
    """Process Qt events until `predicate()` succeeds or timeout expires."""
    app = QApplication.instance()
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        app.processEvents()
        value = predicate()
        if value:
            return value
        time.sleep(step_ms / 1000.0)
    app.processEvents()
    return predicate()


def wait_for_dialog(dialog_type, timeout_ms=3000, step_ms=50):
    """Return the first visible dialog of one type, if it appears."""
    return wait_for(
        lambda: next(
            (
                widget for widget in QApplication.topLevelWidgets()
                if isinstance(widget, dialog_type) and widget.isVisible()
            ),
            None,
        ),
        timeout_ms=timeout_ms,
        step_ms=step_ms,
    )


def _close_main_window(window):
    """Shut Spyder down through its normal plugin teardown path."""
    if window.closing(cancelable=False, close_immediately=True):
        QApplication.instance().quit()


def _terminate_process(window):
    """Exit the process with the recorded validation status."""
    exit_code = int(getattr(window, "_validation_exit_code", 1))
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(exit_code)


def finalize(window):
    """Close Spyder cleanly after a validation run."""
    app = QApplication.instance()
    if not getattr(window, "_validation_exit_hook_connected", False):
        app.aboutToQuit.connect(lambda: _terminate_process(window))
        window._validation_exit_hook_connected = True
    QTimer.singleShot(0, lambda: _close_main_window(window))
    QTimer.singleShot(1500, lambda: app.quit())
    QTimer.singleShot(10000, lambda: os._exit(1))


def write_json(path, payload):
    """Write one JSON artifact with pretty formatting."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def record_validation_result(window, path, payload):
    """Persist one validation result and record the intended exit code."""
    window._validation_exit_code = 1 if payload.get("errors") else 0
    write_json(path, payload)


def write_file(path, text):
    """Write a small text fixture file used by validation harnesses."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def get_ai_plugin(window):
    """Return the dockable chat plugin instance."""
    return window.get_plugin("ai_chat")


def get_chat_widget(window):
    """Return the main chat widget."""
    return get_ai_plugin(window).get_widget()


def get_editor_plugin(window):
    """Return the Editor plugin or raise with a clear message."""
    plugin = window.get_plugin(Plugins.Editor)
    if plugin is None:
        raise RuntimeError("Editor plugin not available")
    return plugin


def get_ipython_plugin(window):
    """Return the IPython Console plugin or raise with a clear message."""
    plugin = window.get_plugin(Plugins.IPythonConsole)
    if plugin is None:
        raise RuntimeError("IPython Console plugin not available")
    return plugin


def get_projects_plugin(window):
    """Return the Projects plugin or raise with a clear message."""
    plugin = window.get_plugin(Plugins.Projects)
    if plugin is None:
        raise RuntimeError("Projects plugin not available")
    return plugin


def get_provider(window):
    """Return the AI completion provider instance."""
    completions = window.get_plugin(Plugins.Completions)
    provider = completions.get_provider("ai_chat")
    if provider is None:
        raise RuntimeError("AI completion provider not available")
    return provider


def get_runtime_service(window):
    """Return the runtime context service owned by the plugin."""
    return get_ai_plugin(window)._runtime_context


def get_current_shell(window):
    """Return the active shellwidget."""
    shell = get_ipython_plugin(window).get_current_shellwidget()
    if shell is None:
        raise RuntimeError("No active shellwidget")
    return shell


def get_console_clients(window):
    """Return the current IPython console client list."""
    clients = list(get_ipython_plugin(window).get_clients() or [])
    if not clients:
        raise RuntimeError("No IPython console clients are available")
    return clients


def create_console_client(window, give_focus=False):
    """Create one new IPython console client and wait for it to appear."""
    ipython = get_ipython_plugin(window)
    before = len(ipython.get_clients() or [])
    ipython.create_new_client(give_focus=give_focus)
    clients = wait_for(
        lambda: ipython.get_clients()
        if len(ipython.get_clients() or []) >= before + 1 else None,
        timeout_ms=30000,
        step_ms=100,
    )
    if not clients:
        raise RuntimeError("Timed out waiting for a new IPython console client")
    return list(clients)[-1]


def set_current_shell(window, shellwidget):
    """Switch the active IPython console shellwidget."""
    ipython = get_ipython_plugin(window)
    ipython.set_current_shellwidget(shellwidget)
    QApplication.instance().processEvents()


def wait_for_runtime_shell_targets(widget, expected_count, timeout_ms=15000):
    """Wait until the runtime target combo lists the requested shell count."""
    return wait_for(
        lambda: len(widget._runtime_shells) >= expected_count,
        timeout_ms=timeout_ms,
        step_ms=100,
    )


def select_runtime_target(widget, shell_id):
    """Select one explicit runtime shell target in the chat toolbar."""
    normalized = str(shell_id or "").strip()
    for index in range(widget.runtime_target_combo.count()):
        if str(widget.runtime_target_combo.itemData(index) or "").strip() == normalized:
            widget.runtime_target_combo.setCurrentIndex(index)
            QApplication.instance().processEvents()
            return True
    raise RuntimeError(f"Runtime target shell not found: {normalized}")


def select_model(widget, model_name, provider_id=None):
    """Select one chat model from the widget dropdown."""
    if not wait_for(lambda: widget.model_combo.count() > 0, timeout_ms=20000):
        raise RuntimeError("Chat model list did not load")

    for index in range(widget.model_combo.count()):
        payload = widget.model_combo.itemData(index)
        if isinstance(payload, dict):
            if payload.get("name") != model_name:
                continue
            if provider_id and payload.get("provider_id") != provider_id:
                continue
            widget.model_combo.setCurrentIndex(index)
            QApplication.instance().processEvents()
            return True
        elif payload == model_name:
            widget.model_combo.setCurrentIndex(index)
            QApplication.instance().processEvents()
            return True
    raise RuntimeError(f"Requested chat model not found: {model_name}")


def select_first_provider_model(widget, provider_id):
    """Select the first available model for one provider id."""
    if not wait_for(lambda: widget.model_combo.count() > 0, timeout_ms=20000):
        raise RuntimeError("Chat model list did not load")

    for index in range(widget.model_combo.count()):
        payload = widget.model_combo.itemData(index)
        if not isinstance(payload, dict):
            continue
        if (
            payload.get("provider_id") == provider_id
            or payload.get("provider_kind") == provider_id
        ):
            widget.model_combo.setCurrentIndex(index)
            QApplication.instance().processEvents()
            return dict(payload)
    raise RuntimeError(f"No chat models available for provider: {provider_id}")


def select_prompt_preset(widget, preset_id):
    """Select one chat prompt preset from the shared toolbar combo."""
    normalized = normalize_chat_prompt_preset(preset_id)
    for index in range(widget.prompt_preset_combo.count()):
        if widget.prompt_preset_combo.itemData(index) == normalized:
            widget.prompt_preset_combo.setCurrentIndex(index)
            QApplication.instance().processEvents()
            return normalized
    raise RuntimeError(f"Requested chat prompt preset not found: {normalized}")


def apply_chat_settings(widget, temperature_override=None,
                        max_tokens_override=None, use_defaults=False):
    """Open the real chat settings dialog and apply one per-tab state."""
    state = {"error": None}

    def _configure_dialog(attempt=0):
        dialog = next(
            (
                top_level for top_level in QApplication.topLevelWidgets()
                if isinstance(top_level, ChatSettingsDialog)
                and top_level.isVisible()
            ),
            None,
        )
        if dialog is None:
            if attempt < 40:
                QTimer.singleShot(50, lambda: _configure_dialog(attempt + 1))
            else:
                state["error"] = "Chat settings dialog did not open"
            return

        try:
            if use_defaults:
                dialog.reset_btn.click()
            else:
                dialog.temperature_checkbox.setChecked(
                    temperature_override is not None
                )
                if temperature_override is not None:
                    dialog.temperature_spin.setValue(float(temperature_override))

                dialog.max_tokens_checkbox.setChecked(
                    max_tokens_override is not None
                )
                if max_tokens_override is not None:
                    dialog.max_tokens_spin.setValue(int(max_tokens_override))

            QApplication.instance().processEvents()
            dialog.accept()
        except Exception as error:  # pragma: no cover - live harness guard
            state["error"] = str(error)

    QTimer.singleShot(50, _configure_dialog)
    widget.chat_settings_btn.click()
    closed = wait_for(
        lambda: not any(
            isinstance(top_level, ChatSettingsDialog) and top_level.isVisible()
            for top_level in QApplication.topLevelWidgets()
        ),
        timeout_ms=5000,
        step_ms=50,
    )
    if state["error"] is not None:
        raise RuntimeError(state["error"])
    if not closed:
        raise RuntimeError("Chat settings dialog did not close")
    QApplication.instance().processEvents()


def delete_chat_exchange_via_dialog(widget, exchange_index):
    """Open the real delete-exchange dialog and confirm one selection."""
    state = {"error": None}

    def _confirm_delete(attempt=0):
        dialog = next(
            (
                top_level for top_level in QApplication.topLevelWidgets()
                if isinstance(top_level, ExchangeDeleteDialog)
                and top_level.isVisible()
            ),
            None,
        )
        if dialog is None:
            if attempt < 40:
                QTimer.singleShot(50, lambda: _confirm_delete(attempt + 1))
            else:
                state["error"] = "Exchange delete dialog did not open"
            return

        if not dialog.select_exchange_index(exchange_index):
            state["error"] = f"Exchange {exchange_index} not available in dialog"
            dialog.reject()
            return

        def _accept_message_box(message_attempt=0):
            message_box = next(
                (
                    top_level for top_level in QApplication.topLevelWidgets()
                    if isinstance(top_level, QMessageBox)
                    and top_level.isVisible()
                ),
                None,
            )
            if message_box is None:
                if message_attempt < 40:
                    QTimer.singleShot(
                        25,
                        lambda: _accept_message_box(message_attempt + 1),
                    )
                else:
                    state["error"] = "Delete confirmation dialog did not open"
                return

            message_box.button(QMessageBox.Yes).click()

        QTimer.singleShot(0, _accept_message_box)
        dialog.request_delete()

    QTimer.singleShot(50, _confirm_delete)
    widget._delete_exchange_action.trigger()
    closed = wait_for(
        lambda: not any(
            (
                isinstance(top_level, ExchangeDeleteDialog)
                or isinstance(top_level, QMessageBox)
            )
            and top_level.isVisible()
            for top_level in QApplication.topLevelWidgets()
        ),
        timeout_ms=5000,
        step_ms=50,
    )
    if state["error"] is not None:
        raise RuntimeError(state["error"])
    if not closed:
        raise RuntimeError("Exchange delete flow did not close")
    QApplication.instance().processEvents()


def apply_chat_code_via_dialog(widget, code, mode, accept=True):
    """Open the real apply-preview dialog and optionally accept it."""
    state = {"error": None, "diff_text": "", "summary": ""}

    def _configure_dialog(attempt=0):
        dialog = next(
            (
                top_level for top_level in QApplication.topLevelWidgets()
                if isinstance(top_level, CodeApplyDialog)
                and top_level.isVisible()
            ),
            None,
        )
        if dialog is None:
            if attempt < 40:
                QTimer.singleShot(50, lambda: _configure_dialog(attempt + 1))
            else:
                state["error"] = "Code apply dialog did not open"
            return

        if mode and not dialog.select_mode(mode):
            state["error"] = f"Apply mode not available in dialog: {mode}"
            dialog.reject()
            return

        state["diff_text"] = dialog.diff_view.toPlainText()
        state["summary"] = dialog.summary_label.text()
        if accept:
            dialog.button_box.button(QDialogButtonBox.Ok).click()
        else:
            dialog.reject()

    QTimer.singleShot(50, _configure_dialog)
    widget._active_session.display.sig_apply_code_requested.emit(code)
    closed = wait_for(
        lambda: not any(
            isinstance(top_level, CodeApplyDialog) and top_level.isVisible()
            for top_level in QApplication.topLevelWidgets()
        ),
        timeout_ms=5000,
        step_ms=50,
    )
    if state["error"] is not None:
        raise RuntimeError(state["error"])
    if not closed:
        raise RuntimeError("Code apply dialog did not close")
    QApplication.instance().processEvents()
    return state


def save_provider_profiles_via_dialog(widget, profiles):
    """Open the real provider-profiles dialog and save one full profile set."""
    state = {"error": None, "saved_profiles": []}

    def _configure_dialog(attempt=0):
        dialog = next(
            (
                top_level for top_level in QApplication.topLevelWidgets()
                if isinstance(top_level, ProviderProfilesDialog)
                and top_level.isVisible()
            ),
            None,
        )
        if dialog is None:
            if attempt < 40:
                QTimer.singleShot(50, lambda: _configure_dialog(attempt + 1))
            else:
                state["error"] = "Provider profiles dialog did not open"
            return

        dialog.replace_profiles(profiles)
        state["saved_profiles"] = dialog.selected_profiles()
        dialog.button_box.button(QDialogButtonBox.Save).click()

    QTimer.singleShot(50, _configure_dialog)
    widget._provider_profiles_action.trigger()
    closed = wait_for(
        lambda: not any(
            isinstance(top_level, ProviderProfilesDialog)
            and top_level.isVisible()
            for top_level in QApplication.topLevelWidgets()
        ),
        timeout_ms=5000,
        step_ms=50,
    )
    if state["error"] is not None:
        raise RuntimeError(state["error"])
    if not closed:
        raise RuntimeError("Provider profiles dialog did not close")
    QApplication.instance().processEvents()
    return state


def set_input_text(widget, text):
    """Replace the current chat input text."""
    widget.chat_input.setPlainText(text)
    QApplication.instance().processEvents()


def wait_for_assistant_turn(widget, session, previous_count, timeout_ms=120000):
    """Wait until a chat turn appends one assistant response."""
    wait_for(lambda: widget._generating, timeout_ms=5000, step_ms=50)
    return wait_for(
        lambda: (
            not widget._generating
            and len(session.messages) >= previous_count + 2
            and session.messages[-1].get("role") == "assistant"
        ),
        timeout_ms=timeout_ms,
        step_ms=200,
    )


def send_prompt(widget, prompt, timeout_ms=120000):
    """Send one normal prompt and return the assistant reply."""
    session = widget._active_session
    previous_count = len(session.messages)
    set_input_text(widget, prompt)
    widget.send_btn.click()
    completed = wait_for_assistant_turn(
        widget,
        session,
        previous_count,
        timeout_ms=timeout_ms,
    )
    if not completed:
        raise RuntimeError("Timed out waiting for prompt response")
    return session.messages[-1]["content"]


def schedule_validation(run_validation, attr_name, delay_ms=3500):
    """Patch `MainWindow.post_visible_setup` to run one validation callback."""
    original_post_visible_setup = MainWindow.post_visible_setup

    def _patched_post_visible_setup(self, *args, **kwargs):
        result = original_post_visible_setup(self, *args, **kwargs)
        if not getattr(self, attr_name, False):
            setattr(self, attr_name, True)
            QTimer.singleShot(delay_ms, lambda: run_validation(self))
        return result

    MainWindow.post_visible_setup = _patched_post_visible_setup


def run_spyder_validation(config_dir, filter_log, run_validation, attr_name,
                          delay_ms=3500):
    """Launch Spyder and schedule one validation callback after startup."""
    config_dir = Path(config_dir)
    if config_dir.exists():
        shutil.rmtree(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    schedule_validation(run_validation, attr_name=attr_name, delay_ms=delay_ms)

    sys.argv = [
        "spyder",
        "--new-instance",
        "--safe-mode",
        "--conf-dir",
        str(config_dir),
        "--debug-info",
        "verbose",
        "--debug-output",
        "terminal",
        "--filter-log",
        filter_log,
    ]

    from spyder.app.start import main as spyder_main

    previous_pytest = os.environ.get("SPYDER_PYTEST")
    os.environ["SPYDER_PYTEST"] = "1"
    try:
        window = spyder_main()
        if window is None:
            raise RuntimeError("Spyder did not create a main window")
        return QApplication.instance().exec_()
    finally:
        if previous_pytest is None:
            os.environ.pop("SPYDER_PYTEST", None)
        else:
            os.environ["SPYDER_PYTEST"] = previous_pytest
