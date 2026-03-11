# Spyder Plugin API Reference

> Compiled: 2026-03-10
> Spyder version: 6.x
> Sources: Spyder developer docs, GitHub wiki, plugin-examples repo, langchain-provider source

---

## 1. Plugin Base Classes

### SpyderPluginV2

For plugins that do NOT create a dockable pane. Import from `spyder.api.plugins`.

**Mandatory class attributes/methods:**

| Attribute/Method | Type | Description |
|---|---|---|
| `NAME` | str | Unique plugin identifier |
| `get_name()` | staticmethod | Human-readable plugin name |
| `get_description()` | staticmethod | Localized description |
| `get_icon()` | classmethod | Returns QIcon |
| `on_initialize()` | method | Core setup (replaces old `register()`) |

**Optional class attributes:**

| Attribute | Type | Description |
|---|---|---|
| `REQUIRES` | list | `Plugins.*` dependencies |
| `OPTIONAL` | list | Optional plugin dependencies |
| `CONTAINER_CLASS` | class | `PluginMainContainer` subclass |
| `CONF_SECTION` | str | Config section name |
| `CONF_FILE` | bool | Use separate config file |
| `CONF_DEFAULTS` | list | `[(option, default_value), ...]` |
| `CONF_VERSION` | str | Config version for migrations |
| `CONF_WIDGET_CLASS` | class | `PluginConfigPage` subclass for Preferences |
| `CAN_BE_DISABLED` | bool | Whether user can disable |
| `CUSTOM_LAYOUTS` | list | Layout classes to register |

**Key signals:**
- `sig_plugin_ready`
- `sig_status_message_requested`
- `sig_exception_occurred`
- `sig_quit_requested`, `sig_restart_requested`

### SpyderDockablePlugin

Extends `SpyderPluginV2` for plugins that create a dockable pane (like Help, Variable Explorer, Plots).

**Additional mandatory attribute:**

| Attribute | Type | Description |
|---|---|---|
| `WIDGET_CLASS` | class | `PluginMainWidget` subclass |

**Additional optional attributes:**

| Attribute | Type | Description |
|---|---|---|
| `TABIFY` | list | Plugin(s) to position alongside |
| `DISABLE_ACTIONS_WHEN_HIDDEN` | bool | Default True |
| `RAISE_AND_FOCUS` | bool | Default False |

**Additional signals:**
- `sig_focus_changed`
- `sig_toggle_view_changed`
- `sig_switch_to_plugin_requested`

---

## 2. Widget System (PluginMainWidget)

Import from `spyder.api.widgets.main_widget`.

Base for the pane of a `SpyderDockablePlugin`. Inherits from `QWidget` and `SpyderWidgetMixin`.

**Three abstract methods to implement:**

```python
from spyder.api.widgets.main_widget import PluginMainWidget

class MyChatWidget(PluginMainWidget):
    ENABLE_SPINNER = True  # optional, shows loading spinner

    def get_title(self):
        """Return widget title for the pane header."""
        return "AI Chat"

    def setup(self):
        """Create actions, menus, toolbars, and internal UI here.
        Called once during initialization."""
        # Build Qt layout
        # Create actions with self.create_action(...)
        # Add to options menu, main toolbar, etc.

    def update_actions(self):
        """Called when actions need state refresh (e.g., on focus)."""
        pass
```

**Built-in infrastructure:**
- **Main toolbar** (`get_main_toolbar()`) — horizontal toolbar at the top
- **Corner toolbar** — right-aligned area with hamburger/options menu + optional spinner
- **Options menu** (`get_options_menu()`) — the hamburger dropdown
- **Auxiliary toolbars** (`create_toolbar(id)`) — additional toolbars
- **Stacked widget** — built-in QStackedWidget for empty-state vs content
- **Spinner** — loading indicator (`start_spinner()` / `stop_spinner()`)

**Key signals:**
- `sig_free_memory_requested`
- `sig_quit_requested`, `sig_restart_requested`
- `sig_redirect_stdio_requested`
- `sig_exception_occurred`
- `sig_toggle_view_changed`
- `sig_focus_status_changed`

---

## 3. Completion Provider API

Import from `spyder.plugins.completion.api`.

### SpyderCompletionProvider

Base class for all completion providers. Inherits from `QObject` and `CompletionConfigurationObserver`.

**Required class attributes:**

| Attribute | Type | Description |
|---|---|---|
| `COMPLETION_PROVIDER_NAME` | str | Unique provider name (e.g., `"ai_chat"`) |
| `DEFAULT_ORDER` | int | Priority (1 = highest) |

