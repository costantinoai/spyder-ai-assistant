"""Unit tests for the runtime bridge protocol."""

from __future__ import annotations

from spyder_ai_assistant.utils.runtime_bridge import (
    RUNTIME_REQUEST_TAG,
    build_runtime_bridge_instructions,
    format_runtime_observation,
    parse_runtime_request,
)


def test_runtime_bridge_instructions_advertise_protocol_and_tools():
    instructions = build_runtime_bridge_instructions()

    assert "Live Spyder runtime access is available when needed." in instructions
    assert RUNTIME_REQUEST_TAG in instructions
    assert "runtime.list_shells" in instructions
    assert "runtime.inspect_variable" in instructions
    assert "runtime.get_latest_error" in instructions


def test_parse_runtime_request_returns_none_for_normal_text():
    assert parse_runtime_request("Normal assistant answer.") is None


def test_parse_runtime_request_accepts_valid_block():
    parsed = parse_runtime_request(
        "<spyder-runtime-request>\n"
        '{"tool":"runtime.inspect_variable","args":{"name":"df"}}\n'
        "</spyder-runtime-request>"
    )

    assert parsed["valid"] is True
    assert parsed["tool"] == "runtime.inspect_variable"
    assert parsed["args"] == {"name": "df"}


def test_parse_runtime_request_rejects_extra_text():
    parsed = parse_runtime_request(
        "Before\n<spyder-runtime-request>{\"tool\":\"runtime.status\"}</spyder-runtime-request>"
    )

    assert parsed["valid"] is False
    assert "only content" in parsed["error"]


def test_parse_runtime_request_rejects_unknown_tool():
    parsed = parse_runtime_request(
        "<spyder-runtime-request>\n"
        '{"tool":"runtime.delete_everything","args":{}}\n'
        "</spyder-runtime-request>"
    )

    assert parsed["valid"] is False
    assert "Unsupported runtime tool" in parsed["error"]


def test_parse_runtime_request_rejects_non_mapping_args():
    parsed = parse_runtime_request(
        "<spyder-runtime-request>\n"
        '{"tool":"runtime.status","args":["bad"]}\n'
        "</spyder-runtime-request>"
    )

    assert parsed["valid"] is False
    assert "JSON object" in parsed["error"]


def test_format_runtime_observation_renders_payload_and_metadata():
    request = {"tool": "runtime.inspect_variable", "args": {"name": "df"}}
    result = {
        "ok": True,
        "tool": "runtime.inspect_variable",
        "source": "live",
        "shell_status": "ready",
        "shell_label": "Console 2/A",
        "shell_id": "0xabc",
        "active_shell_label": "Console 1/A",
        "target_shell_label": "Console 2/A",
        "shell_detail": "Kernel ready.",
        "working_directory": "/tmp/project",
        "last_refreshed_at": "2026-03-11T12:00:00",
        "payload": {
            "variables": [
                {
                    "name": "df",
                    "kind": "dataframe",
                    "type": "DataFrame",
                    "shape": "(3, 2)",
                    "columns": "a, b",
                }
            ]
        },
    }

    observation = format_runtime_observation(request, result)

    assert "[Spyder Runtime Observation]" in observation
    assert "tool: runtime.inspect_variable" in observation
    assert "source: live" in observation
    assert "shell: Console 2/A" in observation
    assert "shell id: 0xabc" in observation
    assert "df [dataframe]; type=DataFrame; shape=(3, 2); columns=a, b" in observation
    assert observation.rstrip().endswith("Otherwise answer normally.")


def test_format_runtime_observation_renders_shell_listing():
    request = {"tool": "runtime.list_shells", "args": {}}
    result = {
        "ok": True,
        "tool": "runtime.list_shells",
        "payload": {
            "count": 2,
            "shells": [
                {
                    "shell_id": "0x1",
                    "label": "Console 1/A",
                    "status": "ready",
                    "working_directory": "/tmp/a",
                    "is_active": True,
                    "is_target": False,
                    "has_error": False,
                },
                {
                    "shell_id": "0x2",
                    "label": "Console 2/A",
                    "status": "busy",
                    "working_directory": "/tmp/b",
                    "is_active": False,
                    "is_target": True,
                    "has_error": True,
                },
            ],
        },
    }

    observation = format_runtime_observation(request, result)

    assert "count: 2" in observation
    assert "Console 1/A; id=0x1; status=ready; cwd=/tmp/a; flags=active" in observation
    assert (
        "Console 2/A; id=0x2; status=busy; cwd=/tmp/b; flags=target,error"
        in observation
    )


def test_format_runtime_observation_renders_missing_error_data():
    request = {"tool": "runtime.get_latest_error", "args": {}}
    result = {
        "ok": False,
        "tool": "runtime.get_latest_error",
        "error": "No active shell",
        "payload": {},
    }

    observation = format_runtime_observation(request, result)

    assert "ok: false" in observation
    assert "error: No active shell" in observation
    assert "No latest error is available." in observation
