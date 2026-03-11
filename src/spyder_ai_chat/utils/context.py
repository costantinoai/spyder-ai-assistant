"""Context extraction utilities for the AI Chat plugin.

Provides pure functions to extract editor state (file content, selection,
cursor position), IPython console state (output, variables), and build
context blocks for LLM system prompts. Supports multiple context sources:
- Active editor file (full content, cursor, selection)
- Other open editor files (summaries)
- Project structure (file tree)
- IPython console output (recent lines)
- Namespace variables (names, types, values from Variable Explorer)

Context budget management:
- Active file: included in full (up to MAX_FILE_CHARS)
- Other open files: included as summaries (first N lines + signature)
- Project structure: file tree of the project root (limited depth)
- Console output: last N lines (up to MAX_CONSOLE_CHARS)
- Variables: up to MAX_VARIABLES with truncated value previews

Used by:
- plugin.py: to provide context to the chat widget
- chat_widget.py: to enrich the system prompt with file context
"""

import os
import logging

logger = logging.getLogger(__name__)

# --- Context budget constants ---
# These control how much context is included in the system prompt.
# The goal is to give the LLM enough context to be helpful without
# overwhelming the model's context window or adding latency.

# Maximum characters for the active file (full content).
# 50K chars ≈ ~12K tokens, leaving room for conversation history.
MAX_FILE_CHARS = 50_000

# Maximum characters per non-active open file (summary only).
# ~30 lines of code, enough to see imports and class/function signatures.
MAX_OTHER_FILE_CHARS = 2_000

# Maximum total characters for all non-active open file summaries combined.
# Prevents context explosion when many files are open.
MAX_TOTAL_OTHER_FILES_CHARS = 10_000

# Maximum number of non-active files to include in context.
# Beyond this, the LLM gets diminishing returns and latency increases.
MAX_OTHER_FILES = 8

# Maximum depth for project file tree listing.
MAX_TREE_DEPTH = 3

# Maximum entries in the project file tree.
MAX_TREE_ENTRIES = 50

# Maximum lines of recent console output to include.
# 50 lines is enough to capture a traceback + surrounding output.
MAX_CONSOLE_LINES = 50

# Maximum total characters for console output.
# Prevents very long output lines from bloating the context.
MAX_CONSOLE_CHARS = 5_000

# Maximum number of namespace variables to include.
# Beyond this, the variable list becomes noise rather than signal.
MAX_VARIABLES = 30

# Maximum characters for a single variable's value preview.
# Large arrays/dataframes are truncated to avoid context explosion.
MAX_VAR_VALUE_CHARS = 200


# File extension → language name mapping for common languages.
# Used to label file context blocks in the system prompt.
_EXTENSION_TO_LANGUAGE = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".jsx": "jsx",
    ".tsx": "tsx",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".sh": "bash",
    ".bash": "bash",
    ".r": "r",
    ".R": "r",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".sql": "sql",
    ".xml": "xml",
}

# Directories to skip when building the project file tree.
# These are typically large, auto-generated, or irrelevant for context.
_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    "node_modules", ".tox", ".venv", "venv", "env", ".env", ".eggs",
    "dist", "build", ".spyproject", ".idea", ".vscode",
}


def _language_from_filename(filename):
    """Infer language from file extension.

    Args:
        filename: Full path or basename of the file.

    Returns:
        Language name string (e.g., "python"), or the extension without dot.
    """
    ext = os.path.splitext(filename)[1].lower() if filename else ""
    return _EXTENSION_TO_LANGUAGE.get(ext, ext.lstrip(".") or "text")


# ---------------------------------------------------------------------------
# Single-file context (active editor) — used by both chat and completions
# ---------------------------------------------------------------------------

