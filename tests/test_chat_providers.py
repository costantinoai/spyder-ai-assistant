"""Unit tests for provider-aware chat backends."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from spyder_ai_assistant.backend.chat_providers import (
    ChatProviderRegistry,
    OpenAICompatibleChatProvider,
    OllamaChatProvider,
)
from spyder_ai_assistant.backend.client import (
    OpenAICompatibleCompletionClient,
)


class _OpenAICompatibleHandler(BaseHTTPRequestHandler):
    server_version = "FakeOpenAICompatible/1.0"

    def do_GET(self):  # noqa: N802 - stdlib handler name
        self.server.seen_auth = self.headers.get("Authorization", "")
        if self.path != "/v1/models":
            self.send_error(404)
            return

        payload = {
            "data": [
                {"id": "fake-chat-1", "owned_by": "local-lab"},
                {"id": "fake-chat-2", "owned_by": "local-lab"},
            ]
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802 - stdlib handler name
        self.server.seen_auth = self.headers.get("Authorization", "")
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_payload = self.rfile.read(content_length or 0)
        self.server.last_chat_payload = json.loads(raw_payload.decode("utf-8"))

        if not self.server.last_chat_payload.get("stream", False):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": "result = helper(values)\nreturn result",
                        }
                    }
                ]
            }
            body = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        chunks = [
            {
                "choices": [
                    {"delta": {"content": "Hello "}, "finish_reason": None}
                ]
            },
            {
                "choices": [
                    {"delta": {"content": "world"}, "finish_reason": None}
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
            self.wfile.write(
                f"data: {json.dumps(chunk)}\n\n".encode("utf-8")
            )
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    def log_message(self, format, *args):  # noqa: A003 - stdlib signature
        del format, args


class _OpenAICompatibleServer:
    def __enter__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAICompatibleHandler)
        self.httpd.seen_auth = ""
        self.httpd.last_chat_payload = {}
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


def test_openai_compatible_provider_lists_models_and_uses_auth_header():
    with _OpenAICompatibleServer() as server:
        provider = OpenAICompatibleChatProvider(
            server.base_url,
            api_key="secret-token",
        )

        models = provider.list_models()

    assert [model.name for model in models] == ["fake-chat-1", "fake-chat-2"]
    assert server.httpd.seen_auth == "Bearer secret-token"


def test_openai_compatible_provider_streams_response_and_metrics():
    with _OpenAICompatibleServer() as server:
        provider = OpenAICompatibleChatProvider(server.base_url)

        chunks = list(
            provider.chat_stream(
                "fake-chat-1",
                [{"role": "user", "content": "Say hello"}],
                {"temperature": 0.3, "num_predict": 48},
            )
        )

    assert chunks[:-1] == [
        {"content": "Hello ", "done": False},
        {"content": "world", "done": False},
    ]
    assert chunks[-1]["done"] is True
    assert chunks[-1]["prompt_eval_count"] == 7
    assert chunks[-1]["eval_count"] == 2
    assert server.httpd.last_chat_payload["model"] == "fake-chat-1"
    assert server.httpd.last_chat_payload["temperature"] == 0.3
    assert server.httpd.last_chat_payload["max_tokens"] == 48


def test_openai_compatible_completion_client_posts_non_stream_request():
    with _OpenAICompatibleServer() as server:
        client = OpenAICompatibleCompletionClient(server.base_url, api_key="secret-token")

        result = client.generate_completion(
            model="fake-chat-1",
            prefix="def compute(values):\n    result = ",
            suffix="\n    return result\n",
            options={"temperature": 0.2, "num_predict": 96},
            single_line=False,
        )

    assert result == "result = helper(values)\nreturn result"
    assert server.httpd.seen_auth == "Bearer secret-token"
    assert server.httpd.last_chat_payload["stream"] is False
    assert server.httpd.last_chat_payload["model"] == "fake-chat-1"
    assert server.httpd.last_chat_payload["temperature"] == 0.2
    assert server.httpd.last_chat_payload["max_tokens"] == 96
    assert server.httpd.last_chat_payload["messages"][0]["role"] == "system"
    assert server.httpd.last_chat_payload["messages"][1]["role"] == "user"
    assert server.httpd.last_chat_payload["stop"] == ["\n\n\n", "\nclass ", "\ndef ", "\n# %%"]


def test_chat_provider_registry_aggregates_models_from_multiple_providers(monkeypatch):
    def fake_list_models(self):
        return [
            {
                "name": "ollama-chat",
                "family": "llama",
                "parameter_size": "8B",
                "quantization": "Q4_K_M",
                "size_gb": 4.2,
            }
        ]

    def fake_chat_stream(self, model, messages, options=None):
        del model, messages, options
        yield {
            "content": "ollama reply",
            "done": True,
            "eval_count": 1,
            "eval_duration": 1,
            "prompt_eval_count": 1,
        }

    monkeypatch.setattr(OllamaChatProvider, "list_models", fake_list_models)
    monkeypatch.setattr(OllamaChatProvider, "chat_stream", fake_chat_stream)

    with _OpenAICompatibleServer() as server:
        registry = ChatProviderRegistry(
            {
                "ollama_host": "http://localhost:11434",
                "provider_profiles": [
                    {
                        "profile_id": "research",
                        "label": "Research API",
                        "provider_kind": "openai_compatible",
                        "base_url": server.base_url,
                        "api_key": "",
                        "enabled": True,
                    }
                ],
            }
        )

        models, diagnostics = registry.list_models_with_diagnostics()
        openai_reply = list(
            registry.chat_stream(
                "openai_compatible:research",
                "fake-chat-1",
                [{"role": "user", "content": "Hi"}],
                {},
            )
        )
        ollama_reply = list(
            registry.chat_stream(
                "ollama",
                "ollama-chat",
                [{"role": "user", "content": "Hi"}],
                {},
            )
        )

    assert {(item["provider_id"], item["name"]) for item in models} == {
        ("ollama", "ollama-chat"),
        ("openai_compatible:research", "fake-chat-1"),
        ("openai_compatible:research", "fake-chat-2"),
    }
    compatible_diag = next(
        record for record in diagnostics
        if record["provider_id"] == "openai_compatible:research"
    )
    assert compatible_diag["status"] == "ready"
    assert compatible_diag["model_count"] == 2
    assert openai_reply[-1]["done"] is True
    assert ollama_reply[-1]["content"] == "ollama reply"
