"""Unit tests for per-chat inference helpers."""

from __future__ import annotations

from spyder_ai_assistant.utils.chat_inference import (
    describe_chat_inference_source,
    format_chat_temperature,
    make_chat_inference_record,
    normalize_chat_max_tokens,
    normalize_chat_temperature,
    resolve_chat_inference_options,
)


def test_normalize_chat_temperature_accepts_legacy_x10_values():
    assert normalize_chat_temperature(5) == 0.5
    assert normalize_chat_temperature(0.7) == 0.7
    assert normalize_chat_temperature("bad") == 0.5


def test_normalize_chat_max_tokens_clamps_to_supported_range():
    assert normalize_chat_max_tokens(32) == 64
    assert normalize_chat_max_tokens(9000) == 8192
    assert normalize_chat_max_tokens("bad") == 1024


def test_make_chat_inference_record_normalizes_optional_overrides():
    record = make_chat_inference_record(
        temperature_override=7,
        max_tokens_override=9000,
    )

    assert record == {
        "temperature_override": 0.7,
        "max_tokens_override": 8192,
    }


def test_resolve_chat_inference_options_prefers_tab_overrides():
    metadata = resolve_chat_inference_options(
        default_temperature=5,
        default_max_tokens=1024,
        temperature_override=0.2,
        max_tokens_override=256,
    )

    assert metadata["temperature"] == 0.2
    assert metadata["num_predict"] == 256
    assert metadata["temperature_source"] == "override"
    assert metadata["num_predict_source"] == "override"
    assert metadata["default_temperature"] == 0.5
    assert metadata["default_num_predict"] == 1024


def test_resolve_chat_inference_options_falls_back_to_defaults():
    metadata = resolve_chat_inference_options(
        default_temperature=0.4,
        default_max_tokens=1536,
    )

    assert metadata["temperature"] == 0.4
    assert metadata["num_predict"] == 1536
    assert metadata["temperature_source"] == "default"
    assert metadata["num_predict_source"] == "default"
    assert metadata["temperature_override"] is None
    assert metadata["max_tokens_override"] is None


def test_chat_inference_format_helpers_are_user_facing():
    assert describe_chat_inference_source("override") == "tab override"
    assert describe_chat_inference_source("default") == "global default"
    assert format_chat_temperature(0.5) == "0.5"
