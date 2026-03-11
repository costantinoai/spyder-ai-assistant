"""Spyder completion provider for AI-powered inline code completions.

Implements VS Code Copilot / Cursor-style ghost text completions using
Ollama's FIM (Fill-in-Middle) API. Completions appear as semi-transparent
gray text in the editor that the user accepts with Tab.

Architecture:
- CompletionWorker: QObject on a background QThread that calls OllamaClient
- AIChatCompletionProvider: SpyderCompletionProvider that handles Spyder's
  completion protocol for file tracking, plus emits ghost text via a
  custom signal (sig_ghost_text_ready) that the plugin wires to editors

Signal flow:
  Editor types → Spyder → send_request(DOCUMENT_COMPLETION) → debounce(300ms)
  → CompletionWorker.perform_completion() [QThread]
  → OllamaClient.generate_completion() → sig_completion_ready
  → sig_ghost_text_ready → plugin → GhostTextManager → inline overlay
"""

import logging
from dataclasses import dataclass

from qtpy.QtCore import QObject, QThread, QTimer, Signal, Slot

from spyder.api.config.decorators import on_conf_change
from spyder.plugins.completion.api import (
    CompletionRequestTypes,
    SpyderCompletionProvider,
)

from spyder_ai_assistant.backend.client import OllamaClient
from spyder_ai_assistant.widgets.status import AIChatCompletionStatus

logger = logging.getLogger(__name__)

# ── Completion provider identity ──
# Used in completion items and signal routing
COMPLETION_PROVIDER_NAME = "ai_chat"

# ── Context window limits for prefix/suffix ──
# For inline completions, we send a focused window around the cursor:
# - Prefix: ~750 tokens (3000 chars) — the current function/block, imports,
#   and surrounding context. Enough for the model to understand the code style.
# - Suffix: ~750 tokens (3000 chars) — critical for FIM models to see what
#   code already exists below the cursor. Without enough suffix, the model
#   generates code that duplicates what's already there.
MAX_PREFIX_CHARS = 3000
MAX_SUFFIX_CHARS = 3000


@dataclass
class _QueuedCompletionRequest:
    """One completion request tracked by the provider.

    The provider only keeps three request slots alive at a time:
    - one debounced request waiting for the timer
    - one active request running on the worker thread
    - one queued "latest" request waiting behind the active one

    This prevents backlog growth when the user types quickly.
    """

    req: dict
    req_id: int


class _LatestOnlyCompletionQueue:
    """Keep only the latest relevant completion requests.

    The completion experience must prioritize freshness. Older requests are
    discarded aggressively so that a slow model cannot trap the user behind
    a backlog of obsolete completions.
    """

    def __init__(self):
        self._debounced = None
        self._queued = None
        self._active_req_id = None

    @property
    def active_req_id(self):
        """Return the currently active request id, if any."""
        return self._active_req_id

    def replace_debounced(self, request):
        """Store a debounced request, dropping any previous debounced one."""
        dropped = self._debounced
        self._debounced = request
        return dropped

    def pop_debounced(self):
        """Return and clear the current debounced request."""
        request = self._debounced
        self._debounced = None
        return request

    def replace_queued(self, request):
        """Store the newest queued request behind the active one."""
        dropped = self._queued
        self._queued = request
        return dropped

    def pop_queued(self):
        """Return and clear the queued request waiting behind the active one."""
        request = self._queued
        self._queued = None
        return request

    def start_active(self, req_id):
        """Mark a request id as active on the worker thread."""
        self._active_req_id = req_id

    def finish_active(self, req_id):
        """Clear the active slot when the matching request finishes."""
        if self._active_req_id == req_id:
            self._active_req_id = None

    def clear_pending(self):
        """Drop debounced and queued requests.

        Returns:
            List of request ids that should be answered immediately.
        """
        dropped_ids = []
        for request in (self._debounced, self._queued):
            if request is not None:
                dropped_ids.append(request.req_id)
        self._debounced = None
        self._queued = None
        return dropped_ids


