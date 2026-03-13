"""Thin, stateless wrappers around completion backends.

This module provides blocking client helpers for inline completions.
They have no Qt dependencies and must only be called from a worker thread.

All methods are synchronous (blocking).
"""

import logging

import httpx
from ollama import Client

logger = logging.getLogger(__name__)


def _blank_line_after_complete_statement(prefix):
    """Return True when the cursor is on a fresh line after a complete line."""
    text = str(prefix or "")
    current_line = text.rsplit("\n", 1)[-1]
    if current_line.strip():
        return False

    lines = text.splitlines()
    for line in reversed(lines):
        stripped = line.rstrip()
        if not stripped.strip():
            continue
        if stripped.endswith(("(", "[", "{", "\\", "+", "-", "*", "/", "%", "&", "|", "^", ",")):
            return False
        if stripped.endswith((" and", " or", " in", " is", " not")):
            return False
        if stripped.endswith(":"):
            return False
        return True
    return False


def _has_meaningful_suffix_context(suffix):
    """Return True when the suffix contains more than whitespace."""
    return bool(str(suffix or "").strip())


def build_completion_system_prompt(system=None, *, blank_line_mode=False):
    """Return the shared system prompt for completion backends."""
    base = system or (
        "You are a code completion engine inside an IDE. "
        "Fill in the code between the prefix (before cursor) and "
        "suffix (after cursor). "
        "RULES: "
        "1) Output ONLY the new code to insert at the cursor position. "
        "2) NEVER repeat code from the prefix (before cursor). "
        "3) NEVER repeat code from the suffix (after cursor). "
        "4) The inserted code must integrate seamlessly — it will be "
        "placed between the prefix and suffix. "
        "5) NEVER use markdown formatting — no ```, no fences. "
        "6) Output raw code only, no explanations. "
        "7) Write complete, well-structured code. Include docstrings, "
        "comments, and full function bodies when appropriate."
    )
    if blank_line_mode:
        base += (
            " 8) If the cursor is on a fresh blank line after a complete "
            "statement, start the next statement or block on the current line. "
            "Do NOT continue the previous line with a leading operator or comma."
        )
    return base


def build_completion_stop_sequences(single_line=False):
    """Return default stop sequences for inline completions."""
    if single_line:
        return ["\n"]
    return [
        "\n\n\n",
        "\nclass ",
        "\ndef ",
        "\n# %%",
    ]


def _is_blank_line_completion_context(prefix):
    """Return True when the cursor sits on an otherwise blank line."""
    current_line = (prefix or "").rsplit("\n", 1)[-1]
    return not current_line.strip()


def build_completion_user_prompt(
    prefix,
    suffix="",
    single_line=False,
    *,
    blank_line_mode=False,
):
    """Render one provider-agnostic prefix/suffix completion prompt."""
    completion_scope = (
        "Return a concise continuation for the current line."
        if single_line
        else "Return the next complete code block or lines to insert."
    )
    if blank_line_mode:
        completion_scope = (
            "Return the next statement or block that should start on the "
            "current blank line."
        )
    return (
        "Complete the code at the cursor and return only the inserted text.\n\n"
        "Prefix (before cursor):\n"
        f"{prefix or ''}\n\n"
        "Suffix (after cursor):\n"
        f"{suffix or ''}\n\n"
        f"{completion_scope} "
        "Return only the code to insert at the cursor. "
        "Do not repeat the prefix. Do not repeat the suffix. "
        "Do not start with a bare operator or comma when the current line is blank. "
        "Do not use markdown fences or explanations."
    )


def _looks_like_empty_completion_meta(text):
    """Return True when one completion response is only formatting/meta."""
    stripped = str(text or "").strip().lower()
    if not stripped:
        return True
    fence_only = {
        "```",
        "```python",
        "```py",
        "python",
    }
    return stripped in fence_only


