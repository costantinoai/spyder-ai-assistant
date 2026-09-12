"""One place that turns a provider failure into something actionable.

Three layers used to format the same failures independently: the chat
worker mapped exceptions for the transcript, the provider registry put
``str(error)`` into the diagnostics that the status tooltip renders, and
the profile probe built its own ``Type: message`` string. The same outage
therefore read three different ways, and two of those paths put a raw
exception in front of the user.

Every message here says what failed *and* what to do about it, because
"Cannot connect to Ollama" leaves the reader to guess that the answer is
``ollama serve``. Raw exception text never reaches a user-facing string:
the exception type is kept as a handle and the detail stays in the log,
which each call site already writes.

Classification is by exception type and HTTP status, never by searching
the message text: a substring like "refused" is a property of one
library's wording, not of the failure.
"""

from __future__ import annotations

from spyder_ai_assistant.utils.provider_profiles import PROVIDER_KIND_OLLAMA


def format_provider_problem(provider_label, message):
    """Return one line naming a provider problem, without stuttering.

    Every message from ``describe_provider_failure`` already opens with the
    provider label, so prefixing the label again produced "Work GPU: Work
    GPU is not reachable at ..." on screen. The label is added only when
    the message does not already lead with it, which is the case for
    messages that come from elsewhere (a provider's own diagnostic text,
    for instance).
    """
    label = str(provider_label or "").strip() or "Provider"
    text = str(message or "").strip()
    if not text:
        return f"{label} is unavailable."
    if text.startswith(label):
        return text
    return f"{label}: {text}"


def _status_code(error):
    """Return the HTTP status code carried by ``error``, if any.

    Reads both shapes without importing either library: ``httpx``
    exceptions carry ``response.status_code`` and ollama's ``ResponseError``
    carries ``status_code`` directly.
    """
    response = getattr(error, "response", None)
    code = getattr(response, "status_code", None)
    if code is None:
        code = getattr(error, "status_code", None)
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def _is_connection_failure(error):
    """Return whether ``error`` means the endpoint could not be reached."""
    if isinstance(error, (ConnectionError, TimeoutError)):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is a hard dependency
        return False
    return isinstance(
        error,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.TimeoutException,
        ),
    )


def describe_provider_failure(
    error,
    *,
    provider_label="The provider",
    endpoint="",
    provider_kind="",
    model="",
):
    """Return one user-facing sentence for a provider failure.

    Args:
        error: The exception raised by the provider call.
        provider_label: Name to show the user, e.g. "Ollama" or a profile
            label.
        endpoint: The endpoint that was contacted, if known.
        provider_kind: Used to pick the remedy; a local Ollama that is not
            running needs different advice from a remote endpoint.
        model: The requested model, used for the "not installed" remedy.

    Returns:
        A sentence naming the failure and the next step. Never contains the
        raw exception message.
    """
    where = f" at {endpoint}" if endpoint else ""
    is_ollama = provider_kind == PROVIDER_KIND_OLLAMA

    if _is_connection_failure(error):
        if is_ollama:
            return (
                f"Ollama is not reachable{where}. Start it with "
                "`ollama serve`, then try again."
            )
        return (
            f"{provider_label} is not reachable{where}. Check that the "
            "endpoint is correct and the service is running."
        )

    code = _status_code(error)
    if code == 404:
        if is_ollama:
            if model:
                return (
                    f"The model '{model}' is not installed. Install it with "
                    f"`ollama pull {model}`."
                )
            return (
                "Ollama does not have the requested model. Install it with "
                "`ollama pull <model>`."
            )
        return (
            f"{provider_label} has no endpoint{where}. Check the Base URL; "
            "the request path is added for you."
        )
    if code in (401, 403):
        return (
            f"{provider_label} rejected the credentials. Check the API key "
            "configured for it."
        )
    if code == 429:
        return (
            f"{provider_label} is rate limiting requests. Wait a moment and "
            "try again."
        )
    if code is not None and code >= 500:
        return (
            f"{provider_label} returned a server error (HTTP {code}). The "
            "endpoint is reachable but failing; try again shortly."
        )
    if code is not None:
        return (
            f"{provider_label} refused the request (HTTP {code}). Check the "
            "configured endpoint and model."
        )

    # Unknown failure. The type is a useful handle for a bug report; the
    # message is not, and could be anything at all. The endpoint is kept
    # because it is the one detail that still helps the reader place the
    # failure -- an earlier version dropped it here, which made an
    # unclassified error say nothing about where it happened.
    return (
        f"{provider_label} failed unexpectedly{where} "
        f"({type(error).__name__}). See the plugin log for details."
    )
