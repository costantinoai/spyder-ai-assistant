"""Live Phase 10 validation in a real Spyder session."""

from __future__ import annotations

import traceback

from tools.spyder_validation.common import (
    DEFAULT_CHAT_MODEL,
    artifact_path,
    create_console_client,
    finalize,
    get_chat_widget,
    get_runtime_service,
    record_validation_result,
    run_spyder_validation,
    select_model,
    select_runtime_target,
    send_prompt,
    set_current_shell,
    wait_for,
    wait_for_runtime_shell_targets,
    write_json,
)


CONFIG_DIR = artifact_path("configs", "phase10-runtime-validation")
RESULT_PATH = artifact_path("results", "phase10-runtime-validation.json")
CHAT_MODEL = DEFAULT_CHAT_MODEL


def _execute(shellwidget, code):
    """Execute one snippet in a specific shellwidget."""
    shellwidget.execute(code)


def _execute_and_wait(shellwidget, code, timeout_ms=15000):
    """Execute one snippet and wait for the shell to leave the busy state."""
    shellwidget.execute(code)
    return wait_for(
        lambda: not bool(getattr(shellwidget, "_executing", False)),
        timeout_ms=timeout_ms,
        step_ms=100,
    )


def _wait_for_runtime_result(service, request, predicate, timeout_ms=20000):
    """Poll one runtime request until its payload matches the predicate."""
    last_result = {}

    def _check():
        nonlocal last_result
        last_result = service.execute_request(request)
        if predicate(last_result):
            return last_result
        return None

    return wait_for(
        _check,
        timeout_ms=timeout_ms,
        step_ms=150,
    ) or last_result


