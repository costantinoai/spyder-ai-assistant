"""Thin, stateless wrapper around the ollama Python package.

This module provides the OllamaClient class, which handles all direct
communication with the Ollama server. It has no Qt dependencies and can
be used independently of the Spyder plugin infrastructure.

All methods are synchronous (blocking) and should only be called from
a background thread — never from the Qt main thread.
"""

import logging

from ollama import Client

logger = logging.getLogger(__name__)


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

        default_system = system or (
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
        merged_options = dict(options or {})

        # Add stop sequences to prevent the model from generating too much.
        # Stop on patterns that indicate a new top-level definition or
        # excessive blank lines (signals the function/block is complete).
        if "stop" not in merged_options:
            if single_line:
                merged_options["stop"] = ["\n"]
            else:
                merged_options["stop"] = [
                    "\n\n\n",      # Triple newline = end of logical block
                    "\nclass ",    # New class definition
                    "\ndef ",      # New top-level function
                    "\n# %%",      # Spyder cell separator
                ]

        # Try FIM (with suffix) first, unless we already know this model
        # doesn't support it. Fall back to prefix-only on "does not support
        # insert" errors.
        use_suffix = suffix and model not in self._fim_unsupported_models

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

        # Prefix-only completion (no suffix/FIM)
        response = self._client.generate(
            model=model,
            prompt=prefix,
            system=default_system,
            options=merged_options,
            stream=False,
        )
        return response.response
