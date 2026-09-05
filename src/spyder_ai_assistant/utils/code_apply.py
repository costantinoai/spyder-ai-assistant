"""Helpers for previewing and applying chat-generated code changes."""

from __future__ import annotations

import ast
import difflib
import re

from spyder_ai_assistant.utils.text_positions import python_index, utf16_length


APPLY_MODE_INSERT = "insert"
APPLY_MODE_REPLACE = "replace"
# Replace the top-level function/class in the document that has the same
# name as the one the code defines. Models usually answer with a whole
# rewritten definition; this applies it without selecting the old one.
APPLY_MODE_REPLACE_DEFINITION = "replace_definition"
VALID_APPLY_MODES = {
    APPLY_MODE_INSERT,
    APPLY_MODE_REPLACE,
    APPLY_MODE_REPLACE_DEFINITION,
}

_DEFINITION_RE = re.compile(
    r"^[ \t]*(?:@[^\n]*\n[ \t]*)*(?:async[ \t]+)?(def|class)[ \t]+([A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
MAX_APPLY_PREVIEW_CHARS = 240


def normalize_apply_mode(mode, default=APPLY_MODE_INSERT):
    """Return a supported apply mode."""
    normalized = str(mode or "").strip().lower()
    if normalized in VALID_APPLY_MODES:
        return normalized
    return default


def leading_definition(code):
    """Return ``(kind, name)`` of the first def/class the code defines, or None.

    Uses the AST when the snippet parses; falls back to a regex so partial
    snippets (missing imports, unbalanced context) still work.
    """
    text = code or ""
    try:
        module = ast.parse(text)
    except SyntaxError:
        module = None
    if module is not None:
        for node in module.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return "def", node.name
            if isinstance(node, ast.ClassDef):
                return "class", node.name
        return None
    match = _DEFINITION_RE.search(text)
    if match:
        return match.group(1), match.group(2)
    return None


def find_definition_span(document_text, code):
    """Locate the definition in ``document_text`` that ``code`` redefines.

    Returns ``None`` when the code does not start with a def/class or the
    document has no top-level definition of that name. Otherwise returns a
    dict with the definition ``kind``/``name``, 1-based ``start_line`` and
    ``end_line`` (decorators included), and Qt (UTF-16) ``start``/``end``
    positions covering those whole lines without the final newline.
    """
    definition = leading_definition(code)
    if definition is None:
        return None
    kind, name = definition
    try:
        module = ast.parse(document_text or "")
    except SyntaxError:
        return None
    wanted = (ast.FunctionDef, ast.AsyncFunctionDef) if kind == "def" else (ast.ClassDef,)
    for node in module.body:
        if not isinstance(node, wanted) or node.name != name:
            continue
        first_line = min([node.lineno] + [d.lineno for d in node.decorator_list])
        last_line = getattr(node, "end_lineno", node.lineno)
        lines = (document_text or "").splitlines(keepends=True)
        before = "".join(lines[: first_line - 1])
        block = "".join(lines[first_line - 1:last_line]).rstrip("\r\n")
        return {
            "kind": kind,
            "name": name,
            "start_line": first_line,
            "end_line": last_line,
            "start": utf16_length(before),
            "end": utf16_length(before) + utf16_length(block),
        }
    return None


def build_code_apply_plan(
    document_text,
    code,
    cursor_position,
    selection_start=None,
    selection_end=None,
    requested_mode=APPLY_MODE_INSERT,
    context_lines=3,
):
    """Build a previewable plan for inserting or replacing code."""
    document_text = document_text or ""
    code = code or ""
    requested_mode = normalize_apply_mode(requested_mode)

    # QTextCursor positions count UTF-16 units, while Python slices count
    # Unicode code points. Keep Qt positions in the plan used for mutation,
    # and convert only the indexes used to build the preview text.
    qt_length = utf16_length(document_text)
    cursor_position = _clamp_index(cursor_position, qt_length)
    selection_start = _clamp_index(selection_start, qt_length)
    selection_end = _clamp_index(selection_end, qt_length)
    if selection_end < selection_start:
        selection_start, selection_end = selection_end, selection_start

    has_selection = selection_end > selection_start
    cursor_index = python_index(document_text, cursor_position)
    start_index = python_index(document_text, selection_start)
    end_index = python_index(document_text, selection_end)
    effective_mode = requested_mode
    note = ""
    definition = (
        find_definition_span(document_text, code)
        if requested_mode == APPLY_MODE_REPLACE_DEFINITION
        else None
    )
    replace_start = replace_end = None

    if requested_mode == APPLY_MODE_REPLACE and has_selection:
        updated_text = (
            document_text[:start_index] + code + document_text[end_index:]
        )
        replace_start, replace_end = selection_start, selection_end
        mode_label = "Replace selection"
        note = "Apply the code by replacing the current editor selection."
    elif requested_mode == APPLY_MODE_REPLACE_DEFINITION and definition:
        replace_start, replace_end = definition["start"], definition["end"]
        def_start_index = python_index(document_text, replace_start)
        def_end_index = python_index(document_text, replace_end)
        # The span excludes the definition's final newline; drop one trailing
        # newline from the code so the block boundary is preserved as-is.
        replacement = code[:-1] if code.endswith("\n") else code
        updated_text = (
            document_text[:def_start_index] + replacement + document_text[def_end_index:]
        )
        mode_label = f"Replace {definition['kind']} {definition['name']}"
        note = (
            f"Apply the code by replacing the existing {definition['kind']} "
            f"'{definition['name']}' (lines {definition['start_line']}-"
            f"{definition['end_line']})."
        )
    else:
        effective_mode = APPLY_MODE_INSERT
        updated_text = (
            document_text[:cursor_index] + code + document_text[cursor_index:]
        )
        mode_label = "Insert at cursor"
        if requested_mode == APPLY_MODE_REPLACE:
            note = (
                "No active selection is available, so replace-selection falls "
                "back to inserting at the current cursor position."
            )
        elif requested_mode == APPLY_MODE_REPLACE_DEFINITION:
            note = (
                "The document has no top-level definition matching the code, "
                "so replace-definition falls back to inserting at the cursor."
            )
        else:
            note = "Apply the code by inserting it at the current cursor position."

    return {
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "mode_label": mode_label,
        "has_selection": has_selection,
        # Qt positions of the range the code replaces; None when inserting.
        "replace_start": replace_start,
        "replace_end": replace_end,
        "definition": definition,
        "cursor_position": cursor_position,
        "selection_start": selection_start,
        "selection_end": selection_end,
        "selection_text": (
            document_text[start_index:end_index] if has_selection else ""
        ),
        "selection_preview": preview_text(
            document_text[start_index:end_index] if has_selection else ""
        ),
        "code_preview": preview_text(code),
        "document_text": document_text,
        "updated_text": updated_text,
        "diff_text": build_code_apply_diff(
            document_text,
            updated_text,
            context_lines=context_lines,
        ),
        "line_delta": updated_text.count("\n") - document_text.count("\n"),
        "note": note,
    }


def build_code_apply_diff(before_text, after_text, context_lines=3):
    """Return a unified diff preview for one editor mutation."""
    # keepends=True so a change that only adds or removes the final newline
    # is still a change (splitlines() alone would report "(no changes)").
    before_lines = (before_text or "").splitlines(keepends=True)
    after_lines = (after_text or "").splitlines(keepends=True)
    diff = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="before",
            tofile="after",
            n=max(0, int(context_lines)),
        )
    )
    if not diff:
        return "(no changes)"
    rendered = []
    for line in diff:
        if line.endswith("\n"):
            rendered.append(line)
        else:
            # Same marker git uses, so the difference is visible.
            rendered.append(line + "\n\\ No newline at end of file\n")
    return "".join(rendered).rstrip("\n")


