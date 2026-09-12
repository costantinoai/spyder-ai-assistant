"""Shared assistant settings defaults and normalization helpers."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from spyder_ai_assistant.mcp.settings import (
    DEFAULT_MCP_HOST,
    DEFAULT_MCP_PORT,
    normalize_mcp_host,
    normalize_mcp_port,
)
from spyder_ai_assistant.utils.coerce import bounded_int
from spyder_ai_assistant.utils.constants import DEFAULT_OLLAMA_HOST
from spyder_ai_assistant.utils.chat_inference import (
    DEFAULT_CHAT_MAX_TOKENS,
    MAX_CHAT_MAX_TOKENS,
    MAX_CHAT_TEMPERATURE,
    MIN_CHAT_MAX_TOKENS,
    MIN_CHAT_TEMPERATURE,
    normalize_chat_max_tokens,
    normalize_chat_temperature,
)
from spyder_ai_assistant.utils.provider_profiles import (
    PROVIDER_KIND_OLLAMA,
    normalize_provider_profiles,
    serialize_provider_profiles,
)


# DEFAULT_OLLAMA_HOST is imported above from utils.constants and re-exported
# here, so the callers that already import settings keep working.
DEFAULT_CHAT_MODEL = "qwen3-coder-next"
DEFAULT_COMPLETION_MODEL = "qwen3-coder-next"
DEFAULT_COMPLETION_TEMPERATURE = 0.15
DEFAULT_COMPLETION_MAX_TOKENS = 256
DEFAULT_COMPLETIONS_ENABLED = True
DEFAULT_DEBOUNCE_MS = 300
DEFAULT_COMPLETION_SHORTCUT = "Ctrl+Shift+Space"
DEFAULT_COMPLETION_ACCEPT_WORD_SHORTCUT = "Alt+Right"
DEFAULT_COMPLETION_ACCEPT_LINE_SHORTCUT = "Alt+Shift+Right"

# How inline AI suggestions share the editor with Spyder's own completion
# popup (pylsp, fallback, snippets). Only *automatic* popups (the ones Spyder
# opens by itself after "." or a few characters) are affected; an explicit
# Ctrl+Space popup always wins, whatever the policy.
NATIVE_POPUP_POLICY_AI_FIRST = "ai_first"
NATIVE_POPUP_POLICY_AI_REPLACES = "ai_replaces"
NATIVE_POPUP_POLICY_NATIVE_FIRST = "native_first"
NATIVE_POPUP_POLICIES = (
    NATIVE_POPUP_POLICY_AI_FIRST,
    NATIVE_POPUP_POLICY_AI_REPLACES,
    NATIVE_POPUP_POLICY_NATIVE_FIRST,
)
NATIVE_POPUP_POLICY_LABELS = {
    NATIVE_POPUP_POLICY_AI_FIRST: "AI suggestions only (Spyder popup on Ctrl+Space)",
    NATIVE_POPUP_POLICY_AI_REPLACES: "Show Spyder popup, replace it when the AI answers",
    NATIVE_POPUP_POLICY_NATIVE_FIRST: "Spyder popup first (AI waits until it closes)",
}
NATIVE_POPUP_POLICY_DESCRIPTIONS = {
    NATIVE_POPUP_POLICY_AI_FIRST: (
        "While AI completions are on and the model is available, Spyder's "
        "automatic completion popup stays hidden and ghost text is the only "
        "automatic suggestion. Press Ctrl+Space for the Spyder popup."
    ),
    NATIVE_POPUP_POLICY_AI_REPLACES: (
        "Spyder's automatic popup opens as usual while the AI is thinking; "
        "the ghost suggestion closes it when it arrives."
    ),
    NATIVE_POPUP_POLICY_NATIVE_FIRST: (
        "Spyder's automatic popup keeps priority: an AI suggestion arriving "
        "while it is open is dropped, and a visible ghost only blocks new "
        "automatic popups."
    ),
}
DEFAULT_NATIVE_POPUP_POLICY = NATIVE_POPUP_POLICY_AI_FIRST
DEFAULT_CHAT_SYSTEM_PROMPT = (
    "You are a helpful AI coding assistant working inside "
    "the Spyder IDE. Be concise and provide code examples "
    "when relevant."
)
DEFAULT_PROMPT_EXPLAIN = "Explain this code from {filename}:\n\n```\n{code}\n```"
DEFAULT_PROMPT_FIX = (
    "Find and fix bugs in this code from {filename}:\n\n```\n{code}\n```"
)
DEFAULT_PROMPT_DOCSTRING = (
    "Add a docstring to this code from {filename}:\n\n```\n{code}\n```"
)
DEFAULT_PROMPT_ASK = (
    "Regarding this code from {filename}:\n\n```\n{code}\n```\n\n"
)
DEFAULT_CHAT_FONT_FAMILY = "sans-serif"
DEFAULT_CHAT_FONT_SIZE = 10
DEFAULT_CHAT_LINE_HEIGHT = 1.5
DEFAULT_CODE_FONT_FAMILY = "Courier New"
DEFAULT_CODE_FONT_SIZE = 9
DEFAULT_PYGMENTS_STYLE_DARK = "monokai"
DEFAULT_PYGMENTS_STYLE_LIGHT = "default"
DEFAULT_BUBBLE_PADDING = 12
DEFAULT_BUBBLE_BORDER_RADIUS = 8
DEFAULT_BUBBLE_SPACING = 4
DEFAULT_THEME_PRESET = "default"
DEFAULT_THEME_COLOR_OVERRIDES = "{}"
DEFAULT_IDLE_COMPLETION_DELAY_MS = 1000
DEFAULT_POST_ACCEPT_COMPLETION_DELAY_MS = 75
DEFAULT_PROVIDER_PROFILES = "[]"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = ""
DEFAULT_OPENAI_COMPATIBLE_API_KEY = ""


ASSISTANT_CONF_DEFAULTS = {
    "ollama_host": DEFAULT_OLLAMA_HOST,
    "chat_provider": PROVIDER_KIND_OLLAMA,
    "chat_provider_profile_id": "",
    "chat_model": DEFAULT_CHAT_MODEL,
    "provider_profiles": DEFAULT_PROVIDER_PROFILES,
    "openai_compatible_base_url": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    "openai_compatible_api_key": DEFAULT_OPENAI_COMPATIBLE_API_KEY,
    "completion_model": DEFAULT_COMPLETION_MODEL,
    "mcp_enabled": True,
    "project_tools_enabled": True,
    "mcp_host": DEFAULT_MCP_HOST,
    "mcp_port": DEFAULT_MCP_PORT,
    # Stored as "temperature x10" for the current preferences UI.
    "chat_temperature": 5,
    "completion_temperature": DEFAULT_COMPLETION_TEMPERATURE,
    "max_tokens": DEFAULT_CHAT_MAX_TOKENS,
    "completion_max_tokens": DEFAULT_COMPLETION_MAX_TOKENS,
    "completions_enabled": DEFAULT_COMPLETIONS_ENABLED,
    # Suggestions only when asked for (GitHub issue #4). Deliberately not in
    # GHOST_TEXT_OPTION_KEYS: that tuple's handler routes anything it does
    # not recognise into update_timing(), which expects milliseconds.
    "completion_manual_only": False,
    "completion_shortcut": DEFAULT_COMPLETION_SHORTCUT,
    "completion_accept_word_shortcut": DEFAULT_COMPLETION_ACCEPT_WORD_SHORTCUT,
    "completion_accept_line_shortcut": DEFAULT_COMPLETION_ACCEPT_LINE_SHORTCUT,
    "chat_system_prompt": DEFAULT_CHAT_SYSTEM_PROMPT,
    "prompt_explain": DEFAULT_PROMPT_EXPLAIN,
    "prompt_fix": DEFAULT_PROMPT_FIX,
    "prompt_docstring": DEFAULT_PROMPT_DOCSTRING,
    "prompt_ask": DEFAULT_PROMPT_ASK,
    "chat_font_family": DEFAULT_CHAT_FONT_FAMILY,
    "chat_font_size": DEFAULT_CHAT_FONT_SIZE,
    "chat_line_height": DEFAULT_CHAT_LINE_HEIGHT,
    "code_font_family": DEFAULT_CODE_FONT_FAMILY,
    "code_font_size": DEFAULT_CODE_FONT_SIZE,
    "pygments_style_dark": DEFAULT_PYGMENTS_STYLE_DARK,
    "pygments_style_light": DEFAULT_PYGMENTS_STYLE_LIGHT,
    "bubble_padding": DEFAULT_BUBBLE_PADDING,
    "bubble_border_radius": DEFAULT_BUBBLE_BORDER_RADIUS,
    "bubble_spacing": DEFAULT_BUBBLE_SPACING,
    "theme_preset": DEFAULT_THEME_PRESET,
    "theme_color_overrides": DEFAULT_THEME_COLOR_OVERRIDES,
    "debounce_ms": DEFAULT_DEBOUNCE_MS,
    "idle_completion_delay_ms": DEFAULT_IDLE_COMPLETION_DELAY_MS,
    "post_accept_completion_delay_ms": DEFAULT_POST_ACCEPT_COMPLETION_DELAY_MS,
    "native_popup_policy": DEFAULT_NATIVE_POPUP_POLICY,
}

# Numeric bounds for the options the settings dialog edits with spin boxes.
# One owner for the clamps applied in ``from_mapping`` and the ranges the
# dialog gives its widgets, which previously disagreed silently whenever one
# side was corrected. Values are ``(minimum, maximum)``.
ASSISTANT_OPTION_RANGES = {
    "chat_temperature": (MIN_CHAT_TEMPERATURE, MAX_CHAT_TEMPERATURE),
    "max_tokens": (MIN_CHAT_MAX_TOKENS, MAX_CHAT_MAX_TOKENS),
    "completion_temperature": (0.0, 2.0),
    "completion_max_tokens": (16, 4096),
    "debounce_ms": (0, 5000),
    "chat_font_size": (6, 24),
    "chat_line_height": (1.0, 3.0),
    "code_font_size": (6, 24),
    "bubble_padding": (4, 32),
    "bubble_border_radius": (0, 24),
    "bubble_spacing": (0, 16),
    "idle_completion_delay_ms": (100, 5000),
    "post_accept_completion_delay_ms": (0, 1000),
    # Enforced by ``normalize_mcp_port``; repeated here for the dialog widget.
    "mcp_port": (1, 65535),
}

# Options every editor's ghost text manager reads directly (the plugin pushes
# changes to all managers; see ``on_ghost_option_changed``).
GHOST_TEXT_OPTION_KEYS = (
    "idle_completion_delay_ms",
    "post_accept_completion_delay_ms",
    "native_popup_policy",
)

COMPLETION_PROVIDER_CONF_DEFAULTS = [
    ("ollama_host", ASSISTANT_CONF_DEFAULTS["ollama_host"]),
    ("chat_provider", ASSISTANT_CONF_DEFAULTS["chat_provider"]),
    ("chat_model", ASSISTANT_CONF_DEFAULTS["chat_model"]),
    ("chat_provider_profile_id", ASSISTANT_CONF_DEFAULTS["chat_provider_profile_id"]),
    ("provider_profiles", ASSISTANT_CONF_DEFAULTS["provider_profiles"]),
    (
        "openai_compatible_base_url",
        ASSISTANT_CONF_DEFAULTS["openai_compatible_base_url"],
    ),
    (
        "openai_compatible_api_key",
        ASSISTANT_CONF_DEFAULTS["openai_compatible_api_key"],
    ),
    ("completion_model", ASSISTANT_CONF_DEFAULTS["completion_model"]),
    (
        "completion_temperature",
        ASSISTANT_CONF_DEFAULTS["completion_temperature"],
    ),
    (
        "completion_max_tokens",
        ASSISTANT_CONF_DEFAULTS["completion_max_tokens"],
    ),
    ("completions_enabled", ASSISTANT_CONF_DEFAULTS["completions_enabled"]),
    (
        "completion_manual_only",
        ASSISTANT_CONF_DEFAULTS["completion_manual_only"],
    ),
    ("project_tools_enabled", ASSISTANT_CONF_DEFAULTS["project_tools_enabled"]),
    ("debounce_ms", ASSISTANT_CONF_DEFAULTS["debounce_ms"]),
]

ASSISTANT_APPEARANCE_KEYS = (
    "chat_font_family",
    "chat_font_size",
    "chat_line_height",
    "code_font_family",
    "code_font_size",
    "pygments_style_dark",
    "pygments_style_light",
    "bubble_padding",
    "bubble_border_radius",
    "bubble_spacing",
    "theme_preset",
    "theme_color_overrides",
)


def _normalize_string(value, default="", *, default_on_blank=False):
    """Return one normalized string setting."""
    if value is None:
        return str(default or "")
    normalized = str(value)
    if default_on_blank and not normalized.strip():
        return str(default or "")
    return normalized


def _normalize_choice(value, choices, default):
    """Return ``value`` when it is one of ``choices``, else ``default``."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in choices else default


