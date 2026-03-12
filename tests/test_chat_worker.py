"""Unit tests for the provider-aware chat worker."""

from __future__ import annotations

from spyder_ai_assistant.backend.worker import ChatWorker


class _FakeRegistry:
    def __init__(self, settings):
        self.settings = dict(settings or {})

    def list_models_with_diagnostics(self):
        return (
            [{"provider_id": "fake", "provider_label": "Fake", "name": "demo"}],
            [{
                "provider_id": "fake",
                "provider_label": "Fake",
                "provider_kind": "fake",
                "status": "ready",
                "message": "1 model(s) available",
                "model_count": 1,
                "endpoint": "http://fake",
            }],
        )

    def chat_stream(self, provider_id, model, messages, options=None):
        assert provider_id == "fake"
        assert model == "demo"
        assert messages == [{"role": "user", "content": "Hi"}]
        assert options == {"temperature": 0.1}
        yield {"content": "Hello", "done": False}
        yield {
            "content": "",
            "done": True,
            "eval_count": 3,
            "eval_duration": 9,
            "prompt_eval_count": 4,
        }

    def describe_provider(self, provider_id):
        return {
            "provider_id": provider_id,
            "provider_label": "Fake",
            "endpoint": "http://fake",
        }


def test_chat_worker_lists_models_and_streams(monkeypatch):
    monkeypatch.setattr(
        "spyder_ai_assistant.backend.worker.ChatProviderRegistry",
        _FakeRegistry,
    )

    worker = ChatWorker(settings={"ollama_host": "http://localhost:11434"})
    models = []
    diagnostics = []
    chunks = []
    responses = []
    worker.models_listed.connect(models.append)
    worker.provider_diagnostics_ready.connect(diagnostics.append)
    worker.chunk_received.connect(chunks.append)
    worker.response_ready.connect(
        lambda text, metrics: responses.append((text, metrics))
    )

    worker.list_models()
    worker.send_chat(
        "fake",
        "demo",
        [{"role": "user", "content": "Hi"}],
        {"temperature": 0.1},
    )

    assert models == [[{"provider_id": "fake", "provider_label": "Fake", "name": "demo"}]]
    assert diagnostics == [[{
        "provider_id": "fake",
        "provider_label": "Fake",
        "provider_kind": "fake",
        "status": "ready",
        "message": "1 model(s) available",
        "model_count": 1,
        "endpoint": "http://fake",
    }]]
    assert chunks == ["Hello"]
    assert responses == [
        (
            "Hello",
            {
                "eval_count": 3,
                "eval_duration": 9,
                "prompt_eval_count": 4,
            },
        )
    ]


def test_chat_worker_formats_connection_errors():
    worker = ChatWorker(
        settings={"openai_compatible_base_url": "http://127.0.0.1:9"}
    )

    message = worker._format_error(
        RuntimeError("Connection refused"),
        provider_id="openai_compatible",
    )

    assert "OpenAI-compatible provider" in message
    assert "http://127.0.0.1:9" in message
