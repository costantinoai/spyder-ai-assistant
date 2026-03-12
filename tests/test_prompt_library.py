"""Unit tests for built-in chat prompt presets."""

from __future__ import annotations

from spyder_ai_assistant.utils.prompt_library import (
    DEFAULT_CHAT_PROMPT_PRESET,
    build_chat_prompt_preset_block,
    get_chat_prompt_preset,
    list_chat_prompt_presets,
    normalize_chat_prompt_preset,
)


def test_list_chat_prompt_presets_returns_expected_ids():
    assert [preset["id"] for preset in list_chat_prompt_presets()] == [
        "coding",
        "debugging",
        "review",
        "analysis",
        "explanation",
        "documentation",
    ]


def test_normalize_chat_prompt_preset_falls_back_to_default():
    assert normalize_chat_prompt_preset("not-real") == DEFAULT_CHAT_PROMPT_PRESET
    assert normalize_chat_prompt_preset(None) == DEFAULT_CHAT_PROMPT_PRESET


def test_get_chat_prompt_preset_returns_normalized_payload():
    preset = get_chat_prompt_preset("DEBUGGING")

    assert preset["id"] == "debugging"
    assert preset["label"] == "Debugging"
    assert "root-cause" in preset["instructions"]


def test_build_chat_prompt_preset_block_contains_mode_label():
    block = build_chat_prompt_preset_block("documentation")

    assert "Active chat mode: Documentation." in block


def test_review_prompt_preset_mentions_findings():
    preset = get_chat_prompt_preset("review")

    assert preset["label"] == "Review"
    assert "regressions" in preset["instructions"]


def test_analysis_prompt_preset_mentions_runtime_subset():
    preset = get_chat_prompt_preset("analysis")

    assert preset["label"] == "Data Analysis"
    assert "smallest useful live subset" in preset["instructions"]