def get_editor_context(editor, editor_plugin):
    """Extract the current editor state as a context dictionary.

    Reads the active file's content, selection, cursor position, and
    filename from the Spyder editor. Returns an empty dict if the
    editor or plugin is not available.

    Args:
        editor: The active CodeEditor instance (may be None).
        editor_plugin: The Spyder Editor plugin instance (may be None).

    Returns:
        Dict with keys:
            - filename (str): Full path to the current file.
            - basename (str): Just the filename without directory.
            - language (str): Inferred language from file extension.
            - cursor_line (int): 0-based line number of the cursor.
            - cursor_col (int): 0-based column number of the cursor.
            - selection (str): Currently selected text, or "".
            - full_content (str): The entire file content.
        Returns empty dict if editor is not available.
    """
    if editor is None or editor_plugin is None:
        return {}

    try:
        filename = editor_plugin.get_current_filename() or ""
        basename = os.path.basename(filename) if filename else ""
        language = _language_from_filename(filename)

        line, col = editor.get_cursor_line_column()
        selection = editor.get_selected_text() or ""
        full_content = editor.toPlainText() or ""

        return {
            "filename": filename,
            "basename": basename,
            "language": language,
            "cursor_line": line,
            "cursor_col": col,
            "selection": selection,
            "full_content": full_content,
        }
    except Exception as e:
        logger.warning("Failed to extract editor context: %s", e)
        return {}


def get_toolbar_context(editor, editor_plugin):
    """Build a short context string for the toolbar label.

    Shows the current filename and cursor line, e.g. "main.py:42".
    Returns an empty string if no editor is active.

    Args:
        editor: The active CodeEditor instance (may be None).
        editor_plugin: The Spyder Editor plugin instance (may be None).

    Returns:
        String like "main.py:42", or "" if no editor is active.
    """
    if editor is None or editor_plugin is None:
        return ""

    try:
        filename = editor_plugin.get_current_filename() or ""
        basename = os.path.basename(filename) if filename else ""
        # Line is 0-based internally, display as 1-based for the user
        line, _ = editor.get_cursor_line_column()
        if basename:
            return f"{basename}:{line + 1}"
        return ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Multi-file context — all open files (for chat system prompt)
# ---------------------------------------------------------------------------

def get_open_files_context(editor_plugin, current_filename=""):
    """Get summaries of all open files (excluding the active one).

    For each non-active open file, extracts the first N characters
    as a summary. This gives the LLM awareness of what else the user
    is working on without consuming the entire context budget.

    Args:
        editor_plugin: The Spyder Editor plugin instance.
        current_filename: The active file's path (excluded from results).

    Returns:
        List of dicts with keys:
            - filename (str): Full path.
            - basename (str): Just the filename.
            - language (str): Inferred language.
            - summary (str): First MAX_OTHER_FILE_CHARS of the file content.
            - total_lines (int): Total number of lines in the file.
    """
    if editor_plugin is None:
        return []

    try:
        all_filenames = editor_plugin.get_filenames()
    except Exception as e:
        logger.warning("Failed to get open filenames: %s", e)
        return []

    # Filter out the active file and limit the count
    other_files = [f for f in all_filenames if f != current_filename]
    other_files = other_files[:MAX_OTHER_FILES]

    results = []
    for filename in other_files:
        try:
            # Get the CodeEditor instance for this file
            code_editor = editor_plugin.get_codeeditor_for_filename(filename)
            if code_editor is None:
                continue

            content = code_editor.toPlainText() or ""
            total_lines = content.count("\n") + 1
            # Take a summary: first N characters (enough for imports + signatures)
            summary = content[:MAX_OTHER_FILE_CHARS]
            if len(content) > MAX_OTHER_FILE_CHARS:
                summary += f"\n... ({total_lines} lines total)"

            results.append({
                "filename": filename,
                "basename": os.path.basename(filename),
                "language": _language_from_filename(filename),
                "summary": summary,
                "total_lines": total_lines,
            })
        except Exception as e:
            logger.debug("Failed to read open file %s: %s", filename, e)

    return results


# ---------------------------------------------------------------------------
# Project context — file tree and project root
# ---------------------------------------------------------------------------

