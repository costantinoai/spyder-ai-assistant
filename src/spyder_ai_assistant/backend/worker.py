"""Background QThread worker for provider-aware chat API calls.

The chat pane still uses one dedicated Qt worker thread so streaming model
responses never block Spyder's UI. The transport itself is now provider-
agnostic: the worker delegates model listing and chat streaming to a small
provider registry built from the current plugin settings snapshot.
"""

from __future__ import annotations

import logging

from qtpy.QtCore import QObject, QMutex, QMutexLocker, Signal

from spyder_ai_assistant.backend.chat_providers import ChatProviderRegistry
from spyder_ai_assistant.utils.constants import DEFAULT_OLLAMA_HOST
from spyder_ai_assistant.utils.error_messages import describe_provider_failure

logger = logging.getLogger(__name__)


class ChatWorker(QObject):
    """Worker that executes chat-provider requests on a background thread."""

    chunk_received = Signal(str)
    response_ready = Signal(str, dict)
    request_chunk_received = Signal(str, str)
    request_response_ready = Signal(str, str, dict)
    request_error_occurred = Signal(str, str)
    models_listed = Signal(list)
    provider_diagnostics_ready = Signal(list)
    error_occurred = Signal(str)
    status_changed = Signal(str)

    def __init__(self, settings=None):
        super().__init__()
        self._settings = dict(settings or {})
        self._registry = None
        self._abort = False
        self._cancelled_requests = set()
        self._mutex = QMutex()

    def update_settings(self, settings):
        """Replace the provider settings snapshot on the worker thread."""
        self._settings = dict(settings or {})
        # The previous registry owns HTTP connection pools; release them
        # instead of leaving them to the garbage collector.
        if self._registry is not None:
            self._registry.close()
        self._registry = ChatProviderRegistry(self._settings)
        logger.info(
            "Chat worker provider settings updated: ollama=%s, profile_count=%d",
            self._settings.get("ollama_host", ""),
            len(self._settings.get("provider_profiles", []) or []),
        )

    def send_chat(self, provider_id, model, messages, options):
        """Send a streaming chat request through the selected provider."""
        options = dict(options or {})
        request_id = options.pop("_spyder_request_id", "")
        with QMutexLocker(self._mutex):
            if request_id and request_id in self._cancelled_requests:
                self._cancelled_requests.discard(request_id)
                return
            self._abort = False
        try:
            self._stream_chat(provider_id, model, messages, options, request_id)
        finally:
            with QMutexLocker(self._mutex):
                self._cancelled_requests.discard(request_id)

    def _emit_chat_result(self, name, request_id, *args):
        with QMutexLocker(self._mutex):
            if self._abort or request_id in self._cancelled_requests:
                return
        if request_id:
            getattr(self, "request_" + name).emit(request_id, *args)
        else:
            getattr(self, name).emit(*args)

    def _stream_chat(self, provider_id, model, messages, options, request_id):
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
                    self._emit_chat_result("chunk_received", request_id, content)

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
                    self._emit_chat_result("response_ready", request_id, full_response, metrics)
                    return

            full_response = "".join(chunks)
            logger.info(
                "Chat worker ended stream without explicit done marker from %s/%s",
                provider_id,
                model,
            )
            self._emit_chat_result(
                "response_ready", request_id,
                full_response,
                {
                    "eval_count": 0,
                    "eval_duration": 0,
                    "prompt_eval_count": 0,
                },
            )
        except Exception as error:  # pragma: no cover - threaded guard
            with QMutexLocker(self._mutex):
                if self._abort or request_id in self._cancelled_requests:
                    return
            logger.warning(
                "Chat worker request failed for %s/%s: %s",
                provider_id,
                model,
                error,
            )
            self._emit_chat_result(
                "error_occurred", request_id,
                self._format_error(error, provider_id, model=model)
            )

    def list_models(self):
        """Fetch models from every configured chat provider."""
        self.status_changed.emit("loading_models")
        try:
            self._ensure_registry()
            models, diagnostics = self._registry.list_models_with_diagnostics()
            logger.info("Chat worker discovered %d chat model(s)", len(models))
            for diagnostic in diagnostics:
                logger.info(
                    "Provider diagnostic: id=%s kind=%s label=%s status=%s models=%d endpoint=%s message=%s",
                    diagnostic.get("provider_id", ""),
                    diagnostic.get("provider_kind", ""),
                    diagnostic.get("provider_label", ""),
                    diagnostic.get("status", ""),
                    diagnostic.get("model_count", 0),
                    diagnostic.get("endpoint", ""),
                    diagnostic.get("message", ""),
                )
            self.provider_diagnostics_ready.emit(diagnostics)
            self.models_listed.emit(models)
        except Exception as error:  # pragma: no cover - threaded guard
            logger.warning("Chat worker failed to list models: %s", error)
            self.error_occurred.emit(self._format_error(error))

    def abort(self, request_id=""):
        """Request cancellation of the current streaming operation."""
        with QMutexLocker(self._mutex):
            self._abort = True
            if request_id:
                self._cancelled_requests.add(request_id)

    def _ensure_registry(self):
        """Create the provider registry lazily on the worker thread."""
        if self._registry is None:
            self._registry = ChatProviderRegistry(self._settings)

    def _format_error(self, error, provider_id="", model=""):
        """Convert provider errors to one actionable user-facing message.

        The classification and the wording live in utils.error_messages, so
        the transcript, the provider diagnostics tooltip and the profile
        connection test all describe the same outage the same way.
        """
        return describe_provider_failure(
            error,
            provider_label=self._provider_label(provider_id),
            endpoint=self._provider_endpoint(provider_id),
            provider_kind=self._provider_kind(provider_id),
            model=model,
        )

    def _provider_kind(self, provider_id):
        """Return the provider kind, which decides the suggested remedy."""
        record = self._provider_record(provider_id)
        return record.get("provider_kind", "") or record.get("provider_id", "")

    def _provider_label(self, provider_id):
        """Return one user-facing provider label for errors."""
        record = self._provider_record(provider_id)
        if record.get("provider_label"):
            return record["provider_label"]
        if provider_id == "openai_compatible":
            return "OpenAI-compatible provider"
        return "Ollama"

    def _provider_endpoint(self, provider_id):
        """Return the configured endpoint for one provider."""
        record = self._provider_record(provider_id)
        if record.get("endpoint"):
            return record["endpoint"]
        if provider_id == "openai_compatible":
            return self._settings.get("openai_compatible_base_url", "<unset>")
        return self._settings.get("ollama_host", DEFAULT_OLLAMA_HOST)

    def _provider_record(self, provider_id):
        """Describe without constructing clients while handling a failure."""
        if self._registry is None:
            return {}
        return self._registry.describe_provider(provider_id)


# Backward-compatible alias kept for older imports and docs.
OllamaWorker = ChatWorker