def _normalize_bool(value, default=False):
    """Return one normalized boolean setting."""
    if value is None:
        return bool(default)
    return bool(value)


def _normalize_float(value, default, minimum=None, maximum=None, precision=2):
    """Return one normalized floating-point setting."""
    try:
        normalized = float(value)
    except (TypeError, ValueError):
        normalized = float(default)

    if minimum is not None:
        normalized = max(float(minimum), normalized)
    if maximum is not None:
        normalized = min(float(maximum), normalized)
    return round(normalized, int(precision))


def _normalize_chat_temperature_conf(value):
    """Return the canonical stored config representation for chat temperature."""
    return int(round(normalize_chat_temperature(value) * 10))


def _normalize_provider_profiles_value(raw_profiles):
    """Return one safe config-stored provider profile payload."""
    if isinstance(raw_profiles, str):
        try:
            loaded = json.loads(raw_profiles or DEFAULT_PROVIDER_PROFILES)
        except Exception:
            return DEFAULT_PROVIDER_PROFILES
        if not isinstance(loaded, list):
            return DEFAULT_PROVIDER_PROFILES
        return raw_profiles or DEFAULT_PROVIDER_PROFILES

    normalized = normalize_provider_profiles(
        raw_profiles,
        legacy_base_url="",
        legacy_api_key="",
    )
    if not normalized:
        return DEFAULT_PROVIDER_PROFILES
    return serialize_provider_profiles(normalized)


