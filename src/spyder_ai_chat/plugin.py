"""Spyder AI Chat plugin — main plugin registration.

Registers the AI Chat dockable pane with Spyder's plugin system.
This is the entry point discovered by Spyder via the pyproject.toml
entry point: [spyder.plugins] ai_chat = "spyder_ai_chat.plugin:AIChatPlugin"

Features:
- Dockable chat panel with streaming Ollama responses
- Right-click context menu actions (Ask AI, Explain, Fix, Add Docstring)
- Editor context (file content, selection, cursor) in the system prompt
- "Insert into editor" support from chat code blocks
- Ghost text inline completions (Cursor/VS Code Copilot style)
"""

import logging
from functools import partial

from qtpy.QtCore import QTimer

from spyder.api.plugins import Plugins, SpyderDockablePlugin
from spyder.api.plugin_registration.decorators import (
    on_plugin_available, on_plugin_teardown,
)

from spyder_ai_chat.utils.context import (
    get_editor_context,
    get_open_files_context,
    get_project_context,
    get_console_context,
    get_toolbar_context,
    build_action_prompt,
)
from spyder_ai_chat.widgets.chat_widget import ChatWidget
from spyder_ai_chat.widgets.config_page import AIChatConfigPage
from spyder_ai_chat.widgets.ghost_text import GhostTextManager

logger = logging.getLogger(__name__)


