"""The tool vocabulary and result fields shared by chat and MCP.

The chat runtime bridge and the embedded MCP server expose one set of tools.
Their names were spelled as literals in four places (the canonical tuples,
the project dispatch table, the runtime dispatch chain and every MCP tool
registration), so a typo produced a silent "unsupported tool" instead of an
error, and adding a tool meant remembering all four sites.

This module owns the spellings. It imports nothing so every layer can use it,
including the latency-sensitive completion path.
"""

from __future__ import annotations


# --- Project and git tools -------------------------------------------------

TOOL_PROJECT_LIST_FILES = "project.list_files"
TOOL_PROJECT_READ_FILE = "project.read_file"
TOOL_PROJECT_SEARCH = "project.search"
TOOL_GIT_STATUS = "git.status"
TOOL_GIT_DIFF = "git.diff"
TOOL_GIT_LOG = "git.log"

PROJECT_TOOL_NAMES = (
    TOOL_PROJECT_LIST_FILES,
    TOOL_PROJECT_READ_FILE,
    TOOL_PROJECT_SEARCH,
    TOOL_GIT_STATUS,
    TOOL_GIT_DIFF,
    TOOL_GIT_LOG,
)

# Namespaces that route a request to the project tools rather than to the
# runtime bridge.
PROJECT_TOOL_PREFIXES = ("project.", "git.")

# --- Read-only runtime inspection ------------------------------------------

TOOL_RUNTIME_STATUS = "runtime.status"
TOOL_RUNTIME_LIST_SHELLS = "runtime.list_shells"
TOOL_RUNTIME_GET_LATEST_ERROR = "runtime.get_latest_error"
TOOL_RUNTIME_GET_CONSOLE_TAIL = "runtime.get_console_tail"
TOOL_RUNTIME_LIST_VARIABLES = "runtime.list_variables"
TOOL_RUNTIME_INSPECT_VARIABLE = "runtime.inspect_variable"
TOOL_RUNTIME_INSPECT_VARIABLES = "runtime.inspect_variables"

RUNTIME_TOOL_NAMES = (
    TOOL_RUNTIME_STATUS,
    TOOL_RUNTIME_LIST_SHELLS,
    TOOL_RUNTIME_GET_LATEST_ERROR,
    TOOL_RUNTIME_GET_CONSOLE_TAIL,
    TOOL_RUNTIME_LIST_VARIABLES,
    TOOL_RUNTIME_INSPECT_VARIABLE,
    TOOL_RUNTIME_INSPECT_VARIABLES,
)

# Explicit console execution. Deliberately outside RUNTIME_TOOL_NAMES: the
# chat model may not request it through the read-only request block, it is
# reachable only through the confirmed MCP action.
TOOL_RUNTIME_EXECUTE_CODE = "runtime.execute_code"

# --- Result envelopes ------------------------------------------------------

# Metadata every runtime result carries besides "ok", "tool" and "payload".
# The MCP normalizer returns exactly these plus "ok", with the payload
# flattened into the same mapping.
RUNTIME_RESULT_METADATA_FIELDS = (
    "source",
    "shell_status",
    "shell_detail",
    "shell_id",
    "shell_label",
    "active_shell_id",
    "active_shell_label",
    "target_shell_id",
    "target_shell_label",
    "working_directory",
    "last_refreshed_at",
    "query_note",
    "error",
)

# Shown when no IPython console can serve a runtime request.
NO_ACTIVE_CONSOLE_MESSAGE = "No active IPython console is available."