**Optional class attributes:**

| Attribute | Type | Description |
|---|---|---|
| `SLOW` | bool | Hint that provider may have variable latency |
| `CONF_VERSION` | str | Default `"0.1.0"` |
| `CONF_DEFAULTS` | list | `[(option, default_value), ...]` |
| `CONF_TABS` | list | Widget classes for preference tabs |
| `STATUS_BAR_CLASSES` | list | `StatusBarWidget` classes or callables |

**Required methods:**

```python
def get_name(self) -> str:
    """Human-readable name for the UI."""

def send_request(self, language, req_type, req, req_id):
    """Handle completion/introspection requests from the editor.
    Must eventually emit sig_response_ready."""

def start_completion_services_for_language(self, language) -> bool:
    """Return True if this provider supports the given language."""

def start(self):
    """Initialize the provider. Must emit sig_provider_ready when ready."""

def shutdown(self):
    """Gracefully stop the provider."""
```

**Key optional methods:**
- `file_opened_closed_or_updated(filename, language)` — file lifecycle events
- `register_file(language, filename, codeeditor)` — register files
- `python_path_update(new_path, prioritize)` — handle path changes
- `project_path_update(project_path, update_kind, instance)` — project changes
- `on_mainwindow_visible()` — post-startup actions

**Key signals:**

| Signal | Signature | Description |
|---|---|---|
| `sig_provider_ready` | `(str)` | Emit when provider is initialized |
| `sig_response_ready` | `(str, int, dict)` | `(provider_name, request_id, response_dict)` |
| `sig_language_completions_available` | `(dict, str)` | Announce capabilities for a language |
| `sig_call_statusbar` | `(str, str, tuple, dict)` | Invoke status bar widget methods |
| `sig_disable_provider` | `(str)` | Request provider to be disabled |
| `sig_open_file` | `(str, int)` | Request editor to open a file |

**Key enums from `spyder.plugins.completion.api`:**

`CompletionRequestTypes`:
- `DOCUMENT_DID_OPEN`, `DOCUMENT_DID_CHANGE`, `DOCUMENT_DID_CLOSE`
- `DOCUMENT_COMPLETION` — the main completion request
- `DOCUMENT_HOVER`, `DOCUMENT_SIGNATURE`
- `DOCUMENT_REFERENCES`, `DOCUMENT_FORMATTING`
- `DOCUMENT_RENAME`, `DOCUMENT_SYMBOL`

`CompletionItemKind`:
- `TEXT`, `METHOD`, `FUNCTION`, `CONSTRUCTOR`
- `FIELD`, `VARIABLE`, `CLASS`, `INTERFACE`, `MODULE`
- `PROPERTY`, `KEYWORD`, `SNIPPET`, `FILE`, `FOLDER`

---

## 4. Plugin Registration (Entry Points)

### For dockable/standard plugins:

```toml
# pyproject.toml
[project.entry-points."spyder.plugins"]
ai_chat = "spyder_ai_assistant.plugin:AIChatPlugin"
```

### For completion providers:

```toml
[project.entry-points."spyder.completions"]
ai_chat = "spyder_ai_assistant.completion_provider:AIChatCompletionProvider"
```

Both can coexist in the same package.

---

## 5. Dependency Injection (Plugin Wiring)

Since Spyder 5.1, plugins use decorators for dependency injection:

```python
from spyder.api.plugin_registration.decorators import (
    on_plugin_available, on_plugin_teardown
)
from spyder.api.plugins import Plugins, SpyderDockablePlugin

class AIChatPlugin(SpyderDockablePlugin):
    NAME = "ai_chat"
    REQUIRES = [Plugins.Preferences]
    OPTIONAL = [Plugins.Editor, Plugins.IPythonConsole, Plugins.MainMenu]

    def on_initialize(self):
        widget = self.get_widget()
        # Self-contained setup — no dependency on other plugins here

    @on_plugin_available(plugin=Plugins.Editor)
    def on_editor_available(self):
        editor = self.get_plugin(Plugins.Editor)
        # Connect to editor signals, register actions, etc.

    @on_plugin_teardown(plugin=Plugins.Editor)
    def on_editor_teardown(self):
        editor = self.get_plugin(Plugins.Editor)
        # Disconnect signals, clean up

    @on_plugin_available(plugin=Plugins.MainMenu)
    def on_main_menu_available(self):
        main_menu = self.get_plugin(Plugins.MainMenu)
        # Add menu items
```

---