def run_validation(window):
    """Run the live Phase 10 runtime validation."""
    results = {
        "phase": "10",
        "branch_expectation": "feat/deeper-kernel-and-terminal-integration",
        "errors": [],
    }

    try:
        print("[phase10] selecting chat model")
        widget = get_chat_widget(window)
        service = get_runtime_service(window)
        select_model(widget, CHAT_MODEL)

        print("[phase10] waiting for initial runtime target")
        if not wait_for_runtime_shell_targets(widget, expected_count=1):
            raise RuntimeError("Runtime target selector did not populate")

        first_shell = service._safe_get_current_shellwidget()
        if first_shell is None:
            raise RuntimeError("No initial shellwidget is available")
        first_shell_id = hex(id(first_shell))

        print("[phase10] creating a second console")
        second_client = create_console_client(window, give_focus=False)
        second_shell = getattr(second_client, "shellwidget", None)
        if second_shell is None:
            second_shell = getattr(second_client, "_shellwidget", None)
        if second_shell is None:
            raise RuntimeError("New console client did not expose a shellwidget")
        if not wait_for(
                lambda: getattr(second_shell, "spyder_kernel_ready", False),
                timeout_ms=30000,
                step_ms=100):
            raise RuntimeError("Second console kernel did not become ready")

        if not wait_for_runtime_shell_targets(widget, expected_count=2):
            raise RuntimeError("Second console did not appear in the runtime selector")
        second_shell_id = next(
            (
                record.get("shell_id", "")
                for record in widget._runtime_shells
                if record.get("shell_id") != first_shell_id
            ),
            "",
        )
        if not second_shell_id:
            raise RuntimeError("Could not resolve the second console shell id")

        print("[phase10] seeding variables and errors in both consoles")
        _execute(first_shell, "origin = 'first-shell'\n")
        set_current_shell(window, second_shell)
        for statement in (
                "import numpy as np\n",
                "import pandas as pd\n",
                "arr = np.arange(6, dtype=np.int64).reshape(2, 3)\n",
                "df = pd.DataFrame({'a': [1, 2], 'b': [3, 4]})\n",
                "label = 'second-shell'\n"):
            if not _execute_and_wait(second_shell, statement):
                raise RuntimeError(f"Timed out executing statement: {statement!r}")

        label_result = _wait_for_runtime_result(
            service,
            {
                "tool": "runtime.inspect_variable",
                "args": {"name": "label", "shell_id": second_shell_id},
            },
            lambda result: (
                result.get("ok")
                and bool(result.get("payload", {}).get("variables"))
            ),
        )
        if not label_result.get("ok"):
            raise RuntimeError(
                f"Second console did not keep seeded variables: {label_result}"
            )

        if not _execute_and_wait(second_shell, "1/0\n"):
            raise RuntimeError("Timed out raising the test traceback on the second console")
        second_status = _wait_for_runtime_result(
            service,
            {
                "tool": "runtime.status",
                "args": {"shell_id": second_shell_id},
            },
            lambda result: result.get("shell_status") in {"ready", "busy"},
        )
        if second_status.get("shell_status") not in {"ready", "busy"}:
            raise RuntimeError(
                f"Second console did not reach a usable runtime state: {second_status}"
            )

        print("[phase10] waiting for richer variable inspection on the second console")
        df_result = _wait_for_runtime_result(
            service,
            {
                "tool": "runtime.inspect_variable",
                "args": {"name": "df", "shell_id": second_shell_id},
            },
            lambda result: (
                result.get("ok")
                and bool(result.get("payload", {}).get("variables"))
            ),
        )
        if not df_result.get("ok"):
            raise RuntimeError(
                f"Timed out waiting for live DataFrame inspection: {df_result}"
            )
        print(
            "[phase10] DataFrame inspection:",
            df_result["payload"]["variables"][0].get("columns", ""),
            df_result["payload"]["variables"][0].get("preview", ""),
        )

        arr_result = _wait_for_runtime_result(
            service,
            {
                "tool": "runtime.inspect_variable",
                "args": {"name": "arr", "shell_id": second_shell_id},
            },
            lambda result: (
                result.get("ok")
                and bool(result.get("payload", {}).get("variables"))
            ),
        )
        if not arr_result.get("ok"):
            raise RuntimeError(
                f"Timed out waiting for live array inspection: {arr_result}"
            )
        print(
            "[phase10] Array inspection:",
            arr_result["payload"]["variables"][0].get("shape", ""),
            arr_result["payload"]["variables"][0].get("dtype", ""),
            arr_result["payload"]["variables"][0].get("range", ""),
        )

        error_result = _wait_for_runtime_result(
            service,
            {
                "tool": "runtime.get_latest_error",
                "args": {"shell_id": second_shell_id},
            },
            lambda result: (
                result.get("ok")
                and bool(result.get("payload", {}).get("summary", {}).get("exception_type"))
            ),
        )
        if not error_result.get("ok"):
            raise RuntimeError(
                f"Timed out waiting for traceback summary on the second console: {error_result}"
            )
        print(
            "[phase10] Latest error summary:",
            error_result["payload"]["summary"].get("exception_type", ""),
            error_result["payload"]["summary"].get("frame_count", 0),
        )

        print("[phase10] pinning the runtime target to the second console")
        set_current_shell(window, first_shell)
        select_runtime_target(widget, second_shell_id)
        pinned = wait_for(
            lambda: (
                widget._runtime_context_snapshot.get("target_shell_id") == second_shell_id
                and widget._runtime_context_snapshot.get("active_shell_id") == first_shell_id
                and widget._runtime_context_snapshot.get("shell_id") == second_shell_id
            ),
            timeout_ms=10000,
            step_ms=100,
        )
        if not pinned:
            raise RuntimeError("Runtime target did not pin to the selected console")

        print("[phase10] listing shell targets and checking target flags")
        shells_result = service.execute_request({"tool": "runtime.list_shells", "args": {}})
        shell_payload = shells_result.get("payload", {}).get("shells", [])
        if len(shell_payload) < 2:
            raise RuntimeError("Runtime shell listing did not report two consoles")
        print(
            "[phase10] Shell targets:",
            [
                (
                    shell.get("label", ""),
                    shell.get("is_active", False),
                    shell.get("is_target", False),
                    shell.get("has_error", False),
                )
                for shell in shell_payload
            ],
        )

        print("[phase10] sending one real chat request against the pinned console")
        answer = send_prompt(
            widget,
            (
                "Inspect the variable named df in the selected console and "
                "answer with only the DataFrame column names separated by commas."
            ),
            timeout_ms=120000,
        )
        print("[phase10] Chat answer:", answer)

        results["runtime_target"] = {
            "first_shell_id": first_shell_id,
            "second_shell_id": second_shell_id,
            "active_shell_id": widget._runtime_context_snapshot.get("active_shell_id", ""),
            "target_shell_id": widget._runtime_context_snapshot.get("target_shell_id", ""),
            "shell_id": widget._runtime_context_snapshot.get("shell_id", ""),
            "runtime_tooltip": widget.runtime_label.toolTip(),
            "selector_text": widget.runtime_target_combo.currentText(),
        }
        results["shell_listing"] = shells_result
        results["df_inspection"] = df_result
        results["arr_inspection"] = arr_result
        results["latest_error"] = error_result
        results["chat_answer"] = answer
        results["checks"] = {
            "df_columns": df_result["payload"]["variables"][0].get("columns", ""),
            "df_preview": df_result["payload"]["variables"][0].get("preview", ""),
            "arr_range": arr_result["payload"]["variables"][0].get("range", ""),
            "arr_preview": arr_result["payload"]["variables"][0].get("preview", ""),
            "error_exception": error_result["payload"]["summary"].get("exception_type", ""),
            "error_frame_count": error_result["payload"]["summary"].get("frame_count", 0),
            "chat_mentions_columns": (
                "a" in answer.lower() and "b" in answer.lower()
            ),
        }

        if results["checks"]["df_columns"] != "a, b":
            raise RuntimeError("Phase 10 DataFrame inspection did not expose columns")
        if results["checks"]["arr_range"] != "0..5":
            raise RuntimeError("Phase 10 array inspection did not expose the numeric range")
        if results["checks"]["error_exception"] != "ZeroDivisionError":
            raise RuntimeError("Phase 10 traceback summary did not expose the exception type")
        if results["checks"]["error_frame_count"] < 1:
            raise RuntimeError("Phase 10 traceback summary did not expose stack frames")
        if not results["checks"]["chat_mentions_columns"]:
            raise RuntimeError("Pinned-console chat answer did not mention the DataFrame columns")

    except Exception as error:  # pragma: no cover - live validation guard
        results["errors"].append(str(error))
        results["traceback"] = traceback.format_exc()

    record_validation_result(window, RESULT_PATH, results)
    write_json(RESULT_PATH, results)
    finalize(window)


if __name__ == "__main__":
    raise SystemExit(
        run_spyder_validation(
            CONFIG_DIR,
            filter_log="spyder_ai_assistant",
            run_validation=run_validation,
            attr_name="_phase10_runtime_validation_ran",
        )
    )
