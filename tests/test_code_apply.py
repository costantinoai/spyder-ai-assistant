"""Unit tests for chat code-apply preview helpers."""

from __future__ import annotations

from spyder_ai_assistant.utils.code_apply import (
    APPLY_MODE_INSERT,
    APPLY_MODE_REPLACE,
    build_code_apply_plan,
    normalize_apply_mode,
)


def test_normalize_apply_mode_accepts_known_modes():
    assert normalize_apply_mode("insert") == APPLY_MODE_INSERT
    assert normalize_apply_mode("replace") == APPLY_MODE_REPLACE
    assert normalize_apply_mode("bad") == APPLY_MODE_INSERT


def test_build_code_apply_plan_inserts_at_cursor():
    plan = build_code_apply_plan(
        document_text="alpha = 1\nbeta = 2\n",
        code="gamma = 3\n",
        cursor_position=len("alpha = 1\nbeta = 2\n"),
        requested_mode=APPLY_MODE_INSERT,
    )

    assert plan["effective_mode"] == APPLY_MODE_INSERT
    assert plan["updated_text"] == "alpha = 1\nbeta = 2\ngamma = 3\n"
    assert "Apply the code by inserting" in plan["note"]
    assert "+gamma = 3" in plan["diff_text"]


def test_build_code_apply_plan_replaces_selection_when_available():
    document_text = "alpha = 1\nbeta = 2\n"
    start = document_text.index("beta = 2")
    end = start + len("beta = 2")

    plan = build_code_apply_plan(
        document_text=document_text,
        code="beta = 99",
        cursor_position=end,
        selection_start=start,
        selection_end=end,
        requested_mode=APPLY_MODE_REPLACE,
    )

    assert plan["effective_mode"] == APPLY_MODE_REPLACE
    assert plan["has_selection"] is True
    assert plan["selection_text"] == "beta = 2"
    assert plan["updated_text"] == "alpha = 1\nbeta = 99\n"
    assert "-beta = 2" in plan["diff_text"]
    assert "+beta = 99" in plan["diff_text"]


def test_build_code_apply_plan_replace_falls_back_without_selection():
    plan = build_code_apply_plan(
        document_text="alpha = 1\n",
        code="beta = 2\n",
        cursor_position=len("alpha = 1\n"),
        requested_mode=APPLY_MODE_REPLACE,
    )

    assert plan["effective_mode"] == APPLY_MODE_INSERT
    assert "falls back to inserting" in plan["note"]
    assert plan["updated_text"] == "alpha = 1\nbeta = 2\n"