## 6. Configuration System

### Defining config options:

```python
class AIChatPlugin(SpyderDockablePlugin):
    CONF_SECTION = "ai_chat"
    CONF_DEFAULTS = [
        ("ollama_host", "http://localhost:11434"),
        ("chat_model", "gpt-oss-20b-abliterated"),
        ("completion_model", "qooba/qwen3-coder-30b-a3b-instruct:q3_k_m"),
        ("temperature", 0.5),
        ("max_tokens", 1024),
        ("completions_enabled", True),
    ]
    CONF_VERSION = "0.1.0"
```

### Reading/writing config:

```python
# In any plugin or widget that inherits SpyderConfigurationAccessor:
host = self.get_conf("ollama_host")
self.set_conf("chat_model", "new-model-name")
```

### Reacting to config changes:

```python
from spyder.api.config.decorators import on_conf_change

class AIChatPlugin(SpyderDockablePlugin):
    @on_conf_change(option="chat_model")
    def on_chat_model_changed(self, value):
        self.get_widget().update_model(value)
```

---

## 7. Status Bar Widgets

```python
from spyder.api.widgets.status import StatusBarWidget

class AIChatStatusWidget(StatusBarWidget):
    ID = "ai_chat_status"

    def __init__(self, parent, provider):
        super().__init__(parent, provider)
        self.set_value("AI: Ready")

    def get_tooltip(self):
        return "AI Chat model status"

    def get_icon(self):
        return self.create_icon("chat")
```

Register in the completion provider:
```python
class AIChatCompletionProvider(SpyderCompletionProvider):
    STATUS_BAR_CLASSES = [AIChatStatusWidget]
    # or use a factory: STATUS_BAR_CLASSES = [self.create_statusbar]
```

---

## 8. Useful Spyder Plugin Names (Plugins enum)

```python
from spyder.api.plugins import Plugins

Plugins.Editor           # The code editor
Plugins.IPythonConsole   # IPython console
Plugins.Help             # Help pane
Plugins.VariableExplorer # Variable explorer
Plugins.Preferences      # Preferences dialog
Plugins.MainMenu         # Main menu bar
Plugins.Toolbar          # Toolbar manager
Plugins.StatusBar        # Status bar
Plugins.Shortcuts        # Keyboard shortcuts
Plugins.Completion       # Completion manager (coordinates all providers)
Plugins.Application      # Application lifecycle
```

---

## 9. Reference Implementations

### Existing langchain-provider structure:

```
langchain_provider/
    __init__.py              # Docstring only
    provider.py              # LangchainProvider(SpyderCompletionProvider)
    client.py                # LangchainClient(QObject) - QThread worker
    widgets/
        __init__.py          # Exports LangchainStatusWidget
        status.py            # LangchainStatusWidget(StatusBarWidget)
        config_dialog.py     # LangchainConfigDialog(QDialog)
```

**Key patterns from langchain-provider:**
- Provider creates a QThread + worker QObject for async LLM calls
- Worker uses `moveToThread()` pattern (not QThread subclass)
- Signal chain: editor request -> provider.send_request() -> signal to worker -> worker processes -> signal back -> provider emits sig_response_ready
- Config changes use `@on_conf_change` decorator
- Status bar widget shows model name and opens config dialog on click

### Other useful references:
- **spyder-terminal** — dockable plugin with interactive terminal widget
- **Help plugin** — dockable with web view for rendered content
- **plugin-examples** repo — minimal examples of Spyder plugins
- **kite-provider** — deprecated but shows completion provider patterns

## Sources

- [Spyder Developer Docs 6 - Plugin Tutorial](https://spyder-ide.github.io/spyder-api-docs/plugin_tutorial_1.html)
- [Spyder Developer Docs 6 - Plugin Development](https://spyder-ide.github.io/spyder-api-docs/plugin_development.html)
- [Spyder Developer Docs 6 - API Elements](https://spyder-ide.github.io/spyder-api-docs/api_elements.html)
- [SpyderCompletionProvider API Wiki](https://github.com/spyder-ide/spyder/wiki/Dev:-SpyderCompletionProvider-API)
- [Plugin Registration Wiki](https://github.com/spyder-ide/spyder/wiki/New-mechanism-to-register-plugins-in-Spyder-5.1.0)
- [langchain-provider GitHub](https://github.com/spyder-ide/langchain-provider)
- [plugin-examples GitHub](https://github.com/spyder-ide/plugin-examples)
- [spyder-terminal GitHub](https://github.com/spyder-ide/spyder-terminal)
