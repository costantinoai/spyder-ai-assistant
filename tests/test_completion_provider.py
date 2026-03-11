"""Unit tests for completion-provider helper logic."""

from __future__ import annotations

from spyder_ai_assistant.completion_provider import (
    _CompletionCache,
    _CompletionCacheKey,
    _LatestOnlyCompletionQueue,
    _QueuedCompletionRequest,
    _TrackedDocumentState,
    _build_completion_path_marker,
    _build_prompt_prefix,
    _clean_completion,
    _completion_already_in_document,
    _finalize_completion_text,
    _looks_like_valid_middle_of_line_suffix,
    _looks_repetitive_completion,
    _should_allow_multiline_completion,
    _trim_suffix_overlap,
)


def _queued_request(req_id, filename="example.py"):
    target = type(
        "Target",
        (),
        {
            "filename": filename,
            "version": 1,
            "line": 1,
            "column": 1,
            "offset": 1,
            "current_word": "",
        },
    )()
    return _QueuedCompletionRequest(
        req={"file": filename},
        req_id=req_id,
        target=target,
    )


def test_latest_only_queue_replaces_debounced_and_queued_requests():
    queue = _LatestOnlyCompletionQueue()

    first = _queued_request(1)
    second = _queued_request(2)
    third = _queued_request(3)

    assert queue.replace_debounced(first) is None
    dropped = queue.replace_debounced(second)
    assert dropped.req_id == 1
    assert queue.pop_debounced().req_id == 2

    queue.start_active(10)
    assert queue.active_req_id == 10
    assert queue.replace_queued(first) is None
    dropped = queue.replace_queued(third)
    assert dropped.req_id == 1
    assert queue.pop_queued().req_id == 3


def test_latest_only_queue_clear_pending_drops_waiting_requests_only():
    queue = _LatestOnlyCompletionQueue()
    queue.replace_debounced(_queued_request(11))
    queue.replace_queued(_queued_request(12))
    queue.start_active(13)

    assert queue.clear_pending() == [11, 12]
    assert queue.active_req_id == 13
    queue.finish_active(13)
    assert queue.active_req_id is None


def test_clean_completion_strips_code_fences_and_prefix_echo():
    prefix = "def add(a, b):\n    return "
    raw_text = "```python\ndef add(a, b):\n    return a + b\n```"

    assert _clean_completion(raw_text, prefix) == "a + b"


def test_clean_completion_discards_suffix_echo():
    prefix = "result = foo(\n    value,\n)\n"
    suffix = "bar()\nprint(result)\n"

    assert _clean_completion("bar()\nprint(result)", prefix, suffix) == ""


def test_clean_completion_truncates_at_first_suffix_line():
    prefix = "value = compute()\n"
    suffix = "print(value)\nfinalize()\n"
    raw_text = "helper = transform(value)\nprint(value)\nfinalize()"

    assert _clean_completion(raw_text, prefix, suffix) == "helper = transform(value)"


def test_completion_path_marker_tracks_common_comment_styles():
    assert _build_completion_path_marker("/tmp/test.py") == "# Path: test.py"
    assert _build_completion_path_marker("/tmp/index.js") == "// Path: index.js"
    assert _build_completion_path_marker("/tmp/view.html") == "<!-- Path: view.html -->"
    assert _build_completion_path_marker("/tmp/style.css") == "/* Path: style.css */"


def test_prompt_prefix_includes_path_marker_even_without_prefix():
    assert _build_prompt_prefix("/tmp/test.py", "") == "# Path: test.py\n"
    assert _build_prompt_prefix("/tmp/test.py", "print('x')") == (
        "# Path: test.py\nprint('x')"
    )


def test_middle_of_line_suffix_allows_only_light_punctuation():
    assert _looks_like_valid_middle_of_line_suffix(")\n")
    assert _looks_like_valid_middle_of_line_suffix("   ) :")
    assert not _looks_like_valid_middle_of_line_suffix("existing_call(arg)")


def test_multiline_completion_only_allowed_in_block_like_contexts():
    assert _should_allow_multiline_completion("def compute(x):\n    ")
    assert _should_allow_multiline_completion("result = (\n    ")
    assert _should_allow_multiline_completion("value = compute(")
    assert not _should_allow_multiline_completion("answer = ")


def test_completion_already_in_document_detects_existing_suffix():
    assert _completion_already_in_document("return value", "return value\nprint(value)")
    assert not _completion_already_in_document("return other", "return value\nprint(value)")


def test_tracked_document_state_defaults_to_version_one():
    state = _TrackedDocumentState(text="print('ok')")

    assert state.version == 1


def test_trim_suffix_overlap_removes_only_the_shared_tail():
    assert _trim_suffix_overlap(", 3]", "]\n") == ", 3"
    assert _trim_suffix_overlap("value", ")\n") == "value"


def test_repetition_filter_detects_repeated_lines_and_tokens():
    assert _looks_repetitive_completion("print(value)\nprint(value)\nprint(value)")
    assert _looks_repetitive_completion("value value value value")
    assert not _looks_repetitive_completion("value + other_value")


def test_finalize_completion_text_reports_overlap_and_repetition():
    assert _finalize_completion_text(", 3]", "]\n") == (", 3", None)
    assert _finalize_completion_text("value value value value", "") == (
        "",
        "repetition",
    )
    assert _finalize_completion_text("helper()", "helper()\nprint('x')") == (
        "",
        "duplicate",
    )


def test_completion_cache_is_lru_and_supports_empty_entries():
    cache = _CompletionCache(max_entries=2)
    first = _CompletionCacheKey(
        host="http://localhost:11434",
        model="model-a",
        temperature=0.1,
        num_predict=64,
        single_line=True,
        prompt_prefix="alpha",
        suffix="",
    )
    second = _CompletionCacheKey(
        host="http://localhost:11434",
        model="model-a",
        temperature=0.1,
        num_predict=64,
        single_line=True,
        prompt_prefix="beta",
        suffix="",
    )
    third = _CompletionCacheKey(
        host="http://localhost:11434",
        model="model-a",
        temperature=0.1,
        num_predict=64,
        single_line=True,
        prompt_prefix="gamma",
        suffix="",
    )

    cache.put(first, {"text": "", "filter_reason": "duplicate"})
    cache.put(second, {"text": "value", "filter_reason": None})
    assert cache.get(first) == {"text": "", "filter_reason": "duplicate"}
    cache.put(third, {"text": "other", "filter_reason": None})

    assert cache.get(first) == {"text": "", "filter_reason": "duplicate"}
    assert cache.get(second) is _CompletionCache._MISSING
    assert cache.get(third) == {"text": "other", "filter_reason": None}
