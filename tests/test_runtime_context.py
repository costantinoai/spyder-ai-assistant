"""Unit tests for runtime-context summarization helpers."""

from __future__ import annotations

from spyder_ai_assistant.utils.runtime_context import (
    _extract_latest_error_lines,
    _normalize_console_text,
    _normalize_dtype,
    build_runtime_variable_summaries,
    format_runtime_variable,
    summarize_console_text,
)


def test_normalize_console_text_strips_ansi_and_crlf():
    text = "\u001b[31mError:\u001b[0m bad\r\nsecond line\r"

    assert _normalize_console_text(text) == "Error: bad\nsecond line"


def test_extract_latest_error_lines_prefers_last_traceback_block():
    lines = [
        "In [1]: do_something()",
        "Traceback (most recent call last):",
        "  File \"<stdin>\", line 1, in <module>",
        "ValueError: first",
        "In [2]: do_other()",
        "Traceback (most recent call last):",
        "  File \"<stdin>\", line 2, in <module>",
        "TypeError: second",
        "In [3]:",
    ]

    assert _extract_latest_error_lines(lines) == [
        "Traceback (most recent call last):",
        "  File \"<stdin>\", line 2, in <module>",
        "TypeError: second",
    ]


def test_summarize_console_text_splits_latest_error_from_recent_output():
    console_text = "\n".join(
        [
            "In [1]: print('ok')",
            "ok",
            "In [2]: fail()",
            "Traceback (most recent call last):",
            "  File \"<stdin>\", line 1, in <module>",
            "ValueError: boom",
            "In [3]: print('after')",
            "after",
        ]
    )

    summary = summarize_console_text(console_text)

    assert summary["latest_error"] == (
        "Traceback (most recent call last):\n"
        "  File \"<stdin>\", line 1, in <module>\n"
        "ValueError: boom"
    )
    assert "ValueError: boom" not in summary["console_output"]
    assert "print('after')" in summary["console_output"]


def test_build_runtime_variable_summaries_formats_common_types():
    namespace_view = {
        "df": {
            "type": "DataFrame",
            "python_type": "DataFrame",
            "size": "3x2",
            "view": "Column names: a, b",
            "numpy_type": "Unknown",
        },
        "value": {
            "type": "int",
            "python_type": "int",
            "size": 1,
            "view": "42",
            "numpy_type": "int64",
        },
    }
    var_properties = {
        "df": {"is_data_frame": True, "len": 3, "array_shape": "(3, 2)"},
        "value": {},
    }

    summaries = build_runtime_variable_summaries(namespace_view, var_properties)

    assert summaries[0] == {
        "name": "df",
        "kind": "dataframe",
        "type": "DataFrame",
        "size": "3x2",
        "length": 3,
        "shape": "(3, 2)",
        "ndim": None,
        "dtype": "",
        "preview": "",
        "columns": "a, b",
    }
    assert summaries[1]["kind"] == "scalar"
    assert summaries[1]["dtype"] == "int64"
    assert summaries[1]["preview"] == "42"


def test_format_runtime_variable_prefers_shape_then_dtype_and_preview():
    variable = {
        "name": "arr",
        "kind": "array",
        "type": "ndarray",
        "shape": "(10, 3)",
        "dtype": "float32",
        "preview": "[1.0, 2.0, 3.0]",
    }

    assert format_runtime_variable(variable) == (
        "arr [array]; type=ndarray; shape=(10, 3); dtype=float32; preview=[1.0, 2.0, 3.0]"
    )


def test_normalize_dtype_drops_unknown_markers():
    assert _normalize_dtype("") == ""
    assert _normalize_dtype("Unknown") == ""
    assert _normalize_dtype("float64") == "float64"
