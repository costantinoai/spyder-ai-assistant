"""Helpers for per-chat inference settings and resolved request options."""

from __future__ import annotations


DEFAULT_CHAT_TEMPERATURE = 0.5
DEFAULT_CHAT_MAX_TOKENS = 1024
MIN_CHAT_TEMPERATURE = 0.0
MAX_CHAT_TEMPERATURE = 2.0
MIN_CHAT_MAX_TOKENS = 64
MAX_CHAT_MAX_TOKENS = 8192


def normalize_chat_temperature(value):
    """Normalize stored chat temperature values to Ollama's expected range.

    The global preferences historically exposed ``temperature x10`` integers,
    so values greater than ``2.0`` are treated as legacy x10 inputs.
    """
    if not isinstance(value, (int, float)):
        return DEFAULT_CHAT_TEMPERATURE

    normalized = float(value)
    if normalized > MAX_CHAT_TEMPERATURE:
        normalized = normalized / 10.0
    normalized = max(MIN_CHAT_TEMPERATURE, min(MAX_CHAT_TEMPERATURE, normalized))
    return round(normalized, 2)


def normalize_chat_temperature_override(value):
    """Return one normalized per-tab temperature override or ``None``."""
    if value in (None, ""):
        return None
    if not isinstance(value, (int, float)):
        return None
    return normalize_chat_temperature(value)


def normalize_chat_max_tokens(value):
    """Return one clamped max-token value for chat responses."""
    if not isinstance(value, (int, float)):
        return DEFAULT_CHAT_MAX_TOKENS
    normalized = int(value)
    normalized = max(MIN_CHAT_MAX_TOKENS, min(MAX_CHAT_MAX_TOKENS, normalized))
    return normalized


def normalize_chat_max_tokens_override(value):
    """Return one normalized per-tab max-token override or ``None``."""
    if value in (None, ""):
        return None
    if not isinstance(value, (int, float)):
        return None
    return normalize_chat_max_tokens(value)


def make_chat_inference_record(temperature_override=None, max_tokens_override=None):
    """Return one normalized persisted inference-settings payload."""
    return {
        "temperature_override": normalize_chat_temperature_override(
            temperature_override
        ),
        "max_tokens_override": normalize_chat_max_tokens_override(
            max_tokens_override
        ),
    }


def resolve_chat_inference_options(default_temperature, default_max_tokens,
                                   temperature_override=None,
                                   max_tokens_override=None):
    """Resolve global defaults plus optional per-tab overrides."""
    defaults = {
        "temperature": normalize_chat_temperature(default_temperature),
        "num_predict": normalize_chat_max_tokens(default_max_tokens),
    }
    overrides = make_chat_inference_record(
        temperature_override=temperature_override,
        max_tokens_override=max_tokens_override,
    )
    temperature = overrides["temperature_override"]
    num_predict = overrides["max_tokens_override"]

    return {
        "temperature": (
            temperature if temperature is not None else defaults["temperature"]
        ),
        "num_predict": (
            num_predict if num_predict is not None else defaults["num_predict"]
        ),
        "temperature_source": (
            "override" if temperature is not None else "default"
        ),
        "num_predict_source": (
            "override" if num_predict is not None else "default"
        ),
        "temperature_override": temperature,
        "max_tokens_override": num_predict,
        "default_temperature": defaults["temperature"],
        "default_num_predict": defaults["num_predict"],
    }


def describe_chat_inference_source(source):
    """Return a small human-readable source label for one option."""
    return "tab override" if source == "override" else "global default"


def format_chat_temperature(value):
    """Format one normalized temperature for UI and export metadata."""
    if not isinstance(value, (int, float)):
        return f"{DEFAULT_CHAT_TEMPERATURE:g}"
    return f"{float(value):g}"
