"""Unit tests for advanced completion-context helpers."""

from __future__ import annotations

from spyder_ai_assistant.utils.completion_context import (
    build_related_completion_snippets,
    extract_completion_terms,
    score_completion_candidate,
)


def test_extract_completion_terms_prefers_current_word_and_recent_identifiers():
    terms = extract_completion_terms(
        "result = helper_value + another_term\ncombined = helper_value",
        current_word="helper_value",
    )

    assert terms[0] == "helper_value"
    assert "another_term" in terms


def test_build_related_completion_snippets_selects_other_open_files():
    document_states = {
        "/tmp/current.py": type("State", (), {"text": "combined = "})(),
        "/tmp/helpers.py": type(
            "State",
            (),
            {"text": "def compute_total(values):\n    return sum(values)\n"},
        )(),
        "/tmp/unused.py": type(
            "State",
            (),
            {"text": "message = 'hello world'\n"},
        )(),
    }

    snippets = build_related_completion_snippets(
        "/tmp/current.py",
        "values = [1, 2, 3]\ncombined = ",
        "compute_total",
        document_states,
    )

    assert len(snippets) == 1
    assert snippets[0].filename == "helpers.py"
    assert "compute_total" in snippets[0].excerpt
    assert "compute_total" in snippets[0].matched_terms


def test_score_completion_candidate_prefers_relevant_shorter_candidates():
    short_score = score_completion_candidate(
        "compute_total(values)",
        current_word="compute_total",
        single_line=True,
        related_terms=("values", "compute_total"),
    )
    multiline_score = score_completion_candidate(
        "compute_total(values)\nlog(values)",
        current_word="compute_total",
        single_line=True,
        related_terms=("values", "compute_total"),
    )

    assert short_score > multiline_score