def _clean_completion(raw_text, prefix, suffix=""):
    """Clean a model's raw completion output for inline display.

    Handles four problems that occur with FIM and non-FIM models:
    1. Markdown code fences (```python ... ```) wrapped around the output
    2. Prefix echo (model repeats the prompt before continuing)
    3. Suffix echo (model generates code that already exists below cursor)
    4. Prefix content repetition (model repeats from beginning of file)

    The function aggressively strips artifacts to produce clean code
    that can be inserted directly at the cursor position.

    Args:
        raw_text: The model's raw output.
        prefix: The code before the cursor that was sent as prompt.
        suffix: The code after the cursor. Used to detect when the model
            generates text that already exists below the cursor.

    Returns:
        Clean completion text ready for ghost text display, or "".
    """
    import re

    if not raw_text:
        return ""

    text = raw_text

    # --- Step 1: Strip markdown code fences ---
    # Pattern A: Full wrapping (```python\n...\n```)
    fence_match = re.match(r"^```\w*\n(.*?)```\s*$", text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)
    else:
        # Pattern B: Opening fence without closing (```python\n... cut by stop)
        fence_start = re.match(r"^```\w*\n(.*)", text, re.DOTALL)
        if fence_start:
            text = fence_start.group(1)

    # Remove any remaining ``` markers anywhere in the text.
    # These can appear when the model inserts fences mid-output.
    text = re.sub(r"```\w*\n?", "", text)

    # --- Step 2: Strip prefix echo ---
    # Many models repeat the entire prompt or a suffix of it.

    # Case A: Full prefix echo (most common with non-FIM models)
    if text.startswith(prefix):
        text = text[len(prefix):]
    else:
        # Case B: Partial echo — model repeats the last N lines of prefix.
        # Try progressively shorter suffixes of the prefix to find the overlap.
        prefix_lines = prefix.split("\n")
        for start_idx in range(len(prefix_lines)):
            partial_prefix = "\n".join(prefix_lines[start_idx:])
            if partial_prefix and text.startswith(partial_prefix):
                text = text[len(partial_prefix):]
                break

    # --- Step 3: Detect and remove suffix repetition ---
    # A very common problem: the model generates text that matches code
    # already present below the cursor (the suffix). For example, if the
    # suffix starts with "\nplt.xlabel('Index')", and the model outputs
    # "plt.xlabel('Index')\nplt.ylabel(...)", the entire output is just
    # echoing existing code and should be discarded or truncated.
    if suffix:
        suffix_lines = suffix.strip().split("\n")
        text_lines = text.split("\n")

        if suffix_lines and text_lines:
            # Check if the first non-empty suffix line appears in the
            # completion. If so, truncate the completion at that point.
            # This catches the model generating "continuation" that is
            # actually the existing code below the cursor.
            first_suffix_line = ""
            for sl in suffix_lines:
                if sl.strip():
                    first_suffix_line = sl.strip()
                    break

            if first_suffix_line and len(first_suffix_line) > 3:
                for i, line in enumerate(text_lines):
                    if line.strip() == first_suffix_line:
                        # Found suffix echo — keep only lines before it.
                        # If it's the very first line, the entire completion
                        # is suffix echo — return empty.
                        text = "\n".join(text_lines[:i])
                        break

    # --- Step 4: Detect and remove prefix content repetition ---
    # Some models output the continuation AND then repeat the whole file.
    # Detect this by checking if the text contains content that already
    # exists in the prefix. If so, truncate at the repetition point.
    prefix_lines = prefix.strip().split("\n") if prefix.strip() else []
    if len(prefix_lines) >= 2:
        # Look for the first line of the prefix appearing in the completion.
        # This signals the model started repeating the file from the top.
        first_prefix_line = prefix_lines[0].strip()
        if first_prefix_line:
            text_lines = text.split("\n")
            for i, line in enumerate(text_lines):
                if i > 0 and line.strip() == first_prefix_line:
                    # Found the start of a repetition — truncate here
                    text = "\n".join(text_lines[:i])
                    break

    # --- Step 5: Clean up whitespace ---
    # Remove leading blank lines but preserve indentation on the first
    # non-blank line (important for code blocks inside functions)
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    # Remove trailing blank lines
    while lines and not lines[-1].strip():
        lines.pop()

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CompletionWorker — runs on a background QThread
# ---------------------------------------------------------------------------

