"""Live multi-provider chat validation in a real Spyder session."""

from __future__ import annotations

import json
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tools.spyder_validation.common import (
    artifact_path,
    finalize,
    get_ai_plugin,
    get_chat_widget,
    record_validation_result,
    run_spyder_validation,
    select_first_provider_model,
    send_prompt,
    wait_for,
)


CONFIG_DIR = artifact_path("configs", "chat-provider-validation")
RESULT_PATH = artifact_path("results", "chat-provider-validation.json")


class _CompatibleHandler(BaseHTTPRequestHandler):
    server_version = "SpyderAICompatible/1.0"

    def do_GET(self):  # noqa: N802 - stdlib handler
        self.server.seen_auth = self.headers.get("Authorization", "")
        if self.path != "/v1/models":
            self.send_error(404)
            return

        payload = {
            "data": [
                {"id": "compatible-chat-small", "owned_by": "local-compatible"}
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
                    {"delta": {"content": "Compatible provider "}, "finish_reason": None}
                ]
            },
            {
                "choices": [
                    {"delta": {"content": "reply OK"}, "finish_reason": None}
                ]
            },
            {
                "choices": [
                    {"delta": {}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 9, "completion_tokens": 3},
            },
        ]
        for chunk in chunks:
            self.wfile.write(
                f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            )
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format, *args):  # noqa: A003 - stdlib signature
        del format, args


class _CompatibleServer:
    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _CompatibleHandler)
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


def run_validation(window):
    """Validate provider-aware chat listing and request dispatch."""
    results = {"errors": []}
    try:
        print("[validation] acquiring AI chat plugin", flush=True)
        ai_plugin = get_ai_plugin(window)
        widget = get_chat_widget(window)

        with _CompatibleServer() as server:
            print("[validation] configuring compatible provider", flush=True)
            ai_plugin.set_conf("openai_compatible_base_url", server.base_url)
            ai_plugin.set_conf("openai_compatible_api_key", "validation-token")
            ai_plugin.set_conf("chat_provider", "openai_compatible")
            ai_plugin.get_widget().update_chat_provider_settings(
                ai_plugin._build_chat_provider_settings()
            )

            print("[validation] waiting for provider-aware model listing", flush=True)
            compatible_ready = wait_for(
                lambda: any(
                    isinstance(widget.model_combo.itemData(index), dict)
                    and (
                        widget.model_combo.itemData(index).get("provider_id")
                        == "openai_compatible"
                        or widget.model_combo.itemData(index).get("provider_kind")
                        == "openai_compatible"
                    )
                    for index in range(widget.model_combo.count())
                ),
                timeout_ms=20000,
                step_ms=100,
            )
            if not compatible_ready:
                raise RuntimeError("OpenAI-compatible models did not appear")

            print("[validation] sending compatible-provider prompt", flush=True)
            compatible_payload = select_first_provider_model(
                widget,
                "openai_compatible",
            )
            compatible_answer = send_prompt(
                widget,
                "Reply with the provider confirmation.",
                timeout_ms=30000,
            )

            print("[validation] switching back to Ollama", flush=True)
            ollama_payload = select_first_provider_model(widget, "ollama")
            real_answer = send_prompt(
                widget,
                "Reply with OK.",
                timeout_ms=120000,
            )

            results["models"] = {
                "compatible_payload": compatible_payload,
                "ollama_payload": ollama_payload,
                "compatible_model_count": widget.model_combo.count(),
            }
            results["compatible_provider"] = {
                "answer": compatible_answer,
                "auth_header": server.httpd.seen_auth,
                "payload": server.httpd.last_payload,
                "used_compatible_model": (
                    server.httpd.last_payload.get("model")
                    == "compatible-chat-small"
                ),
            }
            results["ollama_provider"] = {
                "answer": real_answer,
                "non_empty": bool(real_answer.strip()),
            }
            results["status"] = {
                "label": widget.status_label.text(),
                "combo_tooltip": widget.model_combo.toolTip(),
            }
            print("[validation] completed", flush=True)
    except Exception as error:  # pragma: no cover - live harness guard
        results["errors"].append(str(error))
        results["traceback"] = traceback.format_exc()
    finally:
        print("[validation] finalizing", flush=True)
        record_validation_result(window, RESULT_PATH, results)
        finalize(window)


if __name__ == "__main__":
    raise SystemExit(
        run_spyder_validation(
            config_dir=CONFIG_DIR,
            filter_log="spyder_ai_assistant",
            run_validation=run_validation,
            attr_name="_chat_provider_validation_started",
        )
    )
