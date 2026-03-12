"""Live Phase 12 validation for provider profiles and diagnostics."""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.spyder_validation.common import (
    artifact_path,
    finalize,
    get_chat_widget,
    record_validation_result,
    run_spyder_validation,
    save_provider_profiles_via_dialog,
    send_prompt,
    wait_for,
    write_json,
)


CONFIG_DIR = artifact_path("configs", "phase12-provider-profiles-validation")
RESULT_PATH = artifact_path("results", "phase12-provider-profiles-validation.json")


class _ProfileHandler(BaseHTTPRequestHandler):
    server_version = "SpyderAIProfile/1.0"

    def do_GET(self):  # noqa: N802 - stdlib handler
        self.server.seen_auth = self.headers.get("Authorization", "")
        if self.path != "/v1/models":
            self.send_error(404)
            return

        payload = {
            "data": [
                {
                    "id": self.server.model_id,
                    "owned_by": self.server.owner,
                }
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - stdlib handler
        self.server.seen_auth = self.headers.get("Authorization", "")
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_payload = self.rfile.read(content_length or 0)
        self.server.last_payload = json.loads(raw_payload.decode("utf-8"))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chunks = [
            {
                "choices": [
                    {"delta": {"content": self.server.reply_prefix}, "finish_reason": None}
                ]
            },
            {
                "choices": [
                    {"delta": {"content": " OK"}, "finish_reason": None}
                ]
            },
            {
                "choices": [
                    {"delta": {}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 2},
            },
        ]
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode("utf-8"))
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format, *args):  # noqa: A003 - stdlib signature
        del format, args


class _ProfileServer:
    def __init__(self, model_id, reply_prefix, owner, api_key):
        self.model_id = model_id
        self.reply_prefix = reply_prefix
        self.owner = owner
        self.api_key = api_key

    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ProfileHandler)
        self.httpd.model_id = self.model_id
        self.httpd.reply_prefix = self.reply_prefix
        self.httpd.owner = self.owner
        self.httpd.seen_auth = ""
        self.httpd.last_payload = {}
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            daemon=True,
        )
        self.thread.start()
        host, port = self.httpd.server_address
        self.base_url = f"http://{host}:{port}"
        return self

    def __exit__(self, exc_type, exc, tb):
        del exc_type, exc, tb
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)


def _select_profile_model(widget, profile_id):
    for index in range(widget.model_combo.count()):
        payload = widget.model_combo.itemData(index)
        if (
            isinstance(payload, dict)
            and payload.get("profile_id") == profile_id
        ):
            widget.model_combo.setCurrentIndex(index)
            return dict(payload)
    raise RuntimeError(f"No model found for profile {profile_id}")


