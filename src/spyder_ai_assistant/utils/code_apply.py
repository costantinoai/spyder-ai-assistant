"""Helpers for previewing and applying chat-generated code changes."""

from __future__ import annotations

import difflib

from spyder_ai_assistant.utils.text_positions import python_index, utf16_length


APPLY_MODE_INSERT = "insert"
APPLY_MODE_REPLACE = "replace"
VALID_APPLY_MODES = {APPLY_MODE_INSERT, APPLY_MODE_REPLACE}
MAX_APPLY_PREVIEW_CHARS = 240


def normalize_apply_mode(mode, default=APPLY_MODE_INSERT):
    """Return a supported apply mode."""
    normalized = str(mode or "").strip().lower()
    if normalized in VALID_APPLY_MODES:
        return normalized
    return default


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

    if requested_mode == APPLY_MODE_REPLACE and has_selection:
        updated_text = (
            document_text[:start_index] + code + document_text[end_index:]
        )
        mode_label = "Replace selection"
        note = "Apply the code by replacing the current editor selection."
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
        else:
            note = "Apply the code by inserting it at the current cursor position."

    return {
        "requested_mode": requested_mode,
        "effective_mode": effective_mode,
        "mode_label": mode_label,
        "has_selection": has_selection,
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