def preview_text(text, limit=MAX_APPLY_PREVIEW_CHARS):
    """Return a bounded one-line preview of some code or selection text."""
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[:limit].rstrip() + "..."


def _clamp_index(index, length):
    """Clamp a cursor or selection index into the document bounds."""
    try:
        numeric = int(index)
    except (TypeError, ValueError):
        numeric = 0
    return max(0, min(numeric, length))


def apply_code_plan(editor, code, plan):
    """Mutate ``editor`` according to a plan from ``build_code_apply_plan``.

    One implementation for the chat apply dialog and the MCP apply tool.
    Replacement ranges come from the plan (selection or definition span);
    everything else inserts at the plan's cursor position. The change is a
    single undo step and the cursor ends after the inserted code.
    """
    from qtpy.QtGui import QTextCursor

    cursor = editor.textCursor()
    cursor.beginEditBlock()
    try:
        if plan.get("replace_start") is not None and plan.get("replace_end") is not None:
            cursor.setPosition(int(plan["replace_start"]))
            cursor.setPosition(int(plan["replace_end"]), QTextCursor.KeepAnchor)
            text = code
            if plan.get("effective_mode") == APPLY_MODE_REPLACE_DEFINITION and code.endswith("\n"):
                text = code[:-1]
            cursor.insertText(text)
        else:
            cursor.clearSelection()
            cursor.setPosition(int(plan["cursor_position"]))
            editor.setTextCursor(cursor)
            cursor.insertText(code)
    finally:
        cursor.endEditBlock()
        editor.setTextCursor(cursor)
    return plan.get("effective_mode")