class OllamaClient:
    """Stateless client for communicating with the Ollama API.

    Wraps the official ollama Python package to provide a clean interface
    for the plugin's backend worker. All methods are blocking — they must
    be called from a background thread to avoid freezing the UI.

    Args:
        host: Ollama server URL (e.g., "http://localhost:11434").
    """

    def __init__(self, host="http://localhost:11434"):
        self._host = host
        self._client = Client(host=host)
        # Track models that don't support FIM (suffix) to avoid retrying
        self._fim_unsupported_models = set()

    @property
    def host(self):
        """The configured Ollama server URL."""
        return self._host

    def is_available(self, timeout=2.0):
        """Check if the Ollama server is reachable.

        Performs a lightweight HTTP GET to the model listing endpoint
        as a health check. Returns False on connection failure or timeout.

        Args:
            timeout: Connection timeout in seconds.

        Returns:
            True if the server responds with HTTP 200.
        """
        import httpx
        try:
            response = httpx.get(
                f"{self._host}/api/tags", timeout=timeout
            )
            return response.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    def list_models(self):
        """List all locally available models with metadata.

        Queries the Ollama server for installed models and returns
        a sorted list of model info dictionaries.

        Returns:
            List of dicts with keys: name, family, parameter_size,
            quantization, size_gb. Sorted alphabetically by name.
            Empty list if the server is unreachable.
        """
        try:
            response = self._client.list()
            models = []
            for m in response.models:
                models.append({
                    "name": m.model,
                    "family": m.details.family,
                    "parameter_size": m.details.parameter_size,
                    "quantization": m.details.quantization_level,
                    "size_gb": round(m.size / 1e9, 1),
                })
            # Sort alphabetically for consistent ordering in the UI
            models.sort(key=lambda x: x["name"])
            return models
        except Exception as e:
            logger.warning("Failed to list models: %s", e)
            return []

    def chat_stream(self, model, messages, options=None):
        """Send a chat request and yield streaming response chunks.

        Opens a streaming connection to Ollama's /api/chat endpoint
        and yields each token as it arrives. The final chunk includes
        performance metrics (token count, generation speed).

        Args:
            model: Ollama model name (e.g., "gpt-oss-20b-abliterated").
            messages: Conversation history as list of role/content dicts.
            options: Model parameters dict (temperature, num_predict, etc.).

        Yields:
            Dict with keys:
                - content (str): The text token for this chunk.
                - done (bool): Whether this is the final chunk.
            Final chunk additionally includes:
                - eval_count (int): Number of tokens generated.
                - eval_duration (int): Generation time in nanoseconds.
                - prompt_eval_count (int): Number of prompt tokens.

        Raises:
            ollama.ResponseError: Model not found or server error.
            ConnectionError: Ollama server is unreachable.
        """
        stream = self._client.chat(
            model=model,
            messages=messages,
            stream=True,
            options=options or {},
        )
        for chunk in stream:
            result = {
                "content": chunk.message.content,
                "done": getattr(chunk, "done", False),
            }
            # The final chunk carries performance metrics for display.
            # Use getattr for safety since intermediate chunks may not
            # have these attributes populated.
            if result["done"]:
                result["eval_count"] = getattr(
                    chunk, "eval_count", 0
                ) or 0
                result["eval_duration"] = getattr(
                    chunk, "eval_duration", 0
                ) or 0
                result["prompt_eval_count"] = getattr(
                    chunk, "prompt_eval_count", 0
                ) or 0
            yield result

    def generate_completion(self, model, prefix, suffix="",
                            system=None, options=None, single_line=False):
        """Generate a FIM (Fill-in-Middle) code completion.

        Calls Ollama's /api/generate endpoint with prefix and suffix
        to produce an inline completion at the cursor position. This is
        a synchronous, non-streaming call because completions need the
        full result before presenting it in the editor dropdown.

        If the model doesn't support FIM (suffix), automatically falls
        back to prefix-only completion. This is tracked per-model to
        avoid retrying on every request.

        Typical latency: 0.5–2s for small MoE models (e.g., Qwen3-Coder-3B).

        Args:
            model: Ollama model name (e.g., "qooba/qwen3-coder-30b...").
            prefix: Code text before the cursor (the model completes after this).
            suffix: Code text after the cursor (provides right-side context).
            system: System prompt override. Defaults to a minimal code-only prompt.
            options: Model parameters dict (temperature, num_predict, etc.).
            single_line: If True, stop at the first newline. Used for the
                common "finish this line" case so completions stay concise.

        Returns:
            The generated completion text (str).

        Raises:
            ollama.ResponseError: Model not found or server error.
            ConnectionError: Ollama server is unreachable.
        """
        from ollama import ResponseError

        blank_line_mode = _blank_line_after_complete_statement(prefix)
        default_system = build_completion_system_prompt(
            system=system,
            blank_line_mode=blank_line_mode,
        )
        merged_options = dict(options or {})

        # Add stop sequences to prevent the model from generating too much.
        # Stop on patterns that indicate a new top-level definition or
        # excessive blank lines (signals the function/block is complete).
        if "stop" not in merged_options:
            merged_options["stop"] = build_completion_stop_sequences(
                single_line=single_line
            )
        logger.info(
            "Ollama completion request: host=%s model=%s single_line=%s prefix_chars=%d suffix_chars=%d stop=%s",
            self._host,
            model,
            single_line,
            len(prefix or ""),
            len(suffix or ""),
            merged_options.get("stop"),
        )

        # Try FIM (with suffix) first, unless we already know this model
        # doesn't support it. Fall back to prefix-only on "does not support
        # insert" errors.
        use_suffix = (
            _has_meaningful_suffix_context(suffix)
            and model not in self._fim_unsupported_models
        )

        if use_suffix:
            try:
                response = self._client.generate(
                    model=model,
                    prompt=prefix,
                    suffix=suffix,
                    system=default_system,
                    options=merged_options,
                    stream=False,
                )
                logger.info(
                    "Ollama completion response received via FIM: model=%s chars=%d",
                    model,
                    len(getattr(response, "response", "") or ""),
                )
                return response.response
            except ResponseError as e:
                if "does not support insert" in str(e):
                    # Model doesn't support FIM — remember this and fall back
                    self._fim_unsupported_models.add(model)
                    logger.info(
                        "Model %s doesn't support FIM, falling back to "
                        "prefix-only completion", model
                    )
                else:
                    raise

        # Prefix-only fallback works poorly on several local models because it
        # tends to emit markdown fences or other meta tokens. Use the chat API
        # instead, which behaves much more reliably for these models.
        try:
            response_text = self._generate_completion_via_chat(
                model=model,
                prefix=prefix,
                suffix=suffix,
                system=default_system,
                options=merged_options,
                single_line=single_line,
                blank_line_mode=blank_line_mode,
            )
            if _looks_like_empty_completion_meta(response_text):
                logger.info(
                    "Ollama completion chat fallback returned meta-only output: model=%s raw=%r",
                    model,
                    response_text,
                )
                if single_line and _is_blank_line_completion_context(prefix):
                    retry_options = dict(merged_options)
                    retry_options["stop"] = build_completion_stop_sequences(
                        single_line=False
                    )
                    retry_text = self._generate_completion_via_chat(
                        model=model,
                        prefix=prefix,
                        suffix=suffix,
                        system=default_system,
                        options=retry_options,
                        single_line=False,
                        blank_line_mode=blank_line_mode,
                    )
                    logger.info(
                        "Ollama completion blank-line retry response received via chat fallback: model=%s chars=%d raw=%r",
                        model,
                        len(retry_text or ""),
                        retry_text,
                    )
                    if not _looks_like_empty_completion_meta(retry_text):
                        return retry_text
            logger.info(
                "Ollama completion response received via chat fallback: model=%s chars=%d",
                model,
                len(response_text or ""),
            )
            if not _looks_like_empty_completion_meta(response_text):
                return response_text
        except Exception as chat_error:
            logger.info(
                "Ollama completion chat fallback failed for model=%s; retrying prefix-only generate: %s",
                model,
                chat_error,
            )

        response = self._client.generate(
            model=model,
            prompt=prefix,
            system=default_system,
            options=merged_options,
            stream=False,
        )
        response_text = getattr(response, "response", "") or ""
        logger.info(
            "Ollama completion response received via prefix-only mode: model=%s chars=%d",
            model,
            len(response_text),
        )
        if _looks_like_empty_completion_meta(response_text):
            logger.info(
                "Ollama prefix-only completion looks like empty meta output: model=%s raw=%r",
                model,
                response_text,
            )
        return response_text

    def _generate_completion_via_chat(
        self, model, prefix, suffix, system, options, single_line,
        blank_line_mode=False,
    ):
        """Generate one completion through Ollama's chat endpoint."""
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": build_completion_user_prompt(
                    prefix,
                    suffix,
                    single_line=single_line,
                    blank_line_mode=blank_line_mode,
                ),
            },
        ]
        logger.info(
            "Ollama completion chat fallback request: host=%s model=%s single_line=%s prefix_chars=%d suffix_chars=%d stop=%s",
            self._host,
            model,
            single_line,
            len(prefix or ""),
            len(suffix or ""),
            options.get("stop"),
        )
        response = self._client.chat(
            model=model,
            messages=messages,
            options=options,
            stream=False,
        )
        message = getattr(response, "message", None)
        if message is None and isinstance(response, dict):
            message = response.get("message") or {}
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        return str(content or "")