class CompletionWorker(QObject):
    """Background worker that calls OllamaClient.generate_completion().

    Lives on a dedicated QThread to avoid blocking the Qt main thread.
    Receives completion requests via sig_perform_completion and emits
    results via sig_completion_ready.

    Signals:
        sig_completion_ready(int, list): (req_id, completion_items)
            Emitted when a completion finishes successfully.
        sig_error(int, str): Emitted on connection or model errors.
    """

    # Input signal: dispatched from the main thread via QTimer debounce
    # Args: (req_id, model, prefix, suffix, options)
    sig_perform_completion = Signal(int, str, str, str, dict)
    sig_update_host = Signal(str)
    # Output signals: consumed by the provider on the main thread
    # sig_completion_ready(req_id, completion_text, suffix) — text for ghost display
    sig_completion_ready = Signal(int, str, str)
    sig_error = Signal(int, str)

    def __init__(self, host="http://localhost:11434"):
        super().__init__()
        self._host = host
        self._client = None  # Created lazily on the worker thread

        # QThread setup: moveToThread so slots run on the worker thread
        self._thread = QThread()
        self.moveToThread(self._thread)
        self._thread.started.connect(self._on_thread_started)

        # Connect input signal to the processing slot
        self.sig_perform_completion.connect(self._handle_completion)
        self.sig_update_host.connect(self._handle_update_host)

    # --- Thread lifecycle ---

    def start(self):
        """Start the background thread."""
        if not self._thread.isRunning():
            self._thread.start()

    def stop(self):
        """Stop the background thread and wait for it to finish."""
        if self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(5000)  # 5s timeout to avoid hanging on exit

    @Slot()
    def _on_thread_started(self):
        """Initialize the OllamaClient on the worker thread.

        The client is created here (not in __init__) because httpx
        connection pools are thread-local and must be created on the
        thread that will use them.
        """
        self._client = OllamaClient(host=self._host)
        logger.debug("CompletionWorker thread started, client at %s", self._host)

    # --- Completion handling ---

    @Slot(int, str, str, str, dict)
    def _handle_completion(self, req_id, model, prefix, suffix, options):
        """Process a completion request on the worker thread.

        Calls the Ollama FIM API and converts the result to Spyder
        completion items. Emits sig_completion_ready on success or
        sig_error on failure.

        Args:
            req_id: Request ID for correlating with the response.
            model: Ollama model name.
            prefix: Code before the cursor.
            suffix: Code after the cursor.
            options: Model parameters (temperature, num_predict, etc.).
        """
        if self._client is None:
            self.sig_error.emit(req_id, "Completion worker not initialized")
            return

        try:
            # Call the Ollama FIM API (blocking, but we're on a worker thread)
            raw_text = self._client.generate_completion(
                model=model,
                prefix=prefix,
                suffix=suffix,
                options=options,
            )

            # Clean the response: strip prefix echo, suffix echo, markdown
            # fences, and repeated content. Pass the suffix so we can detect
            # when the model just echoes the code below the cursor.
            completion_text = _clean_completion(
                raw_text or "", prefix, suffix
            )

            if not completion_text:
                # Empty completion — emit empty string so provider doesn't hang
                self.sig_completion_ready.emit(req_id, "", suffix)
                return

            # Emit the completion text + suffix for ghost text display.
            # The suffix is passed through so the provider can do final
            # validation if needed.
            self.sig_completion_ready.emit(req_id, completion_text, suffix)

        except Exception as e:
            logger.warning("Completion request %d failed: %s", req_id, e)
            self.sig_error.emit(req_id, str(e))

    def update_host(self, host):
        """Update the Ollama server URL (called from main thread).

        Recreates the client on the next request. Thread-safe because
        the actual client creation happens in the slot.
        """
        self.sig_update_host.emit(host)

    @Slot(str)
    def _handle_update_host(self, host):
        """Update the worker-owned client on the worker thread."""
        self._host = host
        self._client = OllamaClient(host=host)


# ---------------------------------------------------------------------------
# AIChatCompletionProvider — Spyder completion provider
# ---------------------------------------------------------------------------