class AIChatPlugin(SpyderDockablePlugin):
    """SpyderDockablePlugin that provides the AI Chat pane.

    Creates a dockable chat panel that communicates with local Ollama
    models for AI-powered code assistance. The panel is tabified with
    the Help pane by default (right dock area).

    Phase 2 integrates with the Editor plugin to:
    - Add right-click context menu actions on code editors
    - Include current file context in chat system prompts
    - Support inserting code blocks from chat responses into the editor
    """

    # --- Plugin identity ---
    NAME = "ai_chat"
    WIDGET_CLASS = ChatWidget
    CONF_WIDGET_CLASS = AIChatConfigPage

    # --- Plugin dependencies ---
    # Preferences is required for the configuration system.
    # Completions is optional — used to wire ghost text to the AI provider.
    REQUIRES = [Plugins.Preferences]
    # Editor and MainMenu are optional — used for context menu actions,
    # editor context awareness, and menu integration.
    # Projects is optional — used for project file tree context.
    # IPythonConsole is optional — used for console output and variable context.
    OPTIONAL = [
        Plugins.Editor, Plugins.MainMenu, Plugins.Completions,
        Plugins.Projects, Plugins.IPythonConsole,
    ]

    # --- Pane positioning ---
    # Tabify with Help pane (typically in the right dock area)
    TABIFY = [Plugins.Help]

    # --- Configuration ---
    CONF_SECTION = "ai_chat"
    # CONF_DEFAULTS must be a list of (section_name, options_dict) tuples.
    # Spyder's PluginConfig._check_defaults validates this format.
    CONF_DEFAULTS = [
        ("ai_chat", {
            "ollama_host": "http://localhost:11434",
            "chat_model": "gpt-oss-20b-abliterated",
            "completion_model":
                "qooba/qwen3-coder-30b-a3b-instruct:q3_k_m",
            "chat_temperature": 0.5,
            "completion_temperature": 0.15,
            "max_tokens": 1024,
            "completion_max_tokens": 256,
            "completions_enabled": True,
            # Keyboard shortcut for manually triggering AI completions.
            # Ctrl+Shift+Space mirrors the common IDE convention (Ctrl+Space
            # is Spyder's LSP completion, Shift variant for AI).
            "completion_shortcut": "Ctrl+Shift+Space",
            "chat_system_prompt":
                "You are a helpful AI coding assistant working inside "
                "the Spyder IDE. Be concise and provide code examples "
                "when relevant.",
            # Action prompts for context menu items. Use {filename} and
            # {code} as placeholders that get replaced at runtime.
            "prompt_explain":
                "Explain this code from {filename}:\n\n```\n{code}\n```",
            "prompt_fix":
                "Find and fix bugs in this code from {filename}:"
                "\n\n```\n{code}\n```",
            "prompt_docstring":
                "Add a docstring to this code from {filename}:"
                "\n\n```\n{code}\n```",
            "prompt_ask":
                "Regarding this code from {filename}:\n\n"
                "```\n{code}\n```\n\n",
        }),
    ]
    CONF_VERSION = "0.1.0"

    # --- Plugin metadata ---

    @staticmethod
    def get_name():
        """Human-readable plugin name for menus and dialogs."""
        return "AI Chat"

    @staticmethod
    def get_description():
        """Plugin description shown in the plugin manager."""
        return (
            "AI chat and code completion using local Ollama models. "
            "Provides a chat panel for asking questions about code "
            "and getting AI-powered suggestions."
        )

    @classmethod
    def get_icon(cls):
        """Plugin icon for the View > Panes menu and dock title.

        Uses a Material Design chat icon from qtawesome (which Spyder
        depends on). Falls back to the built-in help icon if qtawesome
        doesn't have the requested icon.
        """
        try:
            import qtawesome as qta
            return qta.icon("mdi.chat-outline")
        except Exception:
            return cls.create_icon("help")

    # --- Plugin lifecycle ---

    def on_initialize(self):
        """Called after the plugin and widget are created.

        Connects the widget's insert-code signal to our handler,
        sets up the context provider, and initializes ghost text tracking.
        """
        widget = self.get_widget()

        # Connect "Insert into editor" signal from chat code blocks
        widget.sig_insert_code.connect(self._insert_code_into_editor)

        # Provide the widget with a callable that returns the current
        # editor context (file, selection, cursor). The widget calls
        # this on each message send to enrich the system prompt.
        widget.set_context_provider(self._get_editor_context)

        # Ghost text managers: one per editor, keyed by editor widget id.
        # Maps editor id() → GhostTextManager instance.
        self._ghost_managers = {}
        # Reverse map: filename → editor widget, for routing ghost text
        # from the completion provider to the correct editor.
        self._filename_to_editor = {}

    def on_close(self, cancellable=True):
        """Called during Spyder shutdown.

        Stops the background worker thread and cleans up ghost text
        managers. Returns True to allow Spyder to proceed with shutdown.
        """
        self.get_widget().cleanup_worker()

        # Clean up all ghost text managers
        for manager in self._ghost_managers.values():
            manager.cleanup()
        self._ghost_managers.clear()
        self._filename_to_editor.clear()

        return True

    # --- Editor plugin wiring ---

    @on_plugin_available(plugin=Plugins.Editor)
    def on_editor_available(self):
        """Wire up editor integration when the Editor plugin loads.

        Connects to editor signals for:
        - Adding context menu actions to each new code editor
        - Updating the toolbar context label on editor tab switches
        """
        editor_plugin = self.get_plugin(Plugins.Editor)

        # Register context menu actions on each new editor that opens
        editor_plugin.sig_codeeditor_created.connect(
            self._on_codeeditor_created
        )
        # Update toolbar context label when the user switches tabs
        editor_plugin.sig_codeeditor_changed.connect(
            self._on_codeeditor_changed
        )

        # Register actions on any editors that were already open
        # before our plugin loaded (e.g., files restored from session)
        current_editor = editor_plugin.get_current_editor()
        if current_editor is not None:
            self._on_codeeditor_created(current_editor)
            self._on_codeeditor_changed(current_editor)

    @on_plugin_teardown(plugin=Plugins.Editor)
    def on_editor_teardown(self):
        """Disconnect editor signals during teardown."""
        editor_plugin = self.get_plugin(Plugins.Editor)
        editor_plugin.sig_codeeditor_created.disconnect(
            self._on_codeeditor_created
        )
        editor_plugin.sig_codeeditor_changed.disconnect(
            self._on_codeeditor_changed
        )

    # --- Editor event handlers ---

    def _on_codeeditor_created(self, codeeditor):
        """Add AI context menu actions and ghost text to a new code editor.

        Creates four context menu actions (Ask AI, Explain, Fix, Add Docstring)
        and installs a GhostTextManager for inline AI completions.

        Args:
            codeeditor: The CodeEditor widget instance.
        """
        # Install ghost text manager for this editor
        editor_id = id(codeeditor)
        if editor_id not in self._ghost_managers:
            manager = GhostTextManager(codeeditor)
            self._ghost_managers[editor_id] = manager
            logger.debug("Ghost text manager installed on editor %d", editor_id)

            # Add keyboard shortcut to trigger AI completion manually.
            # Default: Ctrl+Shift+Space (configurable in Preferences).
            # Uses QShortcut on the editor widget so it only fires when
            # the editor has focus.
            from qtpy.QtWidgets import QShortcut
            from qtpy.QtGui import QKeySequence
            key_combo = self.get_conf("completion_shortcut")
            shortcut = QShortcut(QKeySequence(key_combo), codeeditor)
            shortcut.activated.connect(manager.request_completion)

        # Import here to avoid import errors if editor plugin is not available
        from spyder.plugins.editor.widgets.codeeditor.codeeditor import (
            CodeEditorContextMenuSections,
        )

        # Define the context menu actions: (id_suffix, display_text, action_key)
        actions = [
            ("ask_ai", "Ask AI", "ask"),
            ("explain_ai", "AI: Explain", "explain"),
            ("fix_ai", "AI: Fix", "fix"),
            ("docstring_ai", "AI: Add Docstring", "docstring"),
        ]

        for action_id, text, action_key in actions:
            # Use a unique ID per editor to avoid conflicts when multiple
            # editors are open. The codeeditor's id() ensures uniqueness.
            unique_id = f"ai_chat_{action_id}_{id(codeeditor)}"

            # Create the action on the codeeditor widget itself. We use
            # register_action=False since these are ephemeral per-editor
            # actions, not global plugin actions.
            action = codeeditor.create_action(
                unique_id,
                text=text,
                register_action=False,
                triggered=partial(self._handle_context_action, action_key),
            )

            # Add to the InspectSection of the editor's context menu,
            # which groups inspection-related actions (go to definition,
            # find references, etc.)
            codeeditor.add_item_to_menu(
                action,
                menu=codeeditor.menu,
                section=CodeEditorContextMenuSections.InspectSection,
            )

    def _on_codeeditor_changed(self, codeeditor):
        """Update the toolbar context label and filename tracking.

        Called on tab switches and editor focus changes. Updates the
        "filename:line" label in the chat widget's toolbar and tracks
        which editor corresponds to which filename (for ghost text routing).

        Args:
            codeeditor: The now-active CodeEditor widget instance.
        """
        editor_plugin = self.get_plugin(Plugins.Editor)
        context_str = get_toolbar_context(codeeditor, editor_plugin)
        logger.debug(
            "Editor changed — context_str=%r, editor=%r", context_str, codeeditor
        )
        self.get_widget().update_toolbar_context(context_str)

        # Track filename → editor mapping for ghost text routing.
        # When the completion provider emits a ghost text for a filename,
        # we need to find the right editor widget to display it on.
        try:
            filename = editor_plugin.get_current_filename() or ""
            if filename:
                self._filename_to_editor[filename] = codeeditor
        except Exception:
            pass

    # --- Context menu action handler ---

    def _handle_context_action(self, action):
        """Handle a context menu AI action (Explain, Fix, Docstring, Ask).

        Gets the current selection and filename, builds an appropriate
        prompt, sends it to the chat, and switches focus to the AI Chat
        pane so the user can see the response.

        Args:
            action: One of "explain", "fix", "docstring", "ask".
        """
        editor_plugin = self.get_plugin(Plugins.Editor)
        if editor_plugin is None:
            return

        editor = editor_plugin.get_current_editor()
        if editor is None:
            return

        # Get the selected text — if nothing is selected, warn the user
        selection = editor.get_selected_text() or ""
        if not selection:
            self.get_widget().chat_display.append_error(
                "No code selected. Select some code first, then "
                "use the AI action from the right-click menu."
            )
            return

        # Build the prompt from the action type, selection, and filename.
        # Read the user's custom prompt template from config if available.
        import os
        filename = editor_plugin.get_current_filename() or ""
        basename = os.path.basename(filename) if filename else "untitled"
        prompt_template = self.get_conf(f"prompt_{action}", default=None)
        prompt = build_action_prompt(
            action, selection, basename, prompt_template=prompt_template
        )

        # Send the prompt to the chat and bring the AI Chat pane to focus
        self.get_widget().send_with_prompt(prompt)
        self.switch_to_plugin()

    # --- Editor context provider ---

    def _get_editor_context(self):
        """Get the full context for system prompt enrichment.

        Called by the chat widget on each message send. Returns a dict
        with five context levels:
        1. Active file — full content, cursor, selection
        2. Other open files — summaries of non-active open tabs
        3. Project structure — file tree of the project root
        4. Console output — recent lines from the IPython console
        5. Namespace variables — names, types, values from the kernel

        Returns:
            Dict with keys:
                - context: Dict from get_editor_context() (active file).
                - open_files: List from get_open_files_context().
                - project: Dict from get_project_context().
                - console: Dict from get_console_context().
            Returns dict with empty values if plugins are unavailable.
        """
        result = {
            "context": {}, "open_files": [], "project": {}, "console": {},
        }

        # --- Active file context ---
        editor_plugin = self.get_plugin(Plugins.Editor)
        if editor_plugin is not None:
            editor = editor_plugin.get_current_editor()
            result["context"] = get_editor_context(editor, editor_plugin)

            # --- Other open files (summaries) ---
            current_filename = result["context"].get("filename", "")
            result["open_files"] = get_open_files_context(
                editor_plugin, current_filename
            )

        # --- Project structure ---
        projects_plugin = self.get_plugin(Plugins.Projects)
        result["project"] = get_project_context(projects_plugin)

        # --- IPython console output and namespace variables ---
        ipython_plugin = self.get_plugin(Plugins.IPythonConsole)
        result["console"] = get_console_context(ipython_plugin)

        return result

    # --- Insert code into editor ---

    def _insert_code_into_editor(self, code):
        """Insert code from a chat response into the active editor.

        If the user has text selected, replaces the selection with the
        code. Otherwise, inserts at the current cursor position.

        Args:
            code: The code text to insert.
        """
        editor_plugin = self.get_plugin(Plugins.Editor)
        if editor_plugin is None:
            logger.warning("Cannot insert code: Editor plugin not available")
            return

        editor = editor_plugin.get_current_editor()
        if editor is None:
            logger.warning("Cannot insert code: No active editor")
            return

        # If text is selected, replace the selection with the new code.
        # Otherwise, insert at the current cursor position.
        if editor.get_selected_text():
            cursor = editor.textCursor()
            cursor.insertText(code)
        else:
            editor.insert_text(code)

    # --- Ghost text routing ---

    def _on_ghost_text_ready(self, filename, text):
        """Route ghost text from the completion provider to the right editor.

        Called when the AI completion provider has a suggestion ready.
        Finds the editor for the given filename and displays the ghost
        text on it via the GhostTextManager.

        Args:
            filename: The file the completion is for.
            text: The completion text to show as ghost text.
        """
        editor = self._filename_to_editor.get(filename)
        if editor is None:
            logger.debug("No editor found for %s, skipping ghost text", filename)
            return

        manager = self._ghost_managers.get(id(editor))
        if manager is None:
            logger.debug("No ghost manager for editor %d", id(editor))
            return

        manager.show_suggestion(text)

    # --- Completions plugin wiring ---

    @on_plugin_available(plugin=Plugins.Completions)
    def on_completions_available(self):
        """Wire ghost text signal from the AI completion provider.

        The completion provider may not be fully registered yet when
        this fires, so we retry with a short delay if needed.
        """
        self._connect_ghost_text_retries = 0
        self._try_connect_ghost_text()

    def _try_connect_ghost_text(self):
        """Attempt to connect to the AI completion provider's ghost text signal.

        The Completions plugin loads providers asynchronously, so our
        ai_chat provider may not be in the providers dict yet when the
        Completions plugin first becomes available. We retry up to 10
        times with a 500ms delay.
        """
        try:
            completions_plugin = self.get_plugin(Plugins.Completions)
            # Spyder stores providers as: providers[name] = {"instance": ..., ...}
            provider_info = completions_plugin.providers.get("ai_chat")
            if provider_info and isinstance(provider_info, dict):
                provider_instance = provider_info.get("instance")
                if provider_instance is not None:
                    provider_instance.sig_ghost_text_ready.connect(
                        self._on_ghost_text_ready
                    )
                    logger.info("Ghost text signal connected to AI completion provider")
                    return

            # Provider not ready yet — retry with a short delay
            self._connect_ghost_text_retries += 1
            if self._connect_ghost_text_retries <= 10:
                logger.debug(
                    "AI completion provider not ready yet, retry %d/10",
                    self._connect_ghost_text_retries,
                )
                QTimer.singleShot(500, self._try_connect_ghost_text)
            else:
                logger.warning(
                    "AI completion provider not found after 10 retries. "
                    "Ghost text completions will not be available."
                )
        except Exception as e:
            logger.warning("Failed to wire ghost text signal: %s", e)

    @on_plugin_teardown(plugin=Plugins.Completions)
    def on_completions_teardown(self):
        """Disconnect ghost text signal on teardown."""
        try:
            completions_plugin = self.get_plugin(Plugins.Completions)
            provider_info = completions_plugin.providers.get("ai_chat")
            if provider_info and isinstance(provider_info, dict):
                provider_instance = provider_info.get("instance")
                if provider_instance is not None:
                    provider_instance.sig_ghost_text_ready.disconnect(
                        self._on_ghost_text_ready
                    )
        except Exception:
            pass  # Provider may already be destroyed

    # --- Optional plugin wiring ---

    @on_plugin_available(plugin=Plugins.Preferences)
    def on_preferences_available(self):
        """Register our config page with Spyder's Preferences dialog.

        This makes the "AI Chat" section appear in Preferences where
        users can configure the Ollama connection, prompts, etc.
        """
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.register_plugin_preferences(self)

    @on_plugin_teardown(plugin=Plugins.Preferences)
    def on_preferences_teardown(self):
        """Remove our config page from Preferences on teardown."""
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.deregister_plugin_preferences(self)