def get_project_context(projects_plugin):
    """Get the active project's root path and file tree.

    Scans the project directory to build a tree listing of source files.
    Skips common non-source directories (.git, __pycache__, node_modules, etc.)
    and limits depth and entry count to keep the context manageable.

    Args:
        projects_plugin: The Spyder Projects plugin instance (may be None).

    Returns:
        Dict with keys:
            - project_path (str): Absolute path to the project root.
            - project_name (str): Directory name of the project root.
            - file_tree (str): Indented text listing of project files.
        Returns empty dict if no project is active or plugin unavailable.
    """
    if projects_plugin is None:
        return {}

    try:
        project_path = projects_plugin.get_active_project_path()
    except Exception:
        return {}

    if not project_path:
        return {}

    project_name = os.path.basename(project_path)

    # Build a file tree listing with limited depth and entry count
    file_tree = _build_file_tree(project_path)

    return {
        "project_path": project_path,
        "project_name": project_name,
        "file_tree": file_tree,
    }


def _build_file_tree(root_path, max_depth=MAX_TREE_DEPTH,
                     max_entries=MAX_TREE_ENTRIES):
    """Build an indented file tree listing of a directory.

    Produces output like:
        src/
          spyder_ai_chat/
            plugin.py
            backend/
              client.py
              worker.py
        tests/
          test_client.py

    Skips hidden directories, __pycache__, node_modules, and other
    non-source directories to keep the tree focused on relevant files.

    Args:
        root_path: Directory to scan.
        max_depth: Maximum directory nesting depth.
        max_entries: Maximum number of entries to include.

    Returns:
        Indented text string of the file tree.
    """
    lines = []
    entry_count = [0]  # Mutable counter for the recursive closure

    def _walk(path, depth, indent):
        """Recursively list directory contents with indentation."""
        if depth > max_depth or entry_count[0] >= max_entries:
            return

        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return

        # Separate dirs and files, process dirs first for readability
        dirs = []
        files = []
        for entry in entries:
            full = os.path.join(path, entry)
            if os.path.isdir(full):
                # Skip non-source directories (hidden, build artifacts, etc.)
                if (entry in _SKIP_DIRS
                        or entry.startswith(".")
                        or entry.endswith(".egg-info")):
                    continue
                dirs.append(entry)
            else:
                files.append(entry)

        for d in dirs:
            if entry_count[0] >= max_entries:
                lines.append(f"{indent}... (truncated)")
                return
            lines.append(f"{indent}{d}/")
            entry_count[0] += 1
            _walk(os.path.join(path, d), depth + 1, indent + "  ")

        for f in files:
            if entry_count[0] >= max_entries:
                lines.append(f"{indent}... (truncated)")
                return
            lines.append(f"{indent}{f}")
            entry_count[0] += 1

    _walk(root_path, 0, "")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Console context — IPython console output and namespace variables
# ---------------------------------------------------------------------------

