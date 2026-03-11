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
  Editor types → Spyder → send_request(DOCUMENT_COMPLETION) → debounce(100ms)
  → CompletionWorker.perform_completion() [QThread]
  → OllamaClient.generate_completion() → sig_completion_ready
  → sig_ghost_text_ready → plugin → GhostTextManager → inline overlay
"""

import logging
import os
import re
from collections import OrderedDict
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
MIN_PROMPT_CHARS = 10
DEFAULT_DEBOUNCE_MS = 100
DEFAULT_CACHE_SIZE = 64

MIDDLE_OF_LINE_ALLOWED_SUFFIX_RE = re.compile(
    r"^\s*[\)\}\]\"'`]*\s*[:{;,]?\s*$"
)
PYTHON_BLOCK_START_RE = re.compile(
    r"^\s*(?:async\s+def|def|class|if|elif|else|for|while|with|try|except|finally|match|case)\b.*:\s*$"
)

_COMMENT_MARKERS_BY_EXTENSION = {
    ".py": "#",
    ".pyw": "#",
    ".sh": "#",
    ".bash": "#",
    ".zsh": "#",
    ".yml": "#",
    ".yaml": "#",
    ".toml": "#",
    ".ini": "#",
    ".cfg": "#",
    ".r": "#",
    ".rb": "#",
    ".js": "//",
    ".jsx": "//",
    ".ts": "//",
    ".tsx": "//",
    ".java": "//",
    ".c": "//",
    ".cc": "//",
    ".cpp": "//",
    ".cxx": "//",
    ".h": "//",
    ".hpp": "//",
    ".go": "//",
    ".rs": "//",
    ".php": "//",
    ".swift": "//",
    ".kt": "//",
    ".kts": "//",
    ".scala": "//",
    ".sql": "--",
    ".html": "<!--",
    ".htm": "<!--",
    ".xml": "<!--",
    ".css": "/*",
    ".scss": "/*",
    ".sass": "/*",
}


@dataclass
class _TrackedDocumentState:
    """Latest known text plus a local monotonic version for one file."""

    text: str
    version: int = 1


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
    target: "_CompletionTarget"


@dataclass(frozen=True)
class _CompletionCacheKey:
    """One normalized key for the LRU completion cache."""

    host: str
    model: str
    temperature: float
    num_predict: int
    single_line: bool
    prompt_prefix: str
    suffix: str


@dataclass(frozen=True)
class _CompletionTarget:
    """Exact editor state a completion request was created for."""

    filename: str
    version: int
    line: int
    column: int
    offset: int
    current_word: str = ""

    def to_payload(self):
        """Serialize a target for ghost-text routing."""
        return {
            "filename": self.filename,
            "version": self.version,
            "line": self.line,
            "column": self.column,
            "offset": self.offset,
            "current_word": self.current_word,
        }


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


class _CompletionCache:
    """Small LRU cache for recently-used completion results."""

    _MISSING = object()

    def __init__(self, max_entries=DEFAULT_CACHE_SIZE):
        self._max_entries = max(1, int(max_entries))
        self._entries = OrderedDict()

    def get(self, key):
        """Return one cached completion or ``_MISSING``."""
        if key not in self._entries:
            return self._MISSING

        value = self._entries.pop(key)
        self._entries[key] = value
        return value

    def put(self, key, value):
        """Store one completion result and refresh LRU order."""
        if key in self._entries:
            self._entries.pop(key)

        self._entries[key] = value
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self):
        """Drop every cached completion."""
        self._entries.clear()

    def __len__(self):
        return len(self._entries)


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

    # Remove trailing whitespace from each line. This almost never helps
    # a ghost suggestion and often creates ugly visual padding.
    lines = [line.rstrip() for line in lines]

    cleaned = "\n".join(lines).rstrip()
    if suffix and cleaned and suffix.startswith(cleaned):
        return ""
    return cleaned


def _build_completion_path_marker(filename):
    """Return a small path marker that improves completion prompt quality."""
    basename = os.path.basename(filename or "") or "untitled"
    extension = os.path.splitext(basename)[1].lower()
    marker = _COMMENT_MARKERS_BY_EXTENSION.get(extension, "#")

    if marker == "<!--":
        return f"<!-- Path: {basename} -->"
    if marker == "/*":
        return f"/* Path: {basename} */"
    return f"{marker} Path: {basename}"


def _build_prompt_prefix(filename, prefix):
    """Prepend a lightweight path marker to the completion prompt."""
    marker = _build_completion_path_marker(filename)
    if not prefix:
        return f"{marker}\n"
    return f"{marker}\n{prefix}"


def _looks_like_valid_middle_of_line_suffix(suffix):
    """Return True when the rest of the current line is light punctuation."""
    current_line_suffix = (suffix or "").split("\n", 1)[0]
    if not current_line_suffix.strip():
        return True
    return bool(MIDDLE_OF_LINE_ALLOWED_SUFFIX_RE.fullmatch(current_line_suffix))


def _should_allow_multiline_completion(prefix):
    """Allow multiline completions only in block-like contexts."""
    lines = prefix.splitlines()
    for line in reversed(lines):
        stripped = line.rstrip()
        if stripped.strip():
            if PYTHON_BLOCK_START_RE.match(stripped):
                return True
            if stripped.endswith(("(", "[", "{", "\\")):
                return True
            return False
    return False


def _completion_already_in_document(completion_text, suffix):
    """Return True when the generated text already exists after the cursor."""
    if not completion_text:
        return False

    normalized_completion = completion_text.rstrip()
    if not normalized_completion:
        return False

    return (suffix or "").startswith(normalized_completion)


def _trim_suffix_overlap(completion_text, suffix):
    """Trim one trailing overlap between the completion and document suffix."""
    if not completion_text or not suffix:
        return completion_text

    max_overlap = min(len(completion_text), len(suffix))
    for overlap_size in range(max_overlap, 0, -1):
        if completion_text[-overlap_size:] == suffix[:overlap_size]:
            trimmed = completion_text[:-overlap_size]
            return trimmed.rstrip()

    return completion_text


def _looks_repetitive_completion(completion_text):
    """Return True when a completion is mostly repetition noise."""
    if not completion_text:
        return False

    stripped = completion_text.strip()
    if not stripped:
        return False

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 3 and len(set(lines)) == 1:
        return True

    tokens = re.findall(r"\S+", stripped)
    if len(tokens) >= 4 and len(set(tokens)) == 1:
        return True

    return False


def _finalize_completion_text(completion_text, suffix):
    """Apply final overlap and repetition filters before ghost display."""
    if _completion_already_in_document(completion_text, suffix):
        return "", "duplicate"

    trimmed = _trim_suffix_overlap(completion_text, suffix)
    if not trimmed:
        return "", "overlap"

    if _looks_repetitive_completion(trimmed):
        return "", "repetition"

    return trimmed, None


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
            client_options = dict(options or {})
            single_line = bool(client_options.pop("_single_line", False))

            # Call the Ollama FIM API (blocking, but we're on a worker thread)
            raw_text = self._client.generate_completion(
                model=model,
                prefix=prefix,
                suffix=suffix,
                options=client_options,
                single_line=single_line,
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
    - Debounced requests (100ms by default) to avoid overwhelming the model
    - Stale request detection (only latest request is delivered)
    - Separate from the chat plugin (independent worker, config, lifecycle)
    - Configurable model, temperature, and token limits

    Entry point: spyder.completions → ai_chat

    Signals:
        sig_ghost_text_ready(str, str, dict): (filename, completion_text, target)
            Emitted when a completion is ready for ghost text display.
            The plugin connects this to the active editor's GhostTextManager.
    """

    # Custom signal for ghost text — emitted instead of sending to dropdown.
    # (filename, completion_text, target_dict) — the plugin routes this to
    # the right editor and validates the cursor target before display.
    sig_ghost_text_ready = Signal(str, str, dict)

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
        ("debounce_ms", DEFAULT_DEBOUNCE_MS),
    ]

    def __init__(self, parent, config):
        super().__init__(parent, config)

        # --- File content tracking ---
        # Mirrors the document text for each open file. Updated via
        # DID_OPEN / DID_CHANGE events so we always have the latest
        # content for prefix/suffix extraction.
        self._document_states = {}
        # Latest completion target requested for each filename.
        self._latest_target_by_filename = {}

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
        # Track exact request targets so responses can be matched against
        # the current editor state before ghost text is shown.
        self._req_targets = {}
        self._req_cache_keys = {}
        self._completion_cache = _CompletionCache()
        self._metrics = {
            "requests_received": 0,
            "skipped": 0,
            "debounced_dropped": 0,
            "queued_dropped": 0,
            "dispatched": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "shown": 0,
            "accepted": 0,
            "accepted_full": 0,
            "accepted_word": 0,
            "accepted_line": 0,
            "typed_advances": 0,
            "dismissed_escape": 0,
            "dismissed_typing": 0,
            "dismissed_cursor_move": 0,
            "dismissed_popup_visible": 0,
            "suppressed_popup_visible": 0,
            "stale_discarded": 0,
            "filtered_duplicate": 0,
            "filtered_repetition": 0,
            "filtered_overlap": 0,
            "errors": 0,
            "offline_errors": 0,
        }
        self._current_status_text = "AI: idle"

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
        self._document_states.clear()
        self._latest_target_by_filename.clear()
        self._req_targets.clear()
        self._req_cache_keys.clear()
        self._completion_cache.clear()
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
                self._document_states[filename] = _TrackedDocumentState(
                    text=text,
                    version=1,
                )
                logger.debug(
                    "DID_OPEN: tracked %s (version=%d, %d chars)",
                    filename,
                    1,
                    len(text),
                )

        elif req_type == CompletionRequestTypes.DOCUMENT_DID_CHANGE:
            # Update the stored file content with the latest version
            filename = req.get("file", "")
            text = req.get("text", "")
            if filename:
                previous = self._document_states.get(filename)
                version = 1 if previous is None else previous.version + 1
                self._document_states[filename] = _TrackedDocumentState(
                    text=text,
                    version=version,
                )
                logger.debug(
                    "DID_CHANGE: tracked %s (version=%d, %d chars)",
                    filename,
                    version,
                    len(text),
                )

        elif req_type == CompletionRequestTypes.DOCUMENT_DID_CLOSE:
            filename = req.get("file", "")
            if filename:
                self._document_states.pop(filename, None)
                self._latest_target_by_filename.pop(filename, None)

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
        self._increment_metric("requests_received")
        target = self._build_completion_target(req)
        if target is None:
            self._emit_empty_response(req_id)
            return

        self._latest_target_by_filename[target.filename] = target

        # Check if completions are enabled
        if not self.get_conf("completions_enabled"):
            self._emit_empty_response(req_id)
            return

        # Replace any previous debounced request and answer it immediately.
        dropped = self._request_queue.replace_debounced(
            _QueuedCompletionRequest(req=req, req_id=req_id, target=target)
        )
        if dropped is not None:
            self._increment_metric("debounced_dropped")
            self._emit_empty_response(dropped.req_id)

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
                self._increment_metric("queued_dropped")
                self._emit_empty_response(dropped.req_id)
            return

        self._dispatch_request(request)

    def _dispatch_request(self, request):
        """Dispatch a request to the worker thread if it is still valid."""
        req = request.req
        req_id = request.req_id
        target = request.target

        # Extract file content and cursor position
        filename = target.filename
        state = self._document_states.get(filename)
        text = state.text if state is not None else ""

        if not text:
            logger.debug("No file content for %s, skipping completion", filename)
            self._emit_empty_response(req_id)
            return

        if state is None or state.version != target.version:
            logger.debug(
                "Skipping completion req_id=%d for %s because the document version changed",
                req_id,
                filename,
            )
            self._emit_empty_response(req_id)
            return

        offset = max(0, min(target.offset, len(text)))

        # Split file content at cursor position into prefix (before cursor)
        # and suffix (after cursor) for the FIM model
        prefix = text[:offset]
        suffix = text[offset:]

        # Trim to reasonable sizes to keep model latency low.
        # The model only needs nearby context, not the entire file.
        prefix = prefix[-MAX_PREFIX_CHARS:]
        suffix = suffix[:MAX_SUFFIX_CHARS]

        skip_reason = self._should_skip_completion(req, prefix, suffix)
        if skip_reason:
            self._increment_metric("skipped")
            logger.debug(
                "Skipping completion req_id=%d for %s: %s",
                req_id,
                filename,
                skip_reason,
            )
            self._emit_empty_response(req_id)
            self._set_ready_status()
            return

        prompt_prefix = _build_prompt_prefix(filename, prefix)
        allow_multiline = _should_allow_multiline_completion(prefix)

        # Build model options from config
        model = self.get_conf("completion_model")
        options = {
            "temperature": self.get_conf("completion_temperature"),
            "num_predict": self.get_conf("completion_max_tokens"),
            "_single_line": not allow_multiline,
        }
        cache_key = _CompletionCacheKey(
            host=self.get_conf("ollama_host"),
            model=model,
            temperature=float(options["temperature"]),
            num_predict=int(options["num_predict"]),
            single_line=bool(options["_single_line"]),
            prompt_prefix=prompt_prefix,
            suffix=suffix,
        )

        # Track which request target this response belongs to.
        self._req_targets[req_id] = target
        self._req_cache_keys[req_id] = cache_key

        cached_completion = self._completion_cache.get(cache_key)
        if cached_completion is not _CompletionCache._MISSING:
            self._increment_metric("cache_hits")
            logger.debug(
                "Cache hit for completion req_id=%d on %s",
                req_id,
                filename,
            )
            self._deliver_completion_result(
                req_id,
                str((cached_completion or {}).get("text", "")),
                suffix,
                source="cache",
                cached_filter_reason=(cached_completion or {}).get(
                    "filter_reason"
                ),
            )
            return

        self._increment_metric("cache_misses")
        self._request_queue.start_active(req_id)
        self._increment_metric("dispatched")
        self._update_status("AI: generating")

        logger.debug(
            "Dispatching completion req_id=%d, model=%s, version=%d, line=%d, column=%d, single_line=%s, prefix=%d chars, suffix=%d chars",
            req_id,
            model,
            target.version,
            target.line,
            target.column,
            not allow_multiline,
            len(prompt_prefix),
            len(suffix),
        )

        # Dispatch to the worker thread via signal
        self._worker.sig_perform_completion.emit(
            req_id, model, prompt_prefix, suffix, options
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
        self._deliver_completion_result(req_id, completion_text, suffix, source="worker")

    def _on_completion_error(self, req_id, error_msg):
        """Handle an error from the worker thread.

        Logs the error and updates the status bar to show offline state.

        Args:
            req_id: The request that failed.
            error_msg: Human-readable error description.
        """
        logger.warning("AI completion error: %s", error_msg)
        self._increment_metric("errors")
        self._emit_empty_response(req_id)
        restore_ready = True
        if self._looks_offline(error_msg):
            self._increment_metric("offline_errors")
            self._update_status("AI: offline")
            restore_ready = False
        else:
            self._set_ready_status()
        self._req_targets.pop(req_id, None)
        self._req_cache_keys.pop(req_id, None)
        self._finish_request(req_id, restore_ready=restore_ready)

    # --- Status bar helper ---

    def _update_status(self, text):
        """Update the status bar widget with the given text.

        Uses Spyder's sig_call_statusbar signal to invoke set_value()
        on the AIChatCompletionStatus widget.

        Args:
            text: Short status string (e.g., "AI: model-name").
        """
        self._current_status_text = text
        logger.debug("Completion status updated: %s", text)
        self.sig_call_statusbar.emit(
            AIChatCompletionStatus.ID,
            "set_value",
            (self._build_status_payload(text),),
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
        self._completion_cache.clear()
        if self.get_conf("completions_enabled"):
            self._set_ready_status()

    @on_conf_change(option="completion_model")
    def on_completion_model_changed(self, _value):
        """Refresh steady-state status and clear stale cached completions."""
        self._completion_cache.clear()
        if self.get_conf("completions_enabled"):
            self._set_ready_status()

    @on_conf_change(option="completion_temperature")
    def on_completion_temperature_changed(self, _value):
        """Drop cached completions when the generation temperature changes."""
        self._completion_cache.clear()

    @on_conf_change(option="completion_max_tokens")
    def on_completion_max_tokens_changed(self, _value):
        """Drop cached completions when the completion budget changes."""
        self._completion_cache.clear()

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

    # --- Request metadata helpers ---

    def _build_completion_target(self, req):
        """Build a staleness target from a Spyder DOCUMENT_COMPLETION request."""
        filename = req.get("file", "")
        if not filename:
            return None

        state = self._document_states.get(filename)
        version = state.version if state is not None else 0

        return _CompletionTarget(
            filename=filename,
            version=version,
            line=int(req.get("line", 0)),
            column=int(req.get("column", 0)),
            offset=int(req.get("offset", 0)),
            current_word=str(req.get("current_word", "") or ""),
        )

    def _is_target_current(self, target):
        """Return True when the target still matches the latest editor state."""
        state = self._document_states.get(target.filename)
        if state is None or state.version != target.version:
            return False

        latest_target = self._latest_target_by_filename.get(target.filename)
        return latest_target == target

    @staticmethod
    def _should_skip_completion(req, prefix, suffix):
        """Return a human-readable skip reason for a low-value completion."""
        if req.get("selection_start") != req.get("selection_end"):
            return "selection is active"

        if len(prefix.strip()) < MIN_PROMPT_CHARS:
            return "not enough prefix context"

        if not _looks_like_valid_middle_of_line_suffix(suffix):
            return "substantial code already exists to the right of the cursor"

        return None

    def record_ghost_event(self, event_name, payload=None):
        """Record one ghost-text lifecycle event from the editor layer."""
        payload = payload or {}
        reason = str(payload.get("reason", "") or "")
        method = str(payload.get("method", "") or "")

        if event_name == "shown":
            self._increment_metric("shown")
        elif event_name == "accepted":
            self._increment_metric("accepted")
            if method == "word":
                self._increment_metric("accepted_word")
            elif method == "line":
                self._increment_metric("accepted_line")
            else:
                self._increment_metric("accepted_full")
        elif event_name == "advanced" and method == "typed":
            self._increment_metric("typed_advances")
        elif event_name == "dismissed":
            metric_name = {
                "escape": "dismissed_escape",
                "typing": "dismissed_typing",
                "cursor_move": "dismissed_cursor_move",
                "popup_visible": "dismissed_popup_visible",
            }.get(reason)
            if metric_name:
                self._increment_metric(metric_name)
        elif event_name == "suppressed" and reason in (
            "popup_visible",
            "native_popup",
        ):
            self._increment_metric("suppressed_popup_visible")

        logger.debug(
            "Ghost lifecycle event: event=%s method=%s reason=%s payload=%s",
            event_name,
            method or "-",
            reason or "-",
            payload,
        )
        self._refresh_status_tooltip()

    def get_metrics_snapshot(self):
        """Return one serializable snapshot of local completion metrics."""
        snapshot = dict(self._metrics)
        snapshot["cache_entries"] = len(self._completion_cache)
        snapshot["current_status"] = self._current_status_text
        return snapshot

    def _deliver_completion_result(self, req_id, completion_text, suffix,
                                   source="worker", cached_filter_reason=None):
        """Deliver one completion result from the worker or local cache."""
        self._emit_empty_response(req_id)

        target = self._req_targets.pop(req_id, None)
        cache_key = self._req_cache_keys.pop(req_id, None)
        if target is None:
            self._finish_request(req_id)
            return

        filename = target.filename
        state = self._document_states.get(filename)

        if not self._is_target_current(target):
            self._increment_metric("stale_discarded")
            logger.debug(
                "Discarded stale completion req_id=%d for %s from %s",
                req_id,
                filename,
                source,
            )
            self._finish_request(req_id)
            return

        current_suffix = ""
        if state is not None and 0 <= target.offset <= len(state.text):
            current_suffix = state.text[target.offset:]

        if source == "cache":
            final_text = completion_text or ""
            filter_reason = cached_filter_reason
        else:
            final_text, filter_reason = _finalize_completion_text(
                completion_text,
                current_suffix,
            )

        if cache_key is not None:
            self._completion_cache.put(
                cache_key,
                {
                    "text": final_text,
                    "filter_reason": filter_reason,
                },
            )

        if final_text and filename and self.get_conf("completions_enabled"):
            self.sig_ghost_text_ready.emit(
                filename,
                final_text,
                target.to_payload(),
            )
            logger.debug(
                "Ghost text ready for %s from %s: %d chars",
                filename,
                source,
                len(final_text),
            )
        elif filter_reason:
            metric_name = {
                "duplicate": "filtered_duplicate",
                "repetition": "filtered_repetition",
                "overlap": "filtered_overlap",
            }.get(filter_reason)
            if metric_name:
                self._increment_metric(metric_name)
            logger.debug(
                "Filtered completion req_id=%d for %s from %s: %s",
                req_id,
                filename,
                source,
                filter_reason,
            )

        self._finish_request(req_id)

    def _increment_metric(self, key, amount=1):
        """Increment one local completion metric counter."""
        self._metrics[key] = int(self._metrics.get(key, 0)) + int(amount)

    def _refresh_status_tooltip(self):
        """Re-emit the current status value so the tooltip stays current."""
        if not self._started:
            return
        self._update_status(self._current_status_text)

    def _build_status_payload(self, short_text):
        """Build the status-bar payload with a metrics-rich tooltip."""
        metrics = self.get_metrics_snapshot()
        tooltip_lines = [
            "AI Code Completion status (Ollama)",
            "",
            f"Requests: {metrics['requests_received']}",
            (
                "Shown / accepted: "
                f"{metrics['shown']} / {metrics['accepted']}"
            ),
            (
                "Accepted modes: "
                f"full={metrics['accepted_full']}, "
                f"word={metrics['accepted_word']}, "
                f"line={metrics['accepted_line']}"
            ),
            (
                "Cache: "
                f"{metrics['cache_hits']} hits, "
                f"{metrics['cache_misses']} misses, "
                f"{metrics['cache_entries']} entries"
            ),
            (
                "Filtered: "
                f"duplicate={metrics['filtered_duplicate']}, "
                f"repetition={metrics['filtered_repetition']}, "
                f"overlap={metrics['filtered_overlap']}, "
                f"stale={metrics['stale_discarded']}"
            ),
            (
                "Dismissed: "
                f"escape={metrics['dismissed_escape']}, "
                f"typing={metrics['dismissed_typing']}, "
                f"cursor={metrics['dismissed_cursor_move']}, "
                f"popup={metrics['dismissed_popup_visible']}"
            ),
            (
                "Popup suppression / errors: "
                f"{metrics['suppressed_popup_visible']} / {metrics['errors']}"
            ),
        ]
        return {
            "short": short_text,
            "long": "\n".join(tooltip_lines),
        }