class OpenAICompatibleCompletionClient:
    """Blocking OpenAI-compatible client for inline completions."""

    def __init__(self, base_url, api_key=""):
        self._base_url = str(base_url or "").rstrip("/")
        self._api_key = str(api_key or "")
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        self._client = httpx.Client(
            base_url=f"{self._base_url}/v1",
            headers=headers,
            timeout=30.0,
        )

    def generate_completion(
        self,
        model,
        prefix,
        suffix="",
        system=None,
        options=None,
        single_line=False,
    ):
        """Generate one completion through `/v1/chat/completions`."""
        merged_options = dict(options or {})
        payload = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": build_completion_system_prompt(
                        system=system,
                        blank_line_mode=_blank_line_after_complete_statement(prefix),
                    ),
                },
                {
                    "role": "user",
                    "content": build_completion_user_prompt(
                        prefix,
                        suffix,
                        single_line=single_line,
                        blank_line_mode=_blank_line_after_complete_statement(prefix),
                    ),
                },
            ],
            "stream": False,
        }
        if "temperature" in merged_options:
            payload["temperature"] = merged_options["temperature"]
        if "num_predict" in merged_options:
            payload["max_tokens"] = merged_options["num_predict"]

        stop = merged_options.get("stop")
        if not stop:
            stop = build_completion_stop_sequences(single_line=single_line)
        if stop:
            payload["stop"] = list(stop)
        logger.info(
            "OpenAI-compatible completion request: base_url=%s model=%s single_line=%s prefix_chars=%d suffix_chars=%d stop=%s",
            self._base_url,
            model,
            single_line,
            len(prefix or ""),
            len(suffix or ""),
            payload.get("stop"),
        )

        response = self._client.post("/chat/completions", json=payload)
        response.raise_for_status()
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text") or item.get("content") or ""
                    if text:
                        parts.append(str(text))
            content = "".join(parts)
        if not content:
            content = choice.get("text", "") or data.get("text", "") or ""
        logger.info(
            "OpenAI-compatible completion response received: model=%s chars=%d",
            model,
            len(str(content or "")),
        )
        return str(content or "")