def get_console_context(ipython_console_plugin):
    """Get the IPython console's recent output and namespace variables.

    Extracts two types of runtime context:
    1. Console output — the last N lines of text from the active console,
       including print output, tracebacks, and command results.
    2. Namespace variables — names, types, and value previews from the
       active kernel's namespace (what Variable Explorer shows).

    The console output is read synchronously from the console's text widget.
    Variables are fetched via a blocking kernel call with a short timeout
    to avoid freezing the UI. If the kernel is busy or unresponsive,
    variables are silently skipped.

    Args:
        ipython_console_plugin: The Spyder IPythonConsole plugin instance
            (may be None).

    Returns:
        Dict with optional keys:
            - console_output (str): Recent lines from the console.
            - variables (list[str]): Variable summaries like
              "x (int): 42", "df (DataFrame): (100, 5)".
        Returns empty dict if the plugin is unavailable or no console
        is active.
    """
    if ipython_console_plugin is None:
        return {}

    try:
        shellwidget = ipython_console_plugin.get_current_shellwidget()
    except Exception:
        return {}

    if shellwidget is None:
        return {}

    result = {}

    # --- Console output (last N lines) ---
    # Read directly from the console's QPlainTextEdit control.
    # This contains everything the user sees: print output, tracebacks,
    # In/Out prompts, and system messages.
    try:
        control = shellwidget._control
        if control is not None:
            full_text = control.toPlainText() or ""
            lines = full_text.split("\n")
            # Take only the last N lines to keep context focused
            recent_lines = lines[-MAX_CONSOLE_LINES:]
            console_text = "\n".join(recent_lines)
            # Truncate if individual lines are very long
            if len(console_text) > MAX_CONSOLE_CHARS:
                console_text = console_text[-MAX_CONSOLE_CHARS:]
            if console_text.strip():
                result["console_output"] = console_text
    except Exception as e:
        logger.debug("Failed to get console output: %s", e)

    # --- Namespace variables ---
    # Use a blocking kernel call with a short timeout. This is called
    # when the user clicks Send, so a brief delay is acceptable.
    # If the kernel is busy (e.g., running code), the call times out
    # and we skip variables rather than blocking the UI.
    try:
        if hasattr(shellwidget, "spyder_kernel_ready") and \
                shellwidget.spyder_kernel_ready:
            namespace_view = shellwidget.call_kernel(
                blocking=True, timeout=2
            ).get_namespace_view()

            if namespace_view:
                var_summaries = []
                for name, info in list(namespace_view.items())[:MAX_VARIABLES]:
                    var_type = info.get("type", "?")
                    var_view = info.get("view", "")
                    # Truncate long value previews (large arrays, dataframes)
                    if len(var_view) > MAX_VAR_VALUE_CHARS:
                        var_view = var_view[:MAX_VAR_VALUE_CHARS] + "..."
                    var_summaries.append(f"{name} ({var_type}): {var_view}")
                if var_summaries:
                    result["variables"] = var_summaries
    except Exception as e:
        # TimeoutError, ConnectionError, or kernel not responding.
        # Silently skip variables — they're nice-to-have, not critical.
        logger.debug("Failed to get namespace variables: %s", e)

    return result


# ---------------------------------------------------------------------------
# System prompt context builders
# ---------------------------------------------------------------------------

