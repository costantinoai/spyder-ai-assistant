"""Unit tests for runtime-context summarization helpers."""

from __future__ import annotations

import pytest

from spyder_ai_assistant.utils.runtime_context import (
    _extract_latest_error_lines,
    _normalize_console_text,
    _normalize_dtype,
    build_runtime_variable_summaries,
    format_runtime_variable,
    format_runtime_shell,
    summarize_runtime_value,
    summarize_traceback_text,
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
        "dtypes": "",
        "range": "",
        "channels": "",
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


def test_format_runtime_variable_renders_phase10_fields():
    variable = {
        "name": "df",
        "kind": "dataframe",
        "type": "DataFrame",
        "shape": "(2, 2)",
        "columns": "a, b",
        "dtypes": "a:int64, b:int64",
        "preview": "[{'a': 1, 'b': 2}]",
    }

    assert format_runtime_variable(variable) == (
        "df [dataframe]; type=DataFrame; shape=(2, 2); columns=a, b; "
        "dtypes=a:int64, b:int64; preview=[{'a': 1, 'b': 2}]"
    )


def test_format_runtime_shell_renders_flags_and_status():
    record = {
        "shell_id": "0x1",
        "label": "Console 1/A",
        "status": "ready",
        "working_directory": "/tmp/project",
        "is_active": True,
        "is_target": True,
        "has_error": False,
    }

    assert format_runtime_shell(record) == (
        "Console 1/A; id=0x1; status=ready; cwd=/tmp/project; flags=active,target"
    )


def test_summarize_traceback_text_extracts_exception_and_frames():
    summary = summarize_traceback_text(
        "Traceback (most recent call last):\n"
        "  File \"/tmp/test.py\", line 2, in <module>\n"
        "    run()\n"
        "  File \"/tmp/test.py\", line 5, in run\n"
        "    1 / 0\n"
        "ZeroDivisionError: division by zero"
    )

    assert summary["exception_type"] == "ZeroDivisionError"
    assert summary["exception_message"] == "division by zero"
    assert summary["frame_count"] == 2
    assert summary["frames"][-1]["function"] == "run"
    assert summary["frames"][-1]["code"] == "1 / 0"


def test_summarize_traceback_text_extracts_ipython_cell_frames():
    summary = summarize_traceback_text(
        "---------------------------------------------------------------------------\n"
        "ZeroDivisionError                         Traceback (most recent call last)\n"
        "Cell In[6], line 1\n"
        "----> 1 1/0\n"
        "\n"
        "ZeroDivisionError: division by zero"
    )

    assert summary["exception_type"] == "ZeroDivisionError"
    assert summary["exception_message"] == "division by zero"
    assert summary["frame_count"] == 1
    assert summary["frames"][0]["file"] == "Cell In[6]"
    assert summary["frames"][0]["line"] == 1
    assert summary["frames"][0]["function"] == "In[6]"
    assert summary["frames"][0]["code"] == "1/0"


def test_summarize_runtime_value_handles_plain_python_collections():
    summary = summarize_runtime_value({"a": 1, "b": 2, "c": 3}, kind="dict")

    assert summary["kind"] == "dict"
    assert summary["length"] == 3
    assert "preview" in summary


def test_summarize_runtime_value_handles_numpy_arrays():
    np = pytest.importorskip("numpy")
    value = np.arange(6, dtype=np.int64).reshape(2, 3)

    summary = summarize_runtime_value(value, kind="array", fallback_type="ndarray")

    assert summary["kind"] == "array"
    assert summary["shape"] == "(2, 3)"
    assert summary["dtype"] == "int64"
    assert summary["range"] == "0..5"
    assert summary["preview"] == "[0, 1, 2, 3, 4, 5]"


def test_summarize_runtime_value_handles_sequence_arrays():
    value = [[0, 1, 2], [3, 4, 5]]

    summary = summarize_runtime_value(
        value,
        kind="array",
        fallback_type="Array of int64",
    )

    assert summary["kind"] == "array"
    assert summary["type"] == "Array of int64"
    assert summary["shape"] == "(2, 3)"
    assert summary["dtype"] == "int64"
    assert summary["range"] == "0..5"
    assert summary["preview"] == "[0, 1, 2, 3, 4, 5]"


def test_summarize_runtime_value_handles_pandas_dataframes():
    pd = pytest.importorskip("pandas")
    value = pd.DataFrame({"a": [1, 2], "b": [3, 4]})

    summary = summarize_runtime_value(
        value,
        kind="dataframe",
        fallback_type="DataFrame",
    )

    assert summary["kind"] == "dataframe"
    assert summary["shape"] == "(2, 2)"
    assert summary["columns"] == "a, b"
    assert "a:int64" in summary["dtypes"]
    assert "{'a': 1, 'b': 3}" in summary["preview"]


def test_normalize_dtype_drops_unknown_markers():
    assert _normalize_dtype("") == ""
    assert _normalize_dtype("Unknown") == ""
    assert _normalize_dtype("float64") == "float64"