def run_validation(window):
    """Validate profile-backed chat providers through the real UI."""
    results = {"phase": "12", "errors": []}

    try:
        widget = get_chat_widget(window)

        with (
            _ProfileServer(
                model_id="alpha-chat-small",
                reply_prefix="Alpha profile",
                owner="alpha-lab",
                api_key="alpha-token",
            ) as alpha,
            _ProfileServer(
                model_id="beta-chat-small",
                reply_prefix="Beta profile",
                owner="beta-lab",
                api_key="beta-token",
            ) as beta,
        ):
            print("[phase12] saving provider profiles through the dialog", flush=True)
            saved = save_provider_profiles_via_dialog(
                widget,
                [
                    {
                        "profile_id": "alpha",
                        "label": "Alpha Lab",
                        "provider_kind": "openai_compatible",
                        "base_url": alpha.base_url,
                        "api_key": alpha.api_key,
                        "enabled": True,
                    },
                    {
                        "profile_id": "beta",
                        "label": "Beta Lab",
                        "provider_kind": "openai_compatible",
                        "base_url": beta.base_url,
                        "api_key": beta.api_key,
                        "enabled": True,
                    },
                    {
                        "profile_id": "broken",
                        "label": "Broken Lab",
                        "provider_kind": "openai_compatible",
                        "base_url": "http://127.0.0.1:9",
                        "api_key": "",
                        "enabled": True,
                    },
                ],
            )

            diagnostics_ready = wait_for(
                lambda: (
                    len(widget._provider_diagnostics) >= 3
                    and any(
                        record.get("profile_id") == "alpha"
                        and record.get("status") == "ready"
                        for record in widget._provider_diagnostics
                    )
                    and any(
                        record.get("profile_id") == "beta"
                        and record.get("status") == "ready"
                        for record in widget._provider_diagnostics
                    )
                    and any(
                        record.get("profile_id") == "broken"
                        and record.get("status") == "error"
                        for record in widget._provider_diagnostics
                    )
                ),
                timeout_ms=25000,
                step_ms=100,
            )
            if not diagnostics_ready:
                raise RuntimeError("Provider diagnostics did not settle")
            models_ready = wait_for(
                lambda: (
                    any(
                        isinstance(widget.model_combo.itemData(index), dict)
                        and widget.model_combo.itemData(index).get("profile_id") == "alpha"
                        for index in range(widget.model_combo.count())
                    )
                    and any(
                        isinstance(widget.model_combo.itemData(index), dict)
                        and widget.model_combo.itemData(index).get("profile_id") == "beta"
                        for index in range(widget.model_combo.count())
                    )
                ),
                timeout_ms=20000,
                step_ms=100,
            )
            if not models_ready:
                raise RuntimeError("Profile-backed models did not appear in the selector")

            print("[phase12] selecting alpha profile model", flush=True)
            alpha_payload = _select_profile_model(widget, "alpha")
            alpha_answer = send_prompt(
                widget,
                "Reply with the active profile name.",
                timeout_ms=30000,
            )

            print("[phase12] selecting beta profile model", flush=True)
            beta_payload = _select_profile_model(widget, "beta")
            beta_answer = send_prompt(
                widget,
                "Reply with the active profile name.",
                timeout_ms=30000,
            )

            print("[phase12] removing beta profile to test stale-model fallback", flush=True)
            save_provider_profiles_via_dialog(
                widget,
                [
                    {
                        "profile_id": "alpha",
                        "label": "Alpha Lab",
                        "provider_kind": "openai_compatible",
                        "base_url": alpha.base_url,
                        "api_key": alpha.api_key,
                        "enabled": True,
                    },
                    {
                        "profile_id": "broken",
                        "label": "Broken Lab",
                        "provider_kind": "openai_compatible",
                        "base_url": "http://127.0.0.1:9",
                        "api_key": "",
                        "enabled": True,
                    },
                ],
            )
            fallback_ready = wait_for(
                lambda: (
                    not any(
                        isinstance(widget.model_combo.itemData(index), dict)
                        and widget.model_combo.itemData(index).get("profile_id") == "beta"
                        for index in range(widget.model_combo.count())
                    )
                    and widget._current_provider_profile_id != "beta"
                ),
                timeout_ms=20000,
                step_ms=100,
            )
            if not fallback_ready:
                raise RuntimeError("Stale compatible profile selection did not fall back")
            status_ready = wait_for(
                lambda: widget.status_label.text() != "Loading models...",
                timeout_ms=20000,
                step_ms=100,
            )
            if not status_ready:
                raise RuntimeError("Provider status label did not settle after refresh")

            results["saved_profiles"] = saved["saved_profiles"]
            results["diagnostics"] = list(widget._provider_diagnostics)
            results["status"] = {
                "label": widget.status_label.text(),
                "tooltip": widget.status_label.toolTip(),
                "model_tooltip": widget.model_combo.toolTip(),
            }
            results["alpha"] = {
                "payload": alpha_payload,
                "answer": alpha_answer,
                "auth_header": alpha.httpd.seen_auth,
                "request_payload": alpha.httpd.last_payload,
            }
            results["beta"] = {
                "payload": beta_payload,
                "answer": beta_answer,
                "auth_header": beta.httpd.seen_auth,
                "request_payload": beta.httpd.last_payload,
            }

            if "alpha" not in alpha_answer.lower():
                raise RuntimeError("Alpha profile answer did not come from Alpha Lab")
            if "beta" not in beta_answer.lower():
                raise RuntimeError("Beta profile answer did not come from Beta Lab")
            if alpha.httpd.seen_auth != "Bearer alpha-token":
                raise RuntimeError("Alpha profile auth header was not sent")
            if beta.httpd.seen_auth != "Bearer beta-token":
                raise RuntimeError("Beta profile auth header was not sent")
    except Exception as error:  # pragma: no cover - live harness guard
        results["errors"].append(str(error))
        results["traceback"] = traceback.format_exc()

    record_validation_result(window, RESULT_PATH, results)
    write_json(RESULT_PATH, results)
    finalize(window)


if __name__ == "__main__":
    raise SystemExit(
        run_spyder_validation(
            CONFIG_DIR,
            filter_log="spyder_ai_assistant",
            run_validation=run_validation,
            attr_name="_phase12_provider_profiles_validation_ran",
        )
    )
