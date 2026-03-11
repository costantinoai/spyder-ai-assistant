"""Spyder AI Chat plugin — main plugin registration.

Registers the AI Chat dockable pane with Spyder's plugin system.
This is the entry point discovered by Spyder via the pyproject.toml
entry point: [spyder.plugins] ai_chat = "spyder_ai_assistant.plugin:AIChatPlugin"

Features:
- Dockable chat panel with streaming Ollama responses
- Right-click context menu actions (Ask AI, Explain, Fix, Add Docstring)
- Editor context (file content, selection, cursor) in the system prompt
- Code apply support from chat code blocks
- Ghost text inline completions (Cursor/VS Code Copilot style)
"""

import logging
from functools import partial

from qtpy.QtCore import QTimer

from spyder.api.config.decorators import on_conf_change
from spyder.api.plugins import Plugins, SpyderDockablePlugin
from spyder.api.plugin_registration.decorators import (
    on_plugin_available, on_plugin_teardown,
)

from spyder_ai_assistant.utils.context import (
    get_editor_context,
    get_open_files_context,
    get_project_context,
    get_toolbar_context,
    build_action_prompt,
)
from spyder_ai_assistant.utils.chat_persistence import (
    get_chat_session_storage_path,
    load_chat_session_state,
    save_chat_session_state,
)
from spyder_ai_assistant.utils.runtime_context import RuntimeContextService
from spyder_ai_assistant.widgets.chat_widget import ChatWidget
from spyder_ai_assistant.widgets.config_page import AIChatConfigPage
from spyder_ai_assistant.widgets.ghost_text import GhostTextManager

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
    # IPythonConsole is optional — used for live runtime context.
    # VariableExplorer is optional — used to mirror namespace filter settings.
    OPTIONAL = [
        Plugins.Editor, Plugins.MainMenu, Plugins.Completions,
        Plugins.Projects, Plugins.IPythonConsole, Plugins.VariableExplorer,
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
            # Stored as "temperature x10" for the current preferences UI.
            # Runtime normalization keeps backward compatibility with older
            # float values that may already exist in user config.
            "chat_temperature": 5,
            "completion_temperature": 0.15,
            "max_tokens": 1024,
            "completion_max_tokens": 256,
            "completions_enabled": True,
            # Keyboard shortcut for manually triggering AI completions.
            # Ctrl+Shift+Space mirrors the common IDE convention (Ctrl+Space
            # is Spyder's LSP completion, Shift variant for AI).
            "completion_shortcut": "Ctrl+Shift+Space",
            "completion_accept_word_shortcut": "Alt+Right",
            "completion_accept_line_shortcut": "Alt+Shift+Right",
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

        # Connect code-apply signals from chat code blocks
        widget.sig_insert_code.connect(
            partial(self._apply_code_into_editor, mode="insert")
        )
        widget.sig_replace_code.connect(
            partial(self._apply_code_into_editor, mode="replace")
        )

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
        # Cached runtime context service keyed by active shellwidget.
        self._runtime_context = RuntimeContextService(self)
        self._completion_provider_instance = None
        widget.set_runtime_request_executor(self._runtime_context.execute_request)
        self._runtime_context.sig_current_context_changed.connect(
            widget.update_runtime_context
        )
        widget.update_runtime_context(self._runtime_context.get_current_context())
        widget.set_session_state_changed_callback(
            self._schedule_chat_session_save
        )
        widget.set_session_scope_provider(self._get_chat_session_scope_info)

        self._chat_session_project_path = None
        self._chat_session_storage_path = None
        self._chat_session_state_restored = False
        self._chat_session_save_timer = QTimer(self)
        self._chat_session_save_timer.setSingleShot(True)
        self._chat_session_save_timer.timeout.connect(
            self._flush_chat_session_state
        )
        QTimer.singleShot(0, self._restore_initial_chat_session_state)

    def on_close(self, cancellable=True):
        """Called during Spyder shutdown.

        Stops the background worker thread and cleans up ghost text
        managers. Returns True to allow Spyder to proceed with shutdown.
        """
        self._flush_chat_session_state()
        self.get_widget().cleanup_worker()

        # Clean up all ghost text managers
        for manager in self._ghost_managers.values():
            manager.cleanup()
        self._ghost_managers.clear()
        self._filename_to_editor.clear()
        self._runtime_context.cleanup()

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
        self._register_existing_editors(editor_plugin)

        current_editor = editor_plugin.get_current_editor()
        if current_editor is not None:
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

    @on_plugin_available(plugin=Plugins.Projects)
    def on_projects_available(self):
        """Bind project-aware chat session persistence when Projects loads."""
        projects_plugin = self.get_plugin(Plugins.Projects)
        projects_plugin.sig_project_loaded.connect(self._on_project_loaded)
        projects_plugin.sig_project_closed.connect(self._on_project_closed)

        active_project_path = projects_plugin.get_active_project_path()
        if active_project_path:
            self._switch_chat_session_scope(
                active_project_path,
                restore=True,
                save_current=False,
            )
            self._chat_session_state_restored = True

    @on_plugin_teardown(plugin=Plugins.Projects)
    def on_projects_teardown(self):
        """Disconnect project-aware chat persistence hooks on teardown."""
        projects_plugin = self.get_plugin(Plugins.Projects)
        projects_plugin.sig_project_loaded.disconnect(self._on_project_loaded)
        projects_plugin.sig_project_closed.disconnect(self._on_project_closed)

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
            manager = GhostTextManager(
                codeeditor,
                lifecycle_callback=self._on_ghost_lifecycle_event,
            )
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
            codeeditor._ai_chat_completion_shortcut = shortcut

            accept_word_combo = self.get_conf("completion_accept_word_shortcut")
            accept_word_shortcut = QShortcut(
                QKeySequence(accept_word_combo),
                codeeditor,
            )
            accept_word_shortcut.activated.connect(manager.accept_next_word)
            codeeditor._ai_chat_completion_accept_word_shortcut = (
                accept_word_shortcut
            )

            accept_line_combo = self.get_conf("completion_accept_line_shortcut")
            accept_line_shortcut = QShortcut(
                QKeySequence(accept_line_combo),
                codeeditor,
            )
            accept_line_shortcut.activated.connect(manager.accept_next_line)
            codeeditor._ai_chat_completion_accept_line_shortcut = (
                accept_line_shortcut
            )

        # Import here to avoid import errors if editor plugin is not available
        from spyder.plugins.editor.widgets.codeeditor.codeeditor import (
            CodeEditorContextMenuSections,
        )

        if getattr(codeeditor, "_ai_chat_actions_installed", False):
            return

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

        codeeditor._ai_chat_actions_installed = True

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
        with three context levels:
        1. Active file — full content, cursor, selection
        2. Other open files — summaries of non-active open tabs
        3. Project structure — file tree of the project root

        Returns:
            Dict with keys:
                - context: Dict from get_editor_context() (active file).
                - open_files: List from get_open_files_context().
                - project: Dict from get_project_context().
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

        return result

    # --- Chat session persistence ---

    def _restore_initial_chat_session_state(self):
        """Restore chat sessions for the active project or global scope."""
        if self._chat_session_state_restored:
            return

        self._switch_chat_session_scope(
            self._get_active_project_path(),
            restore=True,
            save_current=False,
        )
        self._chat_session_state_restored = True

    def _get_active_project_path(self):
        """Return the active Spyder project path, if any."""
        projects_plugin = self.get_plugin(Plugins.Projects, error=False)
        if projects_plugin is None:
            return None

        try:
            return projects_plugin.get_active_project_path()
        except Exception:
            return None

    def _on_project_loaded(self, project_path):
        """Switch persisted chat state when a project is opened."""
        self._switch_chat_session_scope(project_path, restore=True)

    def _on_project_closed(self, _project_path):
        """Return persisted chat state to the global scope when closing."""
        self._switch_chat_session_scope(None, restore=True)

    def _resolve_chat_session_storage_path(self, project_path=None):
        """Resolve the persistence file for the given project scope."""
        return get_chat_session_storage_path(project_path)

    def _get_chat_session_scope_info(self):
        """Return the current persistence-scope metadata for the widget."""
        storage_path = self._chat_session_storage_path
        if storage_path is None:
            storage_path = self._resolve_chat_session_storage_path(
                self._get_active_project_path()
            )

        if self._chat_session_project_path:
            scope_label = "Project"
        else:
            scope_label = "Global"

        return {
            "scope_label": scope_label,
            "project_path": self._chat_session_project_path or "",
            "storage_path": str(storage_path),
        }

    def _switch_chat_session_scope(self, project_path, restore=True,
                                   save_current=True):
        """Save the current scope and load sessions for the new scope."""
        new_storage_path = self._resolve_chat_session_storage_path(project_path)
        current_storage_path = self._chat_session_storage_path

        if (
            current_storage_path is not None
            and str(new_storage_path) == str(current_storage_path)
        ):
            if restore:
                self._restore_chat_session_state(new_storage_path)
            return

        if save_current:
            self._flush_chat_session_state()

        self._chat_session_project_path = project_path or None
        self._chat_session_storage_path = new_storage_path
        logger.info(
            "Chat session scope set to %s (%s)",
            project_path or "<global>",
            new_storage_path,
        )

        if restore:
            self._restore_chat_session_state(new_storage_path)

    def _restore_chat_session_state(self, storage_path):
        """Load persisted chat sessions and restore them into the widget."""
        state = load_chat_session_state(storage_path)
        session_count = (
            len(state.get("sessions", []))
            if isinstance(state, dict) else 0
        )
        self.get_widget().restore_session_state(state)
        logger.info(
            "Restored %d chat session(s) from %s",
            session_count,
            storage_path,
        )

    def _schedule_chat_session_save(self):
        """Debounce chat-session persistence after UI state changes."""
        if self._chat_session_storage_path is None:
            self._chat_session_storage_path = (
                self._resolve_chat_session_storage_path(
                    self._get_active_project_path()
                )
            )

        self._chat_session_save_timer.start(250)

    def _flush_chat_session_state(self):
        """Write the current chat sessions to the active persistence file."""
        timer = getattr(self, "_chat_session_save_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

        if self._chat_session_storage_path is None:
            self._chat_session_storage_path = (
                self._resolve_chat_session_storage_path(
                    self._get_active_project_path()
                )
            )

        widget = self.get_widget()
        if widget is None:
            return

        state = widget.serialize_session_state()
        if save_chat_session_state(self._chat_session_storage_path, state):
            logger.debug(
                "Saved %d chat session(s) to %s",
                len(state.get("sessions", [])),
                self._chat_session_storage_path,
            )

    # --- IPython Console wiring ---

    @on_plugin_available(plugin=Plugins.IPythonConsole)
    def on_ipython_console_available(self):
        """Bind runtime-context collection to the IPython Console plugin."""
        ipython_plugin = self.get_plugin(Plugins.IPythonConsole)
        self._runtime_context.bind_ipython_console(ipython_plugin)

        variable_explorer = self.get_plugin(Plugins.VariableExplorer, error=False)
        self._runtime_context.set_variable_explorer_plugin(variable_explorer)

    @on_plugin_teardown(plugin=Plugins.IPythonConsole)
    def on_ipython_console_teardown(self):
        """Release runtime-context signal wiring on console teardown."""
        self._runtime_context.unbind_ipython_console()

    @on_plugin_available(plugin=Plugins.VariableExplorer)
    def on_variable_explorer_available(self):
        """Reuse Variable Explorer settings for runtime namespace filters."""
        variable_explorer = self.get_plugin(Plugins.VariableExplorer)
        self._runtime_context.set_variable_explorer_plugin(variable_explorer)

    @on_plugin_teardown(plugin=Plugins.VariableExplorer)
    def on_variable_explorer_teardown(self):
        """Fall back to default namespace filters when Variable Explorer closes."""
        self._runtime_context.set_variable_explorer_plugin(None)

    # --- Insert code into editor ---

    def _apply_code_into_editor(self, code, mode="insert"):
        """Apply code from a chat response into the active editor.

        Args:
            code: The code text to insert.
            mode: Either ``insert`` to insert at the caret without
                deleting the current selection, or ``replace`` to
                replace the current selection when one exists.
        """
        editor_plugin = self.get_plugin(Plugins.Editor)
        if editor_plugin is None:
            logger.warning(
                "Cannot apply chat code (%s): Editor plugin not available",
                mode,
            )
            return

        editor = editor_plugin.get_current_editor()
        if editor is None:
            logger.warning("Cannot apply chat code (%s): No active editor", mode)
            return

        if mode == "replace" and editor.get_selected_text():
            cursor = editor.textCursor()
            cursor.insertText(code)
            logger.info(
                "Applied chat code by replacing the current editor selection"
            )
            return

        if mode == "insert" and editor.get_selected_text():
            cursor = editor.textCursor()
            insert_position = cursor.position()
            cursor.clearSelection()
            cursor.setPosition(insert_position)
            editor.setTextCursor(cursor)
            cursor.insertText(code)
            logger.info(
                "Applied chat code at the current cursor position without replacing the selection"
            )
            return

        editor.insert_text(code)
        if mode == "replace":
            logger.info(
                "Applied chat code in replace-selection mode with no active selection; inserted at the current cursor position instead"
            )
        else:
            logger.info(
                "Applied chat code at the current cursor position"
            )

    # --- Ghost text routing ---

    def _on_ghost_text_ready(self, filename, text, target):
        """Route ghost text from the completion provider to the right editor.

        Called when the AI completion provider has a suggestion ready.
        Finds the editor for the given filename and displays the ghost
        text on it via the GhostTextManager.

        Args:
            filename: The file the completion is for.
            text: The completion text to show as ghost text.
            target: Cursor target metadata for stale-display checks.
        """
        editor = self._filename_to_editor.get(filename)
        if editor is None:
            logger.debug("No editor found for %s, skipping ghost text", filename)
            return

        manager = self._ghost_managers.get(id(editor))
        if manager is None:
            logger.debug("No ghost manager for editor %d", id(editor))
            return

        shown = manager.show_suggestion(text, target=target)
        if not shown:
            logger.debug(
                "Ghost text suppressed for %s because the editor target no longer matched",
                filename,
            )

    def _on_ghost_lifecycle_event(self, event_name, payload):
        """Forward one editor-side ghost lifecycle event to the provider."""
        provider = self._completion_provider_instance
        if provider is None:
            try:
                completions_plugin = self.get_plugin(Plugins.Completions)
                provider = completions_plugin.get_provider("ai_chat")
            except Exception:
                provider = None

        if provider is None or not hasattr(provider, "record_ghost_event"):
            return

        try:
            provider.record_ghost_event(event_name, payload)
        except Exception as error:
            logger.debug("Failed to record ghost lifecycle event: %s", error)

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
                    self._completion_provider_instance = provider_instance
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
                    self._completion_provider_instance = None
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

    @on_conf_change(option="ollama_host")
    def on_host_changed(self, value):
        """Propagate Ollama host changes to the chat widget worker."""
        self.get_widget().update_ollama_host(value)

    @on_plugin_teardown(plugin=Plugins.Preferences)
    def on_preferences_teardown(self):
        """Remove our config page from Preferences on teardown."""
        preferences = self.get_plugin(Plugins.Preferences)
        preferences.deregister_plugin_preferences(self)

    def _register_existing_editors(self, editor_plugin):
        """Install AI wiring on editors restored before plugin startup."""
        try:
            filenames = editor_plugin.get_filenames() or []
        except Exception:
            filenames = []

        seen_editors = set()
        for filename in filenames:
            try:
                codeeditor = editor_plugin.get_codeeditor_for_filename(filename)
            except Exception:
                codeeditor = None

            if codeeditor is None:
                continue

            editor_id = id(codeeditor)
            if editor_id in seen_editors:
                continue

            seen_editors.add(editor_id)
            self._on_codeeditor_created(codeeditor)
            if filename:
                self._filename_to_editor[filename] = codeeditor
