"""Built-in chat display color theme presets.

Each preset provides both a dark and light variant with the same 23 color
keys used by ChatDisplay._theme. The resolver function merges user overrides
on top of the selected preset so individual colors can be customized without
losing the base palette.

Adding a new preset:
  1. Add a new entry to THEME_PRESETS with "dark" and "light" sub-dicts.
  2. Each sub-dict must contain all keys listed in THEME_COLOR_KEYS.
  3. The preset will automatically appear in the Appearance settings combo.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# Canonical ordered list of all theme color keys.
# Every preset must define all of these for both dark and light variants.
THEME_COLOR_KEYS = [
    "user_bg", "user_text", "user_label",
    "assistant_bg", "assistant_text", "assistant_label",
    "error_bg", "error_text", "error_label",
    "code_block_bg", "code_block_text",
    "inline_code_bg", "inline_code_text",
    "link_color",
    "thinking_bg", "thinking_text", "thinking_border",
    "scroll_btn_bg", "scroll_btn_text",
    "blockquote_bg", "blockquote_border", "blockquote_text",
    "table_border", "table_header_bg",
    "hr_color",
]

# The 8 most user-relevant color keys shown as swatch buttons in settings.
# Other keys are still overridable via the JSON blob but not shown in the UI.
EXPOSED_COLOR_KEYS = [
    ("user_bg", "User message background"),
    ("user_text", "User message text"),
    ("assistant_bg", "AI message background"),
    ("assistant_text", "AI message text"),
    ("code_block_bg", "Code block background"),
    ("code_block_text", "Code block text"),
    ("inline_code_bg", "Inline code background"),
    ("link_color", "Link color"),
]

# ---------------------------------------------------------------------------
# Built-in theme presets
# ---------------------------------------------------------------------------

THEME_PRESETS = {
    "default": {
        "dark": {
            "user_bg": "#1e3a5f", "user_text": "#e8e8e8",
            "user_label": "#7ab3ef",
            "assistant_bg": "#252525", "assistant_text": "#e0e0e0",
            "assistant_label": "#999999",
            "error_bg": "#3d1515", "error_text": "#ff8a80",
            "error_label": "#ff6b6b",
            "code_block_bg": "#1a1d23", "code_block_text": "#abb2bf",
            "inline_code_bg": "#383838", "inline_code_text": "#e0e0e0",
            "link_color": "#64b5f6",
            "thinking_bg": "#1a1a1a", "thinking_text": "#777777",
            "thinking_border": "#444444",
            "scroll_btn_bg": "rgba(255, 255, 255, 150)",
            "scroll_btn_text": "#000000",
            "blockquote_bg": "#2a2a2a", "blockquote_border": "#555555",
            "blockquote_text": "#aaaaaa",
            "table_border": "#444444", "table_header_bg": "#333333",
            "hr_color": "#555555",
        },
        "light": {
            "user_bg": "#d4e6f9", "user_text": "#1a1a1a",
            "user_label": "#2962a1",
            "assistant_bg": "#f0f0f0", "assistant_text": "#1a1a1a",
            "assistant_label": "#4a4a4a",
            "error_bg": "#fde8e8", "error_text": "#b71c1c",
            "error_label": "#c62828",
            "code_block_bg": "#282c34", "code_block_text": "#abb2bf",
            "inline_code_bg": "#e8e8e8", "inline_code_text": "#1a1a1a",
            "link_color": "#1565c0",
            "thinking_bg": "#f5f5f5", "thinking_text": "#888888",
            "thinking_border": "#cccccc",
            "scroll_btn_bg": "rgba(0, 0, 0, 150)",
            "scroll_btn_text": "#ffffff",
            "blockquote_bg": "#f5f5f0", "blockquote_border": "#c0c0c0",
            "blockquote_text": "#555555",
            "table_border": "#cccccc", "table_header_bg": "#e0e0e0",
            "hr_color": "#cccccc",
        },
    },
    "solarized": {
        "dark": {
            "user_bg": "#073642", "user_text": "#eee8d5",
            "user_label": "#268bd2",
            "assistant_bg": "#002b36", "assistant_text": "#fdf6e3",
            "assistant_label": "#839496",
            "error_bg": "#3b1418", "error_text": "#dc322f",
            "error_label": "#cb4b16",
            "code_block_bg": "#002b36", "code_block_text": "#93a1a1",
            "inline_code_bg": "#073642", "inline_code_text": "#eee8d5",
            "link_color": "#2aa198",
            "thinking_bg": "#002b36", "thinking_text": "#657b83",
            "thinking_border": "#586e75",
            "scroll_btn_bg": "rgba(238, 232, 213, 150)",
            "scroll_btn_text": "#002b36",
            "blockquote_bg": "#073642", "blockquote_border": "#586e75",
            "blockquote_text": "#93a1a1",
            "table_border": "#586e75", "table_header_bg": "#073642",
            "hr_color": "#586e75",
        },
        "light": {
            "user_bg": "#eee8d5", "user_text": "#002b36",
            "user_label": "#268bd2",
            "assistant_bg": "#fdf6e3", "assistant_text": "#073642",
            "assistant_label": "#657b83",
            "error_bg": "#fde8e8", "error_text": "#dc322f",
            "error_label": "#cb4b16",
            "code_block_bg": "#002b36", "code_block_text": "#93a1a1",
            "inline_code_bg": "#eee8d5", "inline_code_text": "#073642",
            "link_color": "#2aa198",
            "thinking_bg": "#eee8d5", "thinking_text": "#93a1a1",
            "thinking_border": "#93a1a1",
            "scroll_btn_bg": "rgba(0, 43, 54, 150)",
            "scroll_btn_text": "#fdf6e3",
            "blockquote_bg": "#eee8d5", "blockquote_border": "#93a1a1",
            "blockquote_text": "#586e75",
            "table_border": "#93a1a1", "table_header_bg": "#eee8d5",
            "hr_color": "#93a1a1",
        },
    },
    "nord": {
        "dark": {
            "user_bg": "#3b4252", "user_text": "#eceff4",
            "user_label": "#88c0d0",
            "assistant_bg": "#2e3440", "assistant_text": "#d8dee9",
            "assistant_label": "#81a1c1",
            "error_bg": "#3b2028", "error_text": "#bf616a",
            "error_label": "#d08770",
            "code_block_bg": "#2e3440", "code_block_text": "#d8dee9",
            "inline_code_bg": "#3b4252", "inline_code_text": "#eceff4",
            "link_color": "#88c0d0",
            "thinking_bg": "#2e3440", "thinking_text": "#4c566a",
            "thinking_border": "#4c566a",
            "scroll_btn_bg": "rgba(216, 222, 233, 150)",
            "scroll_btn_text": "#2e3440",
            "blockquote_bg": "#3b4252", "blockquote_border": "#4c566a",
            "blockquote_text": "#d8dee9",
            "table_border": "#4c566a", "table_header_bg": "#3b4252",
            "hr_color": "#4c566a",
        },
        "light": {
            "user_bg": "#d8dee9", "user_text": "#2e3440",
            "user_label": "#5e81ac",
            "assistant_bg": "#eceff4", "assistant_text": "#2e3440",
            "assistant_label": "#4c566a",
            "error_bg": "#f0d5d8", "error_text": "#bf616a",
            "error_label": "#d08770",
            "code_block_bg": "#2e3440", "code_block_text": "#d8dee9",
            "inline_code_bg": "#d8dee9", "inline_code_text": "#2e3440",
            "link_color": "#5e81ac",
            "thinking_bg": "#d8dee9", "thinking_text": "#4c566a",
            "thinking_border": "#b8c5d6",
            "scroll_btn_bg": "rgba(46, 52, 64, 150)",
            "scroll_btn_text": "#eceff4",
            "blockquote_bg": "#d8dee9", "blockquote_border": "#b8c5d6",
            "blockquote_text": "#4c566a",
            "table_border": "#b8c5d6", "table_header_bg": "#d8dee9",
            "hr_color": "#b8c5d6",
        },
    },
    "dracula": {
        "dark": {
            "user_bg": "#44475a", "user_text": "#f8f8f2",
            "user_label": "#8be9fd",
            "assistant_bg": "#282a36", "assistant_text": "#f8f8f2",
            "assistant_label": "#6272a4",
            "error_bg": "#3b1c2a", "error_text": "#ff5555",
            "error_label": "#ff79c6",
            "code_block_bg": "#282a36", "code_block_text": "#f8f8f2",
            "inline_code_bg": "#44475a", "inline_code_text": "#f8f8f2",
            "link_color": "#bd93f9",
            "thinking_bg": "#21222c", "thinking_text": "#6272a4",
            "thinking_border": "#6272a4",
            "scroll_btn_bg": "rgba(248, 248, 242, 150)",
            "scroll_btn_text": "#282a36",
            "blockquote_bg": "#44475a", "blockquote_border": "#6272a4",
            "blockquote_text": "#f8f8f2",
            "table_border": "#6272a4", "table_header_bg": "#44475a",
            "hr_color": "#6272a4",
        },
        "light": {
            "user_bg": "#e8e6f0", "user_text": "#282a36",
            "user_label": "#7c3aed",
            "assistant_bg": "#f8f8f2", "assistant_text": "#282a36",
            "assistant_label": "#6272a4",
            "error_bg": "#fde8e8", "error_text": "#ff5555",
            "error_label": "#ff79c6",
            "code_block_bg": "#282a36", "code_block_text": "#f8f8f2",
            "inline_code_bg": "#e8e6f0", "inline_code_text": "#282a36",
            "link_color": "#7c3aed",
            "thinking_bg": "#e8e6f0", "thinking_text": "#6272a4",
            "thinking_border": "#b8b5c8",
            "scroll_btn_bg": "rgba(40, 42, 54, 150)",
            "scroll_btn_text": "#f8f8f2",
            "blockquote_bg": "#e8e6f0", "blockquote_border": "#b8b5c8",
            "blockquote_text": "#44475a",
            "table_border": "#b8b5c8", "table_header_bg": "#e8e6f0",
            "hr_color": "#b8b5c8",
        },
    },
    "gruvbox": {
        "dark": {
            "user_bg": "#3c3836", "user_text": "#ebdbb2",
            "user_label": "#83a598",
            "assistant_bg": "#282828", "assistant_text": "#ebdbb2",
            "assistant_label": "#a89984",
            "error_bg": "#3c1f1f", "error_text": "#fb4934",
            "error_label": "#fe8019",
            "code_block_bg": "#1d2021", "code_block_text": "#ebdbb2",
            "inline_code_bg": "#3c3836", "inline_code_text": "#ebdbb2",
            "link_color": "#83a598",
            "thinking_bg": "#1d2021", "thinking_text": "#928374",
            "thinking_border": "#665c54",
            "scroll_btn_bg": "rgba(235, 219, 178, 150)",
            "scroll_btn_text": "#282828",
            "blockquote_bg": "#3c3836", "blockquote_border": "#665c54",
            "blockquote_text": "#d5c4a1",
            "table_border": "#665c54", "table_header_bg": "#3c3836",
            "hr_color": "#665c54",
        },
        "light": {
            "user_bg": "#ebdbb2", "user_text": "#282828",
            "user_label": "#427b58",
            "assistant_bg": "#fbf1c7", "assistant_text": "#3c3836",
            "assistant_label": "#7c6f64",
            "error_bg": "#f9e0e0", "error_text": "#9d0006",
            "error_label": "#af3a03",
            "code_block_bg": "#282828", "code_block_text": "#ebdbb2",
            "inline_code_bg": "#ebdbb2", "inline_code_text": "#282828",
            "link_color": "#427b58",
            "thinking_bg": "#ebdbb2", "thinking_text": "#928374",
            "thinking_border": "#a89984",
            "scroll_btn_bg": "rgba(40, 40, 40, 150)",
            "scroll_btn_text": "#fbf1c7",
            "blockquote_bg": "#ebdbb2", "blockquote_border": "#a89984",
            "blockquote_text": "#504945",
            "table_border": "#a89984", "table_header_bg": "#ebdbb2",
            "hr_color": "#a89984",
        },
    },
    "monokai": {
        "dark": {
            "user_bg": "#3e3d32", "user_text": "#f8f8f2",
            "user_label": "#66d9ef",
            "assistant_bg": "#272822", "assistant_text": "#f8f8f2",
            "assistant_label": "#75715e",
            "error_bg": "#3b1c1c", "error_text": "#f92672",
            "error_label": "#fd971f",
            "code_block_bg": "#272822", "code_block_text": "#f8f8f2",
            "inline_code_bg": "#3e3d32", "inline_code_text": "#f8f8f2",
            "link_color": "#a6e22e",
            "thinking_bg": "#1e1f1c", "thinking_text": "#75715e",
            "thinking_border": "#75715e",
            "scroll_btn_bg": "rgba(248, 248, 242, 150)",
            "scroll_btn_text": "#272822",
            "blockquote_bg": "#3e3d32", "blockquote_border": "#75715e",
            "blockquote_text": "#f8f8f2",
            "table_border": "#75715e", "table_header_bg": "#3e3d32",
            "hr_color": "#75715e",
        },
        "light": {
            "user_bg": "#e8e8e0", "user_text": "#272822",
            "user_label": "#0077aa",
            "assistant_bg": "#fafaf5", "assistant_text": "#272822",
            "assistant_label": "#75715e",
            "error_bg": "#fde8e8", "error_text": "#f92672",
            "error_label": "#fd971f",
            "code_block_bg": "#272822", "code_block_text": "#f8f8f2",
            "inline_code_bg": "#e8e8e0", "inline_code_text": "#272822",
            "link_color": "#0077aa",
            "thinking_bg": "#e8e8e0", "thinking_text": "#75715e",
            "thinking_border": "#b8b8b0",
            "scroll_btn_bg": "rgba(39, 40, 34, 150)",
            "scroll_btn_text": "#fafaf5",
            "blockquote_bg": "#e8e8e0", "blockquote_border": "#b8b8b0",
            "blockquote_text": "#49483e",
            "table_border": "#b8b8b0", "table_header_bg": "#e8e8e0",
            "hr_color": "#b8b8b0",
        },
    },
}


def get_preset_names():
    """Return the list of available theme preset names."""
    return list(THEME_PRESETS.keys())


def get_theme_colors(preset_name, is_dark, overrides=None):
    """Resolve the final theme color dict for a given preset and mode.

    Starts from the preset's dark or light variant, then applies any
    per-key color overrides on top. Unknown keys in overrides are ignored.

    Args:
        preset_name: Name of the preset (e.g., "default", "nord").
            Falls back to "default" if the name is not found.
        is_dark: True for dark variant, False for light.
        overrides: Optional dict of color key → hex value overrides.

    Returns:
        A complete theme color dict (all 23 keys).
    """
    preset = THEME_PRESETS.get(preset_name, THEME_PRESETS["default"])
    variant = "dark" if is_dark else "light"
    # Start with a copy of the preset variant
    colors = dict(preset[variant])

    # Apply user overrides
    if overrides:
        for key, value in overrides.items():
            if key in colors and value:
                colors[key] = value

    return colors


def parse_color_overrides(json_str):
    """Parse a JSON string of color overrides into a dict.

    Returns an empty dict on any parse error. Filters out keys that
    are not valid theme color keys.

    Args:
        json_str: JSON string like '{"user_bg": "#ff0000"}'.

    Returns:
        Dict of color key → hex value.
    """
    if not json_str or not isinstance(json_str, str):
        return {}
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, ValueError):
        logger.debug("Failed to parse theme color overrides: %r", json_str)
        return {}
    if not isinstance(data, dict):
        return {}
    valid_keys = set(THEME_COLOR_KEYS)
    return {k: v for k, v in data.items() if k in valid_keys and isinstance(v, str)}


def serialize_color_overrides(overrides):
    """Serialize a color overrides dict to a JSON string for config storage.

    Args:
        overrides: Dict of color key → hex value.

    Returns:
        JSON string. Empty overrides produce "{}".
    """
    if not overrides:
        return "{}"
    return json.dumps(overrides, separators=(",", ":"))