class AIChatCompletionProvider(SpyderCompletionProvider):
    """SpyderCompletionProvider for AI inline ghost text completions.

    Integrates with Spyder's completion system for file tracking (DID_OPEN,
    DID_CHANGE) but renders completions as ghost text overlays instead of
    the standard dropdown. Uses Ollama's FIM (Fill-in-Middle) API with a
    dedicated background worker thread.

    Key features:
    - Ghost text display (Cursor/VS Code Copilot style, Tab to accept)
    - Debounced requests (300ms) to avoid overwhelming the model
    - Stale request detection (only latest request is delivered)
    - Separate from the chat plugin (independent worker, config, lifecycle)
    - Configurable model, temperature, and token limits

    Entry point: spyder.completions → ai_chat

    Signals:
        sig_ghost_text_ready(str, str): (filename, completion_text)
            Emitted when a completion is ready for ghost text display.
            The plugin connects this to the active editor's GhostTextManager.
    """

    # Custom signal for ghost text — emitted instead of sending to dropdown.
    # (filename, completion_text) — the plugin routes this to the right editor.
    sig_ghost_text_ready = Signal(str, str)

    # --- Provider identity ---
    COMPLETION_PROVIDER_NAME = COMPLETION_PROVIDER_NAME
    DEFAULT_ORDER = 2       # After LSP (order=1), so LSP completions take priority
    SLOW = True             # Hint to Spyder that this provider may have variable latency

    # --- Status bar ---
    # Shows "AI: model-name" / "AI: disabled" / "AI: offline" in Spyder's status bar
    STATUS_BAR_CLASSES = [AIChatCompletionStatus]

    # --- Configuration ---
    CONF_VERSION = "0.1.0"
    # FLAT format: list of (option_name, default_value) tuples.
    # This is the format required by SpyderCompletionProvider (NOT the dict
    # format used by SpyderDockablePlugin).
    CONF_DEFAULTS = [
        ("ollama_host", "http://localhost:11434"),
        ("completion_model", "qooba/qwen3-coder-30b-a3b-instruct:q3_k_m"),
        ("completion_temperature", 0.15),
        ("completion_max_tokens", 512),
        ("completions_enabled", True),
        ("debounce_ms", 300),
    ]

    def __init__(self, parent, config):
        super().__init__(parent, config)

        # --- File content tracking ---
        # Mirrors the document text for each open file. Updated via
        # DID_OPEN / DID_CHANGE events so we always have the latest
        # content for prefix/suffix extraction.
        self._file_contents = {}

        # --- Debouncing ---
        # QTimer single-shot to coalesce rapid keystrokes into one request.
        # Each new DOCUMENT_COMPLETION restarts the timer; only the last
        # one fires after the debounce interval.
        self._debounce_timer = QTimer()
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.timeout.connect(self._debounce_fire)

        # Keep only the freshest requests instead of letting stale work
        # pile up behind the worker thread.
        self._request_queue = _LatestOnlyCompletionQueue()
        # Monotonically increasing request ID for staleness detection.
        # Only the response matching _latest_req_id is delivered.
        self._latest_req_id = 0
        # Track which filename each request belongs to, so we can route
        # the ghost text response to the correct editor
        self._req_filename = {}  # {req_id: filename}

        # --- Background worker ---
        host = self.get_conf("ollama_host")
        self._worker = CompletionWorker(host=host)

        # Connect worker output to our response handler
        self._worker.sig_completion_ready.connect(self._on_completion_ready)
        self._worker.sig_error.connect(self._on_completion_error)

        # --- State ---
        self._started = False

    # --- SpyderCompletionProvider required methods ---

    def get_name(self):
        """Human-readable provider name for UI and logs."""
        return "AI Chat"

    def start(self):
        """Initialize the provider and start the background worker.

        Called by Spyder's CompletionPlugin during startup. Must emit
        sig_provider_ready when the provider is ready to handle requests.
        """
        if self._started:
            return

        enabled = self.get_conf("completions_enabled")
        if not enabled:
            logger.info("AI completions disabled in config, not starting worker")
            self._update_status("AI: disabled")
            # Still emit provider_ready so Spyder doesn't block on us
            self.sig_provider_ready.emit(self.COMPLETION_PROVIDER_NAME)
            self._started = True
            return

        # Start the background worker thread
        self._worker.start()
        self._started = True

        # Show the active model name in the status bar
        model = self.get_conf("completion_model")
        # Shorten long model names for the status bar (e.g., "qooba/qwen3-..." → last part)
        short_model = model.split("/")[-1].split(":")[0] if "/" in model else model
        self._update_status(f"AI: {short_model}")

        logger.info("AI completion provider started")
        self.sig_provider_ready.emit(self.COMPLETION_PROVIDER_NAME)

    def shutdown(self):
        """Stop the provider and clean up the background worker."""
        if not self._started:
            return

        self._debounce_timer.stop()
        self._worker.stop()
        self._started = False
        self._file_contents.clear()
        logger.info("AI completion provider shut down")

    def send_request(self, language, req_type, req, req_id):
        """Handle completion requests from Spyder's editor.

        Routes different request types to appropriate handlers:
        - DID_OPEN: Store initial file content
        - DID_CHANGE: Update stored file content
        - DOCUMENT_COMPLETION: Debounce and dispatch to worker

        Args:
            language: Programming language (e.g., "Python").
            req_type: Request type from CompletionRequestTypes.
            req: Request dict with file, text, offset, etc.
            req_id: Unique request ID for response correlation.
        """
        if req_type == CompletionRequestTypes.DOCUMENT_DID_OPEN:
            # Store the initial file content for later prefix/suffix extraction
            filename = req.get("file", "")
            text = req.get("text", "")
            if filename:
                self._file_contents[filename] = text
                logger.debug("DID_OPEN: tracked %s (%d chars)", filename, len(text))

        elif req_type == CompletionRequestTypes.DOCUMENT_DID_CHANGE:
            # Update the stored file content with the latest version
            filename = req.get("file", "")
            text = req.get("text", "")
            if filename:
                self._file_contents[filename] = text

        elif req_type == CompletionRequestTypes.DOCUMENT_DID_CLOSE:
            filename = req.get("file", "")
            if filename:
                self._file_contents.pop(filename, None)

        elif req_type == CompletionRequestTypes.DOCUMENT_COMPLETION:
            # Completion request — debounce to avoid spamming the model
            self._handle_completion_request(req, req_id)

    def start_completion_services_for_language(self, language):
        """Return True for all languages — AI completions are language-agnostic."""
        return True

    # --- Debouncing ---

    def _handle_completion_request(self, req, req_id):
        """Debounce a completion request.

        Stores the request and restarts the debounce timer. Only the
        last request within the debounce window will be dispatched
        to the worker.

        Args:
            req: DOCUMENT_COMPLETION request dict.
            req_id: Unique request ID.
        """
        # Check if completions are enabled
        if not self.get_conf("completions_enabled"):
            self._emit_empty_response(req_id)
            return

        # Replace any previous debounced request and answer it immediately.
        dropped = self._request_queue.replace_debounced(
            _QueuedCompletionRequest(req=req, req_id=req_id)
        )
        if dropped is not None:
            self._emit_empty_response(dropped.req_id)

        self._latest_req_id = req_id

        # Restart the debounce timer
        debounce_ms = self.get_conf("debounce_ms")
        self._debounce_timer.start(debounce_ms)

    def _debounce_fire(self):
        """Called when the debounce timer expires.

        Extracts prefix/suffix from the stored file content at the
        cursor offset, then dispatches the request to the worker thread.
        """
        request = self._request_queue.pop_debounced()
        if request is None:
            return

        # Do not queue arbitrary backlog behind a running request. Keep
        # only one "latest" request waiting for the active one to finish.
        if self._request_queue.active_req_id is not None:
            dropped = self._request_queue.replace_queued(request)
            if dropped is not None:
                self._emit_empty_response(dropped.req_id)
            return

        self._dispatch_request(request)

    def _dispatch_request(self, request):
        """Dispatch a request to the worker thread if it is still valid."""
        req = request.req
        req_id = request.req_id

        # Extract file content and cursor position
        filename = req.get("file", "")
        offset = req.get("offset", 0)
        text = self._file_contents.get(filename, "")

        if not text:
            logger.debug("No file content for %s, skipping completion", filename)
            self._emit_empty_response(req_id)
            return

        # Split file content at cursor position into prefix (before cursor)
        # and suffix (after cursor) for the FIM model
        prefix = text[:offset]
        suffix = text[offset:]

        # Trim to reasonable sizes to keep model latency low.
        # The model only needs nearby context, not the entire file.
        prefix = prefix[-MAX_PREFIX_CHARS:]
        suffix = suffix[:MAX_SUFFIX_CHARS]

        # Build model options from config
        model = self.get_conf("completion_model")
        options = {
            "temperature": self.get_conf("completion_temperature"),
            "num_predict": self.get_conf("completion_max_tokens"),
        }

        # Track which file this request is for (to route ghost text later)
        self._req_filename[req_id] = filename
        self._request_queue.start_active(req_id)
        self._update_status("AI: generating")

        logger.debug(
            "Dispatching completion req_id=%d, model=%s, prefix=%d chars, suffix=%d chars",
            req_id, model, len(prefix), len(suffix),
        )

        # Dispatch to the worker thread via signal
        self._worker.sig_perform_completion.emit(
            req_id, model, prefix, suffix, options
        )

    # --- Worker response handlers ---

    def _on_completion_ready(self, req_id, completion_text, suffix):
        """Handle a completion result from the worker thread.

        Checks for staleness (only delivers the latest request's result)
        and emits sig_ghost_text_ready for inline display.

        Also emits an empty sig_response_ready to Spyder's completion
        system so it doesn't hang waiting for our response.

        Args:
            req_id: The request ID this completion corresponds to.
            completion_text: The cleaned completion text (str).
            suffix: The code after the cursor (for reference).
        """
        # Always emit empty response to Spyder's completion system so it
        # doesn't block. We handle display via ghost text, not the dropdown.
        self.sig_response_ready.emit(
            self.COMPLETION_PROVIDER_NAME,
            req_id,
            {"params": []},
        )

        # Look up the filename for this request
        filename = self._req_filename.pop(req_id, "")

        if (completion_text
                and filename
                and req_id == self._latest_req_id
                and self.get_conf("completions_enabled")):
            # Emit ghost text signal — the plugin will route this to the
            # appropriate editor's GhostTextManager
            self.sig_ghost_text_ready.emit(filename, completion_text)
            logger.debug(
                "Ghost text ready for %s: %d chars",
                filename, len(completion_text),
            )

        self._finish_request(req_id)

    def _on_completion_error(self, req_id, error_msg):
        """Handle an error from the worker thread.

        Logs the error and updates the status bar to show offline state.

        Args:
            req_id: The request that failed.
            error_msg: Human-readable error description.
        """
        logger.warning("AI completion error: %s", error_msg)
        self._emit_empty_response(req_id)
        restore_ready = True
        if self._looks_offline(error_msg):
            self._update_status("AI: offline")
            restore_ready = False
        else:
            self._set_ready_status()
        self._req_filename.pop(req_id, None)
        self._finish_request(req_id, restore_ready=restore_ready)

    # --- Status bar helper ---

    def _update_status(self, text):
        """Update the status bar widget with the given text.

        Uses Spyder's sig_call_statusbar signal to invoke set_value()
        on the AIChatCompletionStatus widget.

        Args:
            text: Short status string (e.g., "AI: model-name").
        """
        self.sig_call_statusbar.emit(
            AIChatCompletionStatus.ID,
            "set_value",
            (text,),
            {},
        )

    def _set_ready_status(self):
        """Restore the steady-state model status text."""
        if not self._started:
            return

        model = self.get_conf("completion_model")
        short_model = (
            model.split("/")[-1].split(":")[0] if "/" in model else model
        )
        self._update_status(f"AI: {short_model}")

    def _emit_empty_response(self, req_id):
        """Emit an empty completion response for a request id."""
        self.sig_response_ready.emit(
            self.COMPLETION_PROVIDER_NAME,
            req_id,
            {"params": []},
        )

    def _finish_request(self, req_id, restore_ready=True):
        """Finish an active request and dispatch the newest queued one."""
        self._request_queue.finish_active(req_id)
        next_request = self._request_queue.pop_queued()
        if next_request is not None and self.get_conf("completions_enabled"):
            self._dispatch_request(next_request)
            return

        if restore_ready and self.get_conf("completions_enabled"):
            self._set_ready_status()

    @staticmethod
    def _looks_offline(error_msg):
        """Return True if an error looks like an Ollama connectivity issue."""
        lowered = error_msg.lower()
        offline_markers = (
            "connect", "connection refused", "failed to establish",
            "timed out", "timeout", "all connection attempts failed",
        )
        return any(marker in lowered for marker in offline_markers)

    # --- Config change handlers ---

    @on_conf_change(option="ollama_host")
    def on_host_changed(self, value):
        """Update the worker's Ollama server URL when config changes."""
        self._worker.update_host(value)
        if self.get_conf("completions_enabled"):
            self._set_ready_status()

    @on_conf_change(option="completions_enabled")
    def on_enabled_changed(self, value):
        """Start or stop the worker when completions are toggled."""
        if value and not self._worker._thread.isRunning():
            self._worker.start()
            self._set_ready_status()
            logger.info("AI completions enabled")
        elif value:
            self._set_ready_status()
        elif not value:
            self._debounce_timer.stop()
            for req_id in self._request_queue.clear_pending():
                self._emit_empty_response(req_id)
            self._update_status("AI: disabled")
            logger.info("AI completions disabled")
