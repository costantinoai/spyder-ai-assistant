"""Prompt and export helpers for Phase 4 chat workflows."""

from __future__ import annotations

from datetime import datetime

from spyder_ai_assistant.utils.chat_inference import (
    describe_chat_inference_source,
    format_chat_temperature,
)


DEBUG_ACTION_LABELS = {
    "explain_error": "Explain Error",
    "fix_traceback": "Fix Traceback",
    "use_variables": "Use Variables",
    "use_console": "Use Console",
}


def build_debug_prompt(action, user_text="", context_label=""):
    """Build a debug-oriented prompt for one-click chat actions.

    The generated prompts are explicit about using live Spyder runtime data,
    but they still rely on the runtime bridge instead of injecting large
    snapshots directly into the prompt.
    """
    cleaned_user_text = (user_text or "").strip()
    context_hint = (
        f"Current editor context: {context_label}.\n\n"
        if context_label else ""
    )

    base_prompts = {
        "explain_error": (
            "Inspect the latest traceback or error from the active Spyder "
            "IPython console, explain the root cause clearly, and point to "
            "the most likely fix."
        ),
        "fix_traceback": (
            "Inspect the latest traceback or error from the active Spyder "
            "IPython console, identify the root cause, and propose a concrete "
            "code fix. If the traceback alone is not enough, inspect the most "
            "relevant live variables before answering."
        ),
        "use_variables": (
            "Inspect the current live variables in the active Spyder IPython "
            "session and summarize the state that matters for debugging the "
            "current problem."
        ),
        "use_console": (
            "Inspect the recent console output from the active Spyder IPython "
            "console and summarize the execution history or messages that "
            "matter for the current problem."
        ),
    }
    base_prompt = base_prompts.get(
        action,
        "Inspect the relevant live Spyder runtime state before answering.",
    )

    if cleaned_user_text:
        return (
            f"{base_prompt}\n\n"
            f"{context_hint}"
            "User request:\n"
            f"{cleaned_user_text}"
        )

    return f"{base_prompt}\n\n{context_hint}".rstrip()


def build_export_markdown(messages, model_name="", context_label="",
                          runtime_context=None, prompt_preset_label="",
                          inference_metadata=None):
    """Render one conversation plus metadata as Markdown."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"# AI Chat Export — {timestamp}\n"]

    if model_name:
        lines.append(f"**Model:** {model_name}")
    if prompt_preset_label:
        lines.append(f"**Chat mode:** {prompt_preset_label}")
    inference_lines = _build_inference_metadata_lines(inference_metadata or {})
    lines.extend(inference_lines)
    if context_label:
        lines.append(f"**Editor context:** {context_label}")

    runtime_lines = _build_runtime_metadata_lines(runtime_context or {})
    lines.extend(runtime_lines)
    if len(lines) > 1:
        lines.append("")

    for msg in messages:
        role = msg.get("role", "").capitalize()
        content = msg.get("content", "")
        if role == "User":
            lines.append(f"## You\n\n{content}\n")
        elif role == "Assistant":
            lines.append(f"## AI\n\n{content}\n")

    return "\n".join(lines)


def _build_inference_metadata_lines(inference_metadata):
    """Return exportable per-tab inference metadata lines when available."""
    if not inference_metadata:
        return []

    temperature = inference_metadata.get("temperature")
    temperature_source = describe_chat_inference_source(
        inference_metadata.get("temperature_source")
    )
    num_predict = inference_metadata.get("num_predict")
    num_predict_source = describe_chat_inference_source(
        inference_metadata.get("num_predict_source")
    )

    if temperature is None or num_predict is None:
        return []

    return [
        (
            "**Temperature:** "
            f"{format_chat_temperature(temperature)} ({temperature_source})"
        ),
        f"**Max tokens:** {int(num_predict)} ({num_predict_source})",
    ]


def _build_runtime_metadata_lines(runtime_context):
    """Return exportable runtime metadata lines when available."""
    if not runtime_context:
        return []

    status = runtime_context.get("status", "")
    detail = runtime_context.get("status_detail", "")
    cwd = runtime_context.get("working_directory", "")
    refreshed = runtime_context.get("last_refreshed_at", "")
    variables = runtime_context.get("variables") or []
    latest_error = runtime_context.get("latest_error", "")

    lines = []
    if status:
        lines.append(f"**Runtime status:** {status}")
    if detail:
        lines.append(f"**Runtime detail:** {detail}")
    if cwd:
        lines.append(f"**Runtime cwd:** {cwd}")
    if refreshed:
        lines.append(f"**Runtime last refreshed:** {refreshed}")
    if variables:
        lines.append(f"**Runtime variables tracked:** {len(variables)}")
    if latest_error:
        lines.append("**Runtime latest error:** available")

    return lines
