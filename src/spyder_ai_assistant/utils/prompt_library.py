"""Built-in chat prompt presets for per-session working modes."""

from __future__ import annotations


DEFAULT_CHAT_PROMPT_PRESET = "coding"


_PROMPT_PRESETS = {
    "coding": {
        "id": "coding",
        "label": "Coding",
        "description": (
            "Implementation-focused help for writing, refactoring, and "
            "modifying code."
        ),
        "instructions": (
            "Active chat mode: Coding.\n"
            "Prioritize implementation, refactoring, and concrete code "
            "changes. Prefer concise explanations and runnable code when "
            "useful."
        ),
    },
    "debugging": {
        "id": "debugging",
        "label": "Debugging",
        "description": (
            "Root-cause analysis and practical fixes for broken or confusing "
            "runtime behavior."
        ),
        "instructions": (
            "Active chat mode: Debugging.\n"
            "Prioritize root-cause analysis, failure isolation, and concrete "
            "fixes. When runtime state matters, inspect the most relevant "
            "live data before answering."
        ),
    },
    "explanation": {
        "id": "explanation",
        "label": "Explanation",
        "description": (
            "Teaching-oriented help for understanding code, APIs, and design "
            "choices."
        ),
        "instructions": (
            "Active chat mode: Explanation.\n"
            "Prioritize clarity, step-by-step reasoning, and concise examples. "
            "Explain why the code works, not only what it does."
        ),
    },
    "documentation": {
        "id": "documentation",
        "label": "Documentation",
        "description": (
            "Documentation-oriented help for docstrings, usage notes, and "
            "maintainable written guidance."
        ),
        "instructions": (
            "Active chat mode: Documentation.\n"
            "Prioritize docstrings, API descriptions, usage notes, and clear "
            "maintainable documentation. Prefer polished prose with compact "
            "examples when helpful."
        ),
    },
    "review": {
        "id": "review",
        "label": "Review",
        "description": (
            "Code-review help focused on risks, regressions, missing tests, "
            "and maintainability issues."
        ),
        "instructions": (
            "Active chat mode: Review.\n"
            "Prioritize bugs, regressions, edge cases, missing validation, "
            "and maintainability risks. Favor concrete findings and proposed "
            "fixes over general praise."
        ),
    },
    "analysis": {
        "id": "analysis",
        "label": "Data Analysis",
        "description": (
            "Scientific and data-oriented help for arrays, tables, plots, "
            "and exploratory workflows."
        ),
        "instructions": (
            "Active chat mode: Data Analysis.\n"
            "Prioritize data shape, units, transformations, statistical "
            "sanity checks, plotting clarity, and reproducible analysis steps. "
            "When runtime state matters, inspect the smallest useful live "
            "subset before answering."
        ),
    },
}


def list_chat_prompt_presets():
    """Return the built-in chat prompt presets in UI order."""
    return [
        _PROMPT_PRESETS["coding"],
        _PROMPT_PRESETS["debugging"],
        _PROMPT_PRESETS["review"],
        _PROMPT_PRESETS["analysis"],
        _PROMPT_PRESETS["explanation"],
        _PROMPT_PRESETS["documentation"],
    ]


def normalize_chat_prompt_preset(preset_id):
    """Return a valid built-in preset id."""
    if isinstance(preset_id, str):
        cleaned = preset_id.strip().lower()
        if cleaned in _PROMPT_PRESETS:
            return cleaned
    return DEFAULT_CHAT_PROMPT_PRESET


def get_chat_prompt_preset(preset_id):
    """Return one normalized prompt preset payload."""
    return _PROMPT_PRESETS[normalize_chat_prompt_preset(preset_id)]


def build_chat_prompt_preset_block(preset_id):
    """Return the system-prompt block for one preset."""
    preset = get_chat_prompt_preset(preset_id)
    return preset["instructions"]
