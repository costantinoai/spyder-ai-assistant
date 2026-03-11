"""Unit tests for chat workflow helpers."""

from __future__ import annotations

from spyder_ai_assistant.utils.chat_workflows import (
    build_debug_prompt,
    build_export_markdown,
)


def test_build_debug_prompt_includes_context_and_user_text():
    prompt = build_debug_prompt(
        "fix_traceback",
        user_text="This fails after loading the CSV.",
        context_label="analysis.py",
    )

    assert "Inspect the latest traceback" in prompt
    assert "Current editor context: analysis.py." in prompt
    assert "User request:\nThis fails after loading the CSV." in prompt


def test_build_debug_prompt_uses_fallback_for_unknown_action():
    prompt = build_debug_prompt("unknown-action")

    assert prompt == "Inspect the relevant live Spyder runtime state before answering."


def test_build_export_markdown_includes_metadata_and_messages():
    markdown = build_export_markdown(
        messages=[
            {"role": "user", "content": "Help me debug this."},
            {"role": "assistant", "content": "Try checking the latest traceback."},
        ],
        model_name="qwen-test",
        prompt_preset_label="Documentation",
        inference_metadata={
            "temperature": 0.2,
            "temperature_source": "override",
            "num_predict": 256,
            "num_predict_source": "override",
        },
        context_label="main.py",
        runtime_context={
            "status": "ready",
            "status_detail": "Kernel ready.",
            "working_directory": "/tmp/project",
            "last_refreshed_at": "2026-03-11T12:00:00",
            "variables": [{"name": "df"}],
            "latest_error": "Traceback...",
        },
    )

    assert "# AI Chat Export" in markdown
    assert "**Model:** qwen-test" in markdown
    assert "**Chat mode:** Documentation" in markdown
    assert "**Temperature:** 0.2 (tab override)" in markdown
    assert "**Max tokens:** 256 (tab override)" in markdown
    assert "**Editor context:** main.py" in markdown
    assert "**Runtime status:** ready" in markdown
    assert "**Runtime variables tracked:** 1" in markdown
    assert "## You\n\nHelp me debug this." in markdown
    assert "## AI\n\nTry checking the latest traceback." in markdown
