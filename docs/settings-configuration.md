# Settings and Configuration

This document describes the full configuration system of `spyder-ai-assistant`,
including all settings tabs, config keys, defaults, and live propagation behavior.

## Settings Architecture

The plugin has a three-layer settings system:

1. **Spyder config store** (`CONF_DEFAULTS` in `plugin.py`)
   All persistent settings are stored by Spyder's `QSettings`-backed config
   system under the `"ai_chat"` section.

2. **In-pane AssistantSettingsDialog**
   The primary user-facing settings UI, opened from the "Settings" button menu
   or the hamburger menu in the chat pane. A `QDialog` with four tabs grouped
   by task: **Chat**, **Completions**, **Appearance** and **Advanced**. Each
   tab page sits in a `QScrollArea`, so the dialog stays at its default size
   and a dense tab scrolls instead of squeezing its controls.

3. **Per-tab ChatSettingsDialog**
   Per-session chat mode, temperature and max tokens. Accessible via
   "Tab settings..." in the Settings menu or the pane menu.

There is also a Spyder Preferences page (`AIChatConfigPage` in `config_page.py`)
which serves as a redirect — it tells the user to use the in-pane dialog.

## Settings Tabs

The four tabs are grouped by task. The sections below list the settings each
one holds, with the config key and default behind every control.

| Tab | Holds |
|-----|-------|
| **Chat** | chat model, chat generation defaults, system and editor-action prompts |
| **Completions** | completion model, completion generation defaults, ghost-text timing, popup ownership, shortcuts |
| **Appearance** | color theme, fonts, message bubble geometry |
| **Advanced** | Ollama host, provider profiles, project access, embedded MCP server, client setup |

### Chat and Completions: models

Controls which AI models and endpoints are used. The chat model lives on the
**Chat** tab, the completion model on **Completions**, and the endpoint plus
provider profiles on **Advanced**.

| Setting | Config Key | Default |
|---------|-----------|---------|
| Default chat model | `chat_model` | `qwen3-coder-next` |
| Default completion model | `completion_model` | `qwen3-coder-next` |
| Ollama host | `ollama_host` | `http://localhost:11434` |
| Chat provider | `chat_provider` | `ollama` |
| Provider profile ID | `chat_provider_profile_id` | `""` |
| Provider profiles (JSON) | `provider_profiles` | `"[]"` |

**Provider Profiles...**, in the chat pane's **Settings** menu, manages those
profiles. Each takes a name, a Base URL and an optional API key, and the form
flags a Base URL with no scheme or host, one that already includes the request
path (the client appends `/v1` and asks for `/models` itself), and a remote
endpoint left without a key. **Test connection** probes the endpoint being
edited and reports how many models it returned, or why it could not be
reached; the probe runs off the GUI thread, so the dialog stays usable while
the request is in flight.

### Chat and Completions: generation

Controls inference parameters. The chat rows are on the **Chat** tab
("Chat defaults (all tabs)"), the completion rows on **Completions**.

| Setting | Config Key | Default | Range |
|---------|-----------|---------|-------|
| Chat temperature | `chat_temperature` | `5` (0.5 x10) | 0.0-2.0 |
| Chat max tokens | `max_tokens` | `1024` | 64-8192 |
| Completions enabled | `completions_enabled` | `True` | bool |
| Completion temperature | `completion_temperature` | `0.15` | 0.0-2.0 |
| Completion max tokens | `completion_max_tokens` | `256` | 16-4096 |
| Debounce (ms) | `debounce_ms` | `300` | 0-5000 |

### Completions: shortcuts

Keyboard shortcuts for completion interactions, on the **Completions** tab.

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

### Completions: timing and popup ownership

Controls ghost text completion timing, on the **Completions** tab. Project
access moved to **Advanced**.

| Setting | Config Key | Default | Range |
|---------|-----------|---------|-------|
| Only suggest when I ask | `completion_manual_only` | `False` | on/off |
| Idle completion delay | `idle_completion_delay_ms` | `1000` ms | 100-5000 |
| Post-accept delay | `post_accept_completion_delay_ms` | `75` ms | 0-1000 |
| Spyder's automatic completion popup | `native_popup_policy` | `ai_first` | `ai_first`, `ai_replaces`, `native_first` |

- **Only suggest when I ask**: nothing is suggested while typing. All three
  automatic paths stop: the idle timer, the follow-up request after accepting
  a suggestion, and the editor's own automatic completion request, which
  would otherwise reach ghost text through the completion provider without
  going near those timers. `Ctrl+Shift+Space` still asks for a suggestion and
  is then the only way to get one, so the two delays below are greyed out
  while this is on.
- **Idle completion delay**: How long after the user stops typing before an
  automatic ghost text completion is requested.
- **Post-accept delay**: Pause after accepting a ghost text suggestion before
  requesting the next one.
- **Spyder's automatic completion popup**: who owns the editor when Spyder
  would open its own completion popup automatically (after `.` or a few
  characters). An explicit `Ctrl+Space` popup always wins, whatever the
  policy.
  - `ai_first` (default): while AI completions are on and the model is
    available, the automatic popup stays hidden and ghost text is the only
    automatic suggestion. If the model is offline or completions are off,
    Spyder's popup behaves normally.
  - `ai_replaces`: the automatic popup opens while the AI is thinking; the
    ghost suggestion closes it when it arrives.
  - `native_first`: the automatic popup keeps priority; an AI suggestion
    arriving while it is open is dropped, and a visible ghost only blocks new
    automatic popups.

Behavior changes apply immediately to all open editors.

### Chat: prompts

Customizable prompt templates for chat and editor actions, on the **Chat** tab.

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
- **Completion keys** -> `_sync_completion_provider_settings()` on the completion provider
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
