"""Background QThread worker for provider-aware chat API calls.

The chat pane still uses one dedicated Qt worker thread so streaming model
responses never block Spyder's UI. The transport itself is now provider-
agnostic: the worker delegates model listing and chat streaming to a small
provider registry built from the current plugin settings snapshot.
"""

from __future__ import annotations

import logging

import httpx
from ollama import ResponseError
from qtpy.QtCore import QObject, QMutex, QMutexLocker, Signal

from spyder_ai_assistant.backend.chat_providers import ChatProviderRegistry

logger = logging.getLogger(__name__)


class ChatWorker(QObject):
    """Worker that executes chat-provider requests on a background thread."""

    chunk_received = Signal(str)
    response_ready = Signal(str, dict)
    models_listed = Signal(list)
    error_occurred = Signal(str)
    status_changed = Signal(str)

    def __init__(self, settings=None):
        super().__init__()
        self._settings = dict(settings or {})
        self._registry = None
        self._abort = False
        self._mutex = QMutex()

    def update_settings(self, settings):
        """Replace the provider settings snapshot on the worker thread."""
        self._settings = dict(settings or {})
        self._registry = ChatProviderRegistry(self._settings)
        logger.info(
            "Chat worker provider settings updated: ollama=%s, openai_compatible=%s",
            self._settings.get("ollama_host", ""),
            self._settings.get("openai_compatible_base_url", ""),
        )

    def send_chat(self, provider_id, model, messages, options):
        """Send a streaming chat request through the selected provider."""
        with QMutexLocker(self._mutex):
            self._abort = False

        self.status_changed.emit("generating")

        try:
            self._ensure_registry()
            chunks = []
            for chunk_data in self._registry.chat_stream(
                    provider_id,
                    model,
                    messages,
                    options):
                with QMutexLocker(self._mutex):
                    if self._abort:
                        logger.info(
                            "Chat worker aborted streaming response from %s/%s",
                            provider_id,
                            model,
                        )
                        return

                content = chunk_data.get("content", "") or ""
                if content:
                    chunks.append(content)
                    self.chunk_received.emit(content)

                if chunk_data.get("done"):
                    full_response = "".join(chunks)
                    metrics = {
                        "eval_count": int(chunk_data.get("eval_count", 0) or 0),
                        "eval_duration": int(
                            chunk_data.get("eval_duration", 0) or 0
                        ),
                        "prompt_eval_count": int(
                            chunk_data.get("prompt_eval_count", 0) or 0
                        ),
                    }
                    logger.info(
                        "Chat worker completed response from %s/%s (%d chars)",
                        provider_id,
                        model,
                        len(full_response),
                    )
                    self.response_ready.emit(full_response, metrics)
                    return

            full_response = "".join(chunks)
            logger.info(
                "Chat worker ended stream without explicit done marker from %s/%s",
                provider_id,
                model,
            )
            self.response_ready.emit(
                full_response,
                {
                    "eval_count": 0,
                    "eval_duration": 0,
                    "prompt_eval_count": 0,
                },
            )
        except Exception as error:  # pragma: no cover - threaded guard
            logger.warning(
                "Chat worker request failed for %s/%s: %s",
                provider_id,
                model,
                error,
            )
            self.error_occurred.emit(self._format_error(error, provider_id))

    def list_models(self):
        """Fetch models from every configured chat provider."""
        self.status_changed.emit("loading_models")
        try:
            self._ensure_registry()
            models = self._registry.list_models()
            logger.info("Chat worker discovered %d chat model(s)", len(models))
            self.models_listed.emit(models)
        except Exception as error:  # pragma: no cover - threaded guard
            logger.warning("Chat worker failed to list models: %s", error)
            self.error_occurred.emit(self._format_error(error))

    def abort(self):
        """Request cancellation of the current streaming operation."""
        with QMutexLocker(self._mutex):
            self._abort = True

    def _ensure_registry(self):
        """Create the provider registry lazily on the worker thread."""
        if self._registry is None:
            self._registry = ChatProviderRegistry(self._settings)

    def _format_error(self, error, provider_id=""):
        """Convert provider errors to concise user-facing messages."""
        if isinstance(error, ResponseError):
            if error.status_code == 404:
                return f"Model not found: {error.error}"
            return f"Ollama error: {error.error}"

        if isinstance(error, httpx.HTTPStatusError):
            provider_label = self._provider_label(provider_id)
            status_code = error.response.status_code
            return (
                f"{provider_label} request failed with HTTP {status_code}. "
                "Check the configured endpoint and model."
            )

        if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
            provider_label = self._provider_label(provider_id)
            endpoint = self._provider_endpoint(provider_id)
            return (
                f"Cannot connect to {provider_label} at {endpoint}. "
                "Check that the service is reachable."
            )

        error_str = str(error)
        if "Connect" in error_str or "refused" in error_str:
            provider_label = self._provider_label(provider_id)
            endpoint = self._provider_endpoint(provider_id)
            return (
                f"Cannot connect to {provider_label} at {endpoint}. "
                "Check that the service is reachable."
            )

        provider_label = self._provider_label(provider_id)
        if provider_id:
            return f"{provider_label} error: {error}"
        return f"Unexpected error: {error}"

    def _provider_label(self, provider_id):
        """Return one user-facing provider label for errors."""
        if provider_id == "openai_compatible":
            return "OpenAI-compatible provider"
        return "Ollama"

    def _provider_endpoint(self, provider_id):
        """Return the configured endpoint for one provider."""
        if provider_id == "openai_compatible":
            return self._settings.get("openai_compatible_base_url", "<unset>")
        return self._settings.get("ollama_host", "http://localhost:11434")


# Backward-compatible alias kept for older imports and docs.
OllamaWorker = ChatWorker