def build_system_context_block(context, open_files=None, project=None,
                               console=None):
    """Build the full context block for the chat system prompt.

    Assembles context from up to five sources:
    1. Project structure — file tree and project root
    2. Active file — full content with cursor position and selection
    3. Other open files — summaries (first ~30 lines each)
    4. IPython console output — recent lines (tracebacks, print output)
    5. Namespace variables — names, types, and value previews

    Each section is clearly delimited so the LLM can identify and
    reference specific parts of the context.

    Args:
        context: Dict from get_editor_context() (active file).
        open_files: List of dicts from get_open_files_context() (optional).
        project: Dict from get_project_context() (optional).
        console: Dict from get_console_context() (optional).
            Keys: "console_output" (str), "variables" (list[str]).

    Returns:
        A multi-line string for the system prompt, or "" if no context.
    """
    parts = []

    # --- Section 1: Project overview (if available) ---
    # Placed first so the LLM understands the project structure before
    # seeing individual file contents.
    if project:
        project_name = project.get("project_name", "")
        project_path = project.get("project_path", "")
        file_tree = project.get("file_tree", "")
        if file_tree:
            parts.append(
                f"[Project: {project_name}]\n"
                f"Root: {project_path}\n"
                f"--- project structure ---\n"
                f"{file_tree}\n"
                f"--- end project structure ---"
            )

    # --- Section 2: Active file (full content) ---
    if context:
        basename = context.get("basename", "")
        language = context.get("language", "")
        cursor_line = context.get("cursor_line", 0)
        full_content = context.get("full_content", "")
        selection = context.get("selection", "")

        # Header: file identity and cursor position (1-based for readability)
        header = (
            f"[Current File: {basename} ({language}), "
            f"cursor at line {cursor_line + 1}]"
        )

        # Truncate file content if it exceeds the character budget
        truncated_content = truncate_file_content(full_content)

        parts.append(
            f"{header}\n"
            f"--- file content ---\n"
            f"{truncated_content}\n"
            f"--- end file content ---"
        )

        # If user has text selected, highlight it separately so the LLM
        # knows what the user is focused on
        if selection:
            parts.append(
                f"--- selected text ---\n"
                f"{selection}\n"
                f"--- end selected text ---"
            )

    # --- Section 3: Other open files (summaries) ---
    # Include summaries of non-active files so the LLM knows about
    # related code the user has open. Budget-limited to avoid bloat.
    if open_files:
        total_chars = 0
        file_summaries = []
        for f in open_files:
            summary = f.get("summary", "")
            # Stop if adding this file would exceed the total budget
            if total_chars + len(summary) > MAX_TOTAL_OTHER_FILES_CHARS:
                break
            total_chars += len(summary)
            basename = f.get("basename", "")
            language = f.get("language", "")
            total_lines = f.get("total_lines", 0)
            file_summaries.append(
                f"[Open File: {basename} ({language}, "
                f"{total_lines} lines)]\n{summary}"
            )

        if file_summaries:
            parts.append(
                "--- other open files ---\n"
                + "\n\n".join(file_summaries)
                + "\n--- end other open files ---"
            )

    # --- Section 4: IPython console output (recent lines) ---
    # Shows the user's recent console activity: print output, tracebacks,
    # command results. Especially useful for debugging (the LLM can see
    # the error message and traceback).
    if console:
        console_output = console.get("console_output", "")
        if console_output:
            parts.append(
                "[IPython Console — recent output]\n"
                "--- console output ---\n"
                f"{console_output}\n"
                "--- end console output ---"
            )

        # --- Section 5: Namespace variables ---
        # Shows what variables exist in the user's kernel: their types
        # and current values. Helps the LLM write code that uses existing
        # variables and understand the data the user is working with.
        variables = console.get("variables", [])
        if variables:
            var_text = "\n".join(variables)
            parts.append(
                "[Namespace Variables]\n"
                "--- variables ---\n"
                f"{var_text}\n"
                "--- end variables ---"
            )

    return "\n\n".join(parts)


def truncate_file_content(content, max_chars=MAX_FILE_CHARS):
    """Truncate file content to fit within the character budget.

    If the content exceeds max_chars, it is cut off and a marker is
    appended showing the total size. This prevents very large files
    from consuming the entire model context window.

    Args:
        content: The full file content string.
        max_chars: Maximum number of characters to keep.

    Returns:
        The content, possibly truncated with a size marker.
    """
    if len(content) <= max_chars:
        return content

    return content[:max_chars] + f"\n... (truncated, {len(content)} chars total)"


def build_action_prompt(action, selection, filename, prompt_template=None):
    """Build the user-facing prompt for a context menu action.

    Uses a configurable template if provided, otherwise falls back to
    sensible defaults. Templates use {filename} and {code} as placeholders.

    Args:
        action: One of "explain", "fix", "docstring", "ask".
        selection: The selected code text.
        filename: The basename of the file (for display in the prompt).
        prompt_template: Optional template string with {filename} and {code}
            placeholders. If None, uses built-in defaults.

    Returns:
        A formatted prompt string ready to send to the chat.
    """
    # If a custom template is provided, use it with placeholder substitution
    if prompt_template:
        return prompt_template.format(filename=filename, code=selection)

    # Built-in defaults (used if no template configured)
    code_block = f"```\n{selection}\n```"

    defaults = {
        "explain": f"Explain this code from {filename}:\n\n{code_block}",
        "fix": f"Find and fix bugs in this code from {filename}:\n\n{code_block}",
        "docstring": f"Add a docstring to this code from {filename}:\n\n{code_block}",
        "ask": f"Regarding this code from {filename}:\n\n{code_block}\n\n",
    }

    return defaults.get(action, f"Help with this code from {filename}:\n\n{code_block}")
