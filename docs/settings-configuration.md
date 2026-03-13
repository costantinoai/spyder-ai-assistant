# Settings and Configuration

This document describes the full configuration system of `spyder-ai-assistant`,
including all settings tabs, config keys, defaults, and live propagation behavior.

## Settings Architecture

The plugin has a three-layer settings system:

1. **Spyder config store** (`CONF_DEFAULTS` in `plugin.py`)
   All persistent settings are stored by Spyder's `QSettings`-backed config
   system under the `"ai_chat"` section.

2. **In-pane AssistantSettingsDialog**
   The primary user-facing settings UI, opened via the "Settings" button or the
   hamburger menu in the chat pane. This is a `QDialog` with six tabs.

3. **Per-tab ChatSettingsDialog**
   Per-session overrides for temperature and max tokens only. Accessible via
   "Tab Overrides..." in the pane menu.

There is also a Spyder Preferences page (`AIChatConfigPage` in `config_page.py`)
which serves as a redirect — it tells the user to use the in-pane dialog.

## Settings Tabs

### Models

Controls which AI models and endpoints are used.

| Setting | Config Key | Default |
|---------|-----------|---------|
| Default chat model | `chat_model` | `gpt-oss-20b-abliterated` |
| Default completion model | `completion_model` | `qooba/qwen3-coder-30b-a3b-instruct:q3_k_m` |
| Ollama host | `ollama_host` | `http://localhost:11434` |
| Chat provider | `chat_provider` | `ollama` |
| Provider profile ID | `chat_provider_profile_id` | `""` |
| Provider profiles (JSON) | `provider_profiles` | `"[]"` |

### Generation

Controls inference parameters for chat and completions.

| Setting | Config Key | Default | Range |
|---------|-----------|---------|-------|
| Chat temperature | `chat_temperature` | `5` (0.5 x10) | 0.0-2.0 |
| Chat max tokens | `max_tokens` | `1024` | 64-8192 |
| Completions enabled | `completions_enabled` | `True` | bool |
| Completion temperature | `completion_temperature` | `0.15` | 0.0-2.0 |
| Completion max tokens | `completion_max_tokens` | `256` | 16-4096 |
| Debounce (ms) | `debounce_ms` | `300` | 0-5000 |

### Shortcuts

Keyboard shortcuts for completion interactions.

| Setting | Config Key | Default |
|---------|-----------|---------|
| Trigger completion | `completion_shortcut` | `Ctrl+Shift+Space` |
| Accept next word | `completion_accept_word_shortcut` | `Alt+Right` |
| Accept next line | `completion_accept_line_shortcut` | `Alt+Shift+Right` |

Shortcut changes take effect after restarting Spyder.

### Appearance

Controls the visual styling of the chat display, including color themes.

#### Color Theme

| Setting | Config Key | Default | Range |
|---------|-----------|---------|-------|
| Theme preset | `theme_preset` | `default` | default, solarized, nord, dracula, gruvbox, monokai |
| Color overrides | `theme_color_overrides` | `"{}"` | JSON dict of color key to hex |

Built-in presets each provide dark and light variants. The correct variant is
auto-selected based on Spyder's theme. Individual colors can be overridden via
the color swatch buttons in the settings dialog — overrides are applied on top
of the active preset.

The 8 most impactful color keys are exposed as swatch buttons:
user_bg, user_text, assistant_bg, assistant_text, code_block_bg,
code_block_text, inline_code_bg, link_color.

All 23 theme color keys can be overridden via the JSON blob for power users.

#### Fonts and Layout

| Setting | Config Key | Default | Range |
|---------|-----------|---------|-------|
| Chat font family | `chat_font_family` | `sans-serif` | System fonts |
| Chat font size | `chat_font_size` | `10` pt | 6-24 |
| Line height | `chat_line_height` | `1.5` | 1.0-3.0 |
| Code font family | `code_font_family` | `Courier New` | System fonts |
| Code font size | `code_font_size` | `9` pt | 6-24 |
| Syntax theme (dark) | `pygments_style_dark` | `monokai` | Pygments styles |
| Syntax theme (light) | `pygments_style_light` | `default` | Pygments styles |
| Bubble padding | `bubble_padding` | `12` px | 4-32 |
| Bubble border radius | `bubble_border_radius` | `8` px | 0-24 |
| Bubble spacing | `bubble_spacing` | `4` px | 0-16 |

Appearance changes apply immediately to all open chat sessions.

### Behavior

Controls ghost text completion timing.

| Setting | Config Key | Default | Range |
|---------|-----------|---------|-------|
| Idle completion delay | `idle_completion_delay_ms` | `1000` ms | 100-5000 |
| Post-accept delay | `post_accept_completion_delay_ms` | `75` ms | 0-1000 |

- **Idle completion delay**: How long after the user stops typing before an
  automatic ghost text completion is requested.
- **Post-accept delay**: Pause after accepting a ghost text suggestion before
  requesting the next one.

Behavior changes apply immediately to all open editors.

### Prompts

Customizable prompt templates for chat and editor actions.

| Setting | Config Key |
|---------|-----------|
| System prompt | `chat_system_prompt` |
| Explain prompt | `prompt_explain` |
| Fix prompt | `prompt_fix` |
| Add docstring prompt | `prompt_docstring` |
| Ask AI prompt | `prompt_ask` |

Action prompts support `{filename}` and `{code}` placeholders.

## Live Propagation

Settings changes propagate immediately through Spyder's `@on_conf_change`
decorator system:

- **Appearance keys** -> `plugin._propagate_appearance_setting()` ->
  `ChatWidget.update_all_display_appearance()` -> each `ChatDisplay.update_appearance()`
- **Behavior keys** -> `GhostTextManager.update_timing()` on each editor's manager
- **Generation keys** -> `_sync_completion_provider_settings()` on the completion provider
- **Provider keys** -> `_refresh_chat_provider_settings()` -> model rediscovery

## ChatDisplay Styling System

The chat display renders messages as HTML in a `QTextEdit`. All styling uses
inline CSS (Qt's HTML renderer does not support external stylesheets or CSS classes).

### Theme Detection

On construction, `ChatDisplay` checks the widget's background luminance
(`bg.lightness() < 128`) to select either `_DARK_THEME` or `_LIGHT_THEME` —
dictionaries mapping semantic color names to hex values.

### Configurable Values

The following values are stored as instance attributes on `ChatDisplay` and
read during HTML generation:

- `_font_family`, `_font_size`, `_line_height` — message body typography
- `_code_font_family`, `_code_font_size` — code block and inline code typography
- `_pygments_style_dark`, `_pygments_style_light` — Pygments syntax highlighting styles
- `_bubble_padding`, `_bubble_border_radius`, `_bubble_spacing` — bubble geometry

These are initialized to defaults matching the original hardcoded values and can
be updated at runtime via `update_appearance(**kwargs)`.

### Ghost Text Styling

Ghost text colors (foreground, background, underline) are theme-aware but
currently not user-configurable. They use amber/beige tones for visibility
against both dark and light editor backgrounds.