@dataclass(frozen=True, slots=True)
class AssistantSettings:
    """Canonical normalized assistant settings snapshot."""

    ollama_host: str = DEFAULT_OLLAMA_HOST
    chat_provider: str = PROVIDER_KIND_OLLAMA
    chat_provider_profile_id: str = ""
    chat_model: str = DEFAULT_CHAT_MODEL
    provider_profiles: str = DEFAULT_PROVIDER_PROFILES
    openai_compatible_base_url: str = DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    openai_compatible_api_key: str = DEFAULT_OPENAI_COMPATIBLE_API_KEY
    completion_model: str = DEFAULT_COMPLETION_MODEL
    mcp_enabled: bool = True
    project_tools_enabled: bool = True
    mcp_host: str = DEFAULT_MCP_HOST
    mcp_port: int = DEFAULT_MCP_PORT
    chat_temperature: int = 5
    completion_temperature: float = DEFAULT_COMPLETION_TEMPERATURE
    max_tokens: int = DEFAULT_CHAT_MAX_TOKENS
    completion_max_tokens: int = DEFAULT_COMPLETION_MAX_TOKENS
    completions_enabled: bool = DEFAULT_COMPLETIONS_ENABLED
    completion_manual_only: bool = False
    completion_shortcut: str = DEFAULT_COMPLETION_SHORTCUT
    completion_accept_word_shortcut: str = DEFAULT_COMPLETION_ACCEPT_WORD_SHORTCUT
    completion_accept_line_shortcut: str = DEFAULT_COMPLETION_ACCEPT_LINE_SHORTCUT
    chat_system_prompt: str = DEFAULT_CHAT_SYSTEM_PROMPT
    prompt_explain: str = DEFAULT_PROMPT_EXPLAIN
    prompt_fix: str = DEFAULT_PROMPT_FIX
    prompt_docstring: str = DEFAULT_PROMPT_DOCSTRING
    prompt_ask: str = DEFAULT_PROMPT_ASK
    chat_font_family: str = DEFAULT_CHAT_FONT_FAMILY
    chat_font_size: int = DEFAULT_CHAT_FONT_SIZE
    chat_line_height: float = DEFAULT_CHAT_LINE_HEIGHT
    code_font_family: str = DEFAULT_CODE_FONT_FAMILY
    code_font_size: int = DEFAULT_CODE_FONT_SIZE
    pygments_style_dark: str = DEFAULT_PYGMENTS_STYLE_DARK
    pygments_style_light: str = DEFAULT_PYGMENTS_STYLE_LIGHT
    bubble_padding: int = DEFAULT_BUBBLE_PADDING
    bubble_border_radius: int = DEFAULT_BUBBLE_BORDER_RADIUS
    bubble_spacing: int = DEFAULT_BUBBLE_SPACING
    theme_preset: str = DEFAULT_THEME_PRESET
    theme_color_overrides: str = DEFAULT_THEME_COLOR_OVERRIDES
    debounce_ms: int = DEFAULT_DEBOUNCE_MS
    idle_completion_delay_ms: int = DEFAULT_IDLE_COMPLETION_DELAY_MS
    native_popup_policy: str = DEFAULT_NATIVE_POPUP_POLICY
    post_accept_completion_delay_ms: int = DEFAULT_POST_ACCEPT_COMPLETION_DELAY_MS

    @classmethod
    def from_mapping(cls, values=None):
        """Build one canonical settings snapshot from a dict-like payload."""
        if isinstance(values, cls):
            return values

        values = dict(values or {})
        return cls(
            ollama_host=_normalize_string(
                values.get("ollama_host", DEFAULT_OLLAMA_HOST),
                DEFAULT_OLLAMA_HOST,
                default_on_blank=True,
            ),
            chat_provider=_normalize_string(
                values.get("chat_provider", PROVIDER_KIND_OLLAMA),
                PROVIDER_KIND_OLLAMA,
                default_on_blank=True,
            ),
            chat_provider_profile_id=_normalize_string(
                values.get("chat_provider_profile_id", ""),
                "",
            ).strip(),
            chat_model=_normalize_string(
                values.get("chat_model", DEFAULT_CHAT_MODEL),
                DEFAULT_CHAT_MODEL,
                default_on_blank=True,
            ),
            provider_profiles=_normalize_provider_profiles_value(
                values.get("provider_profiles", DEFAULT_PROVIDER_PROFILES)
            ),
            openai_compatible_base_url=_normalize_string(
                values.get(
                    "openai_compatible_base_url",
                    DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
                ),
                DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
            ).strip(),
            openai_compatible_api_key=_normalize_string(
                values.get(
                    "openai_compatible_api_key",
                    DEFAULT_OPENAI_COMPATIBLE_API_KEY,
                ),
                DEFAULT_OPENAI_COMPATIBLE_API_KEY,
            ),
            completion_model=_normalize_string(
                values.get("completion_model", DEFAULT_COMPLETION_MODEL),
                DEFAULT_COMPLETION_MODEL,
                default_on_blank=True,
            ),
            project_tools_enabled=_normalize_bool(
                values.get("project_tools_enabled", True),
            ),
            native_popup_policy=_normalize_choice(
                values.get("native_popup_policy", DEFAULT_NATIVE_POPUP_POLICY),
                NATIVE_POPUP_POLICIES,
                DEFAULT_NATIVE_POPUP_POLICY,
            ),
            mcp_enabled=_normalize_bool(
                values.get("mcp_enabled", True),
                True,
            ),
            mcp_host=normalize_mcp_host(
                values.get("mcp_host", DEFAULT_MCP_HOST)
            ),
            mcp_port=normalize_mcp_port(
                values.get("mcp_port", DEFAULT_MCP_PORT)
            ),
            chat_temperature=_normalize_chat_temperature_conf(
                values.get("chat_temperature", 5)
            ),
            completion_temperature=_normalize_float(
                values.get(
                    "completion_temperature",
                    DEFAULT_COMPLETION_TEMPERATURE,
                ),
                DEFAULT_COMPLETION_TEMPERATURE,
                *ASSISTANT_OPTION_RANGES["completion_temperature"],
            ),
            max_tokens=normalize_chat_max_tokens(
                values.get("max_tokens", DEFAULT_CHAT_MAX_TOKENS)
            ),
            completion_max_tokens=bounded_int(
                values.get(
                    "completion_max_tokens",
                    DEFAULT_COMPLETION_MAX_TOKENS,
                ),
                DEFAULT_COMPLETION_MAX_TOKENS,
                *ASSISTANT_OPTION_RANGES["completion_max_tokens"],
            ),
            completions_enabled=_normalize_bool(
                values.get("completions_enabled", DEFAULT_COMPLETIONS_ENABLED),
                DEFAULT_COMPLETIONS_ENABLED,
            ),
            completion_manual_only=_normalize_bool(
                values.get("completion_manual_only", False),
                False,
            ),
            completion_shortcut=_normalize_string(
                values.get("completion_shortcut", DEFAULT_COMPLETION_SHORTCUT),
                DEFAULT_COMPLETION_SHORTCUT,
            ).strip(),
            completion_accept_word_shortcut=_normalize_string(
                values.get(
                    "completion_accept_word_shortcut",
                    DEFAULT_COMPLETION_ACCEPT_WORD_SHORTCUT,
                ),
                DEFAULT_COMPLETION_ACCEPT_WORD_SHORTCUT,
            ).strip(),
            completion_accept_line_shortcut=_normalize_string(
                values.get(
                    "completion_accept_line_shortcut",
                    DEFAULT_COMPLETION_ACCEPT_LINE_SHORTCUT,
                ),
                DEFAULT_COMPLETION_ACCEPT_LINE_SHORTCUT,
            ).strip(),
            chat_system_prompt=_normalize_string(
                values.get("chat_system_prompt", DEFAULT_CHAT_SYSTEM_PROMPT),
                DEFAULT_CHAT_SYSTEM_PROMPT,
            ),
            prompt_explain=_normalize_string(
                values.get("prompt_explain", DEFAULT_PROMPT_EXPLAIN),
                DEFAULT_PROMPT_EXPLAIN,
            ),
            prompt_fix=_normalize_string(
                values.get("prompt_fix", DEFAULT_PROMPT_FIX),
                DEFAULT_PROMPT_FIX,
            ),
            prompt_docstring=_normalize_string(
                values.get("prompt_docstring", DEFAULT_PROMPT_DOCSTRING),
                DEFAULT_PROMPT_DOCSTRING,
            ),
            prompt_ask=_normalize_string(
                values.get("prompt_ask", DEFAULT_PROMPT_ASK),
                DEFAULT_PROMPT_ASK,
            ),
            chat_font_family=_normalize_string(
                values.get("chat_font_family", DEFAULT_CHAT_FONT_FAMILY),
                DEFAULT_CHAT_FONT_FAMILY,
                default_on_blank=True,
            ),
            chat_font_size=bounded_int(
                values.get("chat_font_size", DEFAULT_CHAT_FONT_SIZE),
                DEFAULT_CHAT_FONT_SIZE,
                *ASSISTANT_OPTION_RANGES["chat_font_size"],
            ),
            chat_line_height=_normalize_float(
                values.get("chat_line_height", DEFAULT_CHAT_LINE_HEIGHT),
                DEFAULT_CHAT_LINE_HEIGHT,
                *ASSISTANT_OPTION_RANGES["chat_line_height"],
                precision=1,
            ),
            code_font_family=_normalize_string(
                values.get("code_font_family", DEFAULT_CODE_FONT_FAMILY),
                DEFAULT_CODE_FONT_FAMILY,
                default_on_blank=True,
            ),
            code_font_size=bounded_int(
                values.get("code_font_size", DEFAULT_CODE_FONT_SIZE),
                DEFAULT_CODE_FONT_SIZE,
                *ASSISTANT_OPTION_RANGES["code_font_size"],
            ),
            pygments_style_dark=_normalize_string(
                values.get("pygments_style_dark", DEFAULT_PYGMENTS_STYLE_DARK),
                DEFAULT_PYGMENTS_STYLE_DARK,
                default_on_blank=True,
            ),
            pygments_style_light=_normalize_string(
                values.get("pygments_style_light", DEFAULT_PYGMENTS_STYLE_LIGHT),
                DEFAULT_PYGMENTS_STYLE_LIGHT,
                default_on_blank=True,
            ),
            bubble_padding=bounded_int(
                values.get("bubble_padding", DEFAULT_BUBBLE_PADDING),
                DEFAULT_BUBBLE_PADDING,
                *ASSISTANT_OPTION_RANGES["bubble_padding"],
            ),
            bubble_border_radius=bounded_int(
                values.get("bubble_border_radius", DEFAULT_BUBBLE_BORDER_RADIUS),
                DEFAULT_BUBBLE_BORDER_RADIUS,
                *ASSISTANT_OPTION_RANGES["bubble_border_radius"],
            ),
            bubble_spacing=bounded_int(
                values.get("bubble_spacing", DEFAULT_BUBBLE_SPACING),
                DEFAULT_BUBBLE_SPACING,
                *ASSISTANT_OPTION_RANGES["bubble_spacing"],
            ),
            theme_preset=_normalize_string(
                values.get("theme_preset", DEFAULT_THEME_PRESET),
                DEFAULT_THEME_PRESET,
                default_on_blank=True,
            ),
            theme_color_overrides=_normalize_string(
                values.get("theme_color_overrides", DEFAULT_THEME_COLOR_OVERRIDES),
                DEFAULT_THEME_COLOR_OVERRIDES,
                default_on_blank=True,
            ),
            debounce_ms=bounded_int(
                values.get("debounce_ms", DEFAULT_DEBOUNCE_MS),
                DEFAULT_DEBOUNCE_MS,
                *ASSISTANT_OPTION_RANGES["debounce_ms"],
            ),
            idle_completion_delay_ms=bounded_int(
                values.get(
                    "idle_completion_delay_ms",
                    DEFAULT_IDLE_COMPLETION_DELAY_MS,
                ),
                DEFAULT_IDLE_COMPLETION_DELAY_MS,
                *ASSISTANT_OPTION_RANGES["idle_completion_delay_ms"],
            ),
            post_accept_completion_delay_ms=bounded_int(
                values.get(
                    "post_accept_completion_delay_ms",
                    DEFAULT_POST_ACCEPT_COMPLETION_DELAY_MS,
                ),
                DEFAULT_POST_ACCEPT_COMPLETION_DELAY_MS,
                *ASSISTANT_OPTION_RANGES["post_accept_completion_delay_ms"],
            ),
        )

    @classmethod
    def from_conf(cls, get_conf):
        """Build one canonical settings snapshot from a Spyder config getter."""
        values = {}
        for key, default in ASSISTANT_CONF_DEFAULTS.items():
            try:
                values[key] = get_conf(key, default=default)
            except TypeError:
                try:
                    values[key] = get_conf(key)
                except Exception:
                    values[key] = default
            except Exception:
                values[key] = default
        return cls.from_mapping(values)

    def to_conf_dict(self):
        """Return one config-storable assistant settings payload."""
        return asdict(self)

    def provider_profiles_list(self):
        """Return normalized provider profiles for runtime consumption."""
        return normalize_provider_profiles(
            self.provider_profiles,
            legacy_base_url=self.openai_compatible_base_url,
            legacy_api_key=self.openai_compatible_api_key,
        )

    def chat_temperature_display_value(self):
        """Return the current chat temperature as a UI-friendly float."""
        return normalize_chat_temperature(self.chat_temperature)

    def chat_default_options(self):
        """Return the normalized default chat request options."""
        return {
            "temperature": normalize_chat_temperature(self.chat_temperature),
            "num_predict": normalize_chat_max_tokens(self.max_tokens),
        }

    def chat_provider_settings(self):
        """Return the normalized chat-provider settings snapshot."""
        return {
            "ollama_host": self.ollama_host,
            "provider_profiles": self.provider_profiles_list(),
            "openai_compatible_base_url": self.openai_compatible_base_url,
            "openai_compatible_api_key": self.openai_compatible_api_key,
        }

    def completion_provider_settings(self):
        """Return the completion-provider settings snapshot."""
        return {
            "ollama_host": self.ollama_host,
            "chat_provider": self.chat_provider,
            "chat_model": self.chat_model,
            "chat_provider_profile_id": self.chat_provider_profile_id,
            "provider_profiles": self.provider_profiles,
            "openai_compatible_base_url": self.openai_compatible_base_url,
            "openai_compatible_api_key": self.openai_compatible_api_key,
            "completion_model": self.completion_model,
            "completion_temperature": self.completion_temperature,
            "completion_max_tokens": self.completion_max_tokens,
            "completions_enabled": self.completions_enabled,
            "completion_manual_only": self.completion_manual_only,
            "project_tools_enabled": self.project_tools_enabled,
            "debounce_ms": self.debounce_ms,
        }

    def mcp_server_config(self):
        """Return the embedded MCP server configuration snapshot."""
        return {
            "enabled": self.mcp_enabled,
            "host": self.mcp_host,
            "port": self.mcp_port,
        }
