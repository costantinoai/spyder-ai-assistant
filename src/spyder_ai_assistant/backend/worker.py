"""Background QThread worker for async Ollama API calls.

This module provides the OllamaWorker QObject which handles all Ollama
communication on a dedicated background thread, ensuring the Qt main
thread (UI) is never blocked during LLM inference.

All communication with the main thread is via Qt signals. The worker
is created on the main thread, then moved to a QThread via moveToThread().

Signal flow:
    Main thread → Worker: sig_send_chat, sig_list_models (via connected signals)
    Worker → Main thread: chunk_received, response_ready, error_occurred, etc.
"""

import logging

from qtpy.QtCore import QObject, Signal, QMutex, QMutexLocker

from spyder_ai_assistant.backend.client import OllamaClient

logger = logging.getLogger(__name__)


class OllamaWorker(QObject):
    """Worker that executes Ollama API calls on a background QThread.

    All public methods are invoked via connected signals from the main
    thread. Results are emitted back to the main thread via signals.
    Thread-safe abort is managed via a QMutex-protected flag.
    """

    # --- Signals emitted from worker thread, received on main thread ---

    # Each streaming token as it arrives from the LLM
    chunk_received = Signal(str)

    # Complete response text and performance metrics dict.
    # Emitted when streaming finishes successfully.
    response_ready = Signal(str, dict)

    # List of model info dicts from Ollama's model listing
    models_listed = Signal(list)

    # Human-readable error message for display in the chat
    error_occurred = Signal(str)

    # Worker status change: "generating", "loading_models".
    # Final states (ready, error) are handled by the widget based
    # on response_ready / error_occurred signals.
    status_changed = Signal(str)

    def __init__(self, host="http://localhost:11434"):
        super().__init__()
        self._host = host
        self._client = None
        self._abort = False
        self._mutex = QMutex()

    def update_host(self, host):
        """Recreate the Ollama client with a new server URL.

        Called when the user changes the Ollama host in preferences.
        """
        self._host = host
        self._client = OllamaClient(host=host)

    # --- Slots invoked via signals from main thread ---

    def send_chat(self, model, messages, options):
        """Send a streaming chat request to Ollama.

        Iterates over the streaming response, emitting chunk_received
        for each token. On completion, emits response_ready with the
        full response text and performance metrics. Checks the abort
        flag between chunks for responsive mid-stream cancellation.

        Args:
            model: Ollama model name.
            messages: Conversation history (list of role/content dicts).
            options: Model parameters (temperature, num_predict, etc.).
        """
        # Reset abort flag at the start of each new request
        with QMutexLocker(self._mutex):
            self._abort = False

        self.status_changed.emit("generating")

        try:
            self._ensure_client()
            chunks = []
            for chunk_data in self._client.chat_stream(
                model, messages, options
            ):
                # Check abort flag between chunks so the user can
                # cancel mid-stream without waiting for completion
                with QMutexLocker(self._mutex):
                    if self._abort:
                        return

                content = chunk_data["content"]
                if content:
                    chunks.append(content)
                    self.chunk_received.emit(content)

                # The final chunk carries performance metrics
                if chunk_data["done"]:
                    full_response = "".join(chunks)
                    metrics = {
                        "eval_count": chunk_data.get("eval_count", 0),
                        "eval_duration": chunk_data.get(
                            "eval_duration", 0
                        ),
                        "prompt_eval_count": chunk_data.get(
                            "prompt_eval_count", 0
                        ),
                    }
                    self.response_ready.emit(full_response, metrics)
        except Exception as e:
            self.error_occurred.emit(self._format_error(e))

    def list_models(self):
        """Fetch available models from Ollama.

        Emits models_listed with a list of model info dicts on success,
        or error_occurred if the server is unreachable.
        """
        self.status_changed.emit("loading_models")
        try:
            self._ensure_client()
            models = self._client.list_models()
            self.models_listed.emit(models)
        except Exception as e:
            self.error_occurred.emit(self._format_error(e))

    def abort(self):
        """Request cancellation of the current streaming operation.

        Thread-safe: can be called from any thread. Sets a flag that
        the streaming loop checks between each chunk.
        """
        with QMutexLocker(self._mutex):
            self._abort = True

    # --- Private helpers ---

    def _format_error(self, error):
        """Convert an exception to a user-friendly error message.

        Recognizes common Ollama error types and produces clear
        messages that help the user diagnose the problem.
        """
        from ollama import ResponseError

        if isinstance(error, ResponseError):
            if error.status_code == 404:
                return f"Model not found: {error.error}"
            return f"Ollama error: {error.error}"

        # Check for connection-related errors by inspecting the message,
        # since the exact exception type varies by httpx version
        error_str = str(error)
        if "Connect" in error_str or "refused" in error_str:
            return (
                f"Cannot connect to Ollama at {self._host}. "
                "Is the Ollama service running?"
            )

        return f"Unexpected error: {error}"

    def _ensure_client(self):
        """Create the Ollama client on the worker thread when needed."""
        if self._client is None:
            self._client = OllamaClient(host=self._host)
