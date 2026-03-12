"""Preferences page for the AI Chat plugin.

Provides a settings UI in Spyder's Preferences dialog where users can
configure the Ollama connection, model parameters, system prompt, and
the action prompts used by the right-click context menu actions
(Explain, Fix, Add Docstring, Ask AI).

All settings are automatically persisted by Spyder's config system
via the create_* widget factories from PluginConfigPage.
"""

from qtpy.QtCore import Qt
from qtpy.QtWidgets import QGroupBox, QLabel, QVBoxLayout

from spyder.api.preferences import PluginConfigPage


class AIChatConfigPage(PluginConfigPage):
    """Preferences page for the Spyder AI Chat plugin.

    Organized into groups:
    - Connection: Ollama server URL
    - Models: Chat and completion model names
    - Generation: Temperature and token limits
    - System prompt: The base system prompt for all chat messages
    - Action prompts: Editable templates for context menu actions
    """

    def setup_page(self):
        """Build the preferences UI layout.

        Called by Spyder when the preferences dialog opens. All widgets
        created with self.create_* auto-save/load from the plugin's
        CONF_SECTION on OK/Apply.
        """
        # --- Connection settings ---
        connection_group = QGroupBox("Chat Providers")
        chat_provider_combo = self.create_combobox(
            "Default chat provider:",
            [
                ("Ollama", "ollama"),
                ("OpenAI-compatible", "openai_compatible"),
            ],
            "chat_provider",
            default="ollama",
            tip="Default provider selected when the chat model list refreshes.",
            alignment=Qt.Horizontal,
        )
        host_edit = self.create_lineedit(
            "Ollama server URL:",
            "ollama_host",
            default="http://localhost:11434",
            tip="Base URL for the local Ollama server",
            alignment=Qt.Horizontal,
        )
        compatible_profiles_note = QLabel(
            "OpenAI-compatible chat endpoints are managed from the AI Chat pane: "
            "More > Provider Profiles.... Existing single-endpoint settings are "
            "imported automatically the first time you open that dialog."
        )
        compatible_profiles_note.setWordWrap(True)
        connection_layout = QVBoxLayout()
        connection_layout.addWidget(chat_provider_combo)
        connection_layout.addWidget(host_edit)
        connection_layout.addWidget(compatible_profiles_note)
        connection_group.setLayout(connection_layout)

        # --- Model settings ---
        models_group = QGroupBox("Models")
        chat_model_edit = self.create_lineedit(
            "Chat model:",
            "chat_model",
            default="gpt-oss-20b-abliterated",
            tip="Default chat model name for the selected provider",
            alignment=Qt.Horizontal,
        )
        completion_model_edit = self.create_lineedit(
            "Completion model:",
            "completion_model",
            default="qooba/qwen3-coder-30b-a3b-instruct:q3_k_m",
            tip="Ollama model name for code completions",
            alignment=Qt.Horizontal,
        )
        models_layout = QVBoxLayout()
        models_layout.addWidget(chat_model_edit)
        models_layout.addWidget(completion_model_edit)
        models_group.setLayout(models_layout)

        # --- Keyboard shortcuts ---
        shortcuts_group = QGroupBox("Keyboard Shortcuts")
        shortcut_edit = self.create_lineedit(
            "Trigger AI completion:",
            "completion_shortcut",
            default="Ctrl+Shift+Space",
            tip="Key combination to manually request an AI code completion. "
                "Requires Spyder restart to take effect.",
            alignment=Qt.Horizontal,
        )
        accept_word_edit = self.create_lineedit(
            "Accept next word:",
            "completion_accept_word_shortcut",
            default="Alt+Right",
            tip="Key combination to accept only the next word-like segment "
                "from one ghost completion. Requires Spyder restart to take effect.",
            alignment=Qt.Horizontal,
        )
        accept_line_edit = self.create_lineedit(
            "Accept next line:",
            "completion_accept_line_shortcut",
            default="Alt+Shift+Right",
            tip="Key combination to accept only the next line from one ghost "
                "completion. Requires Spyder restart to take effect.",
            alignment=Qt.Horizontal,
        )
        shortcuts_layout = QVBoxLayout()
        shortcuts_layout.addWidget(shortcut_edit)
        shortcuts_layout.addWidget(accept_word_edit)
        shortcuts_layout.addWidget(accept_line_edit)
        shortcuts_group.setLayout(shortcuts_layout)

        # --- Generation settings ---
        generation_group = QGroupBox("Generation")
        chat_temp_spin = self.create_spinbox(
            "Chat temperature:", "",
            "chat_temperature",
            default=5, min_=0, max_=20, step=1,
            tip="Temperature x10 (e.g., 5 = 0.5). "
                "Lower = more focused, higher = more creative.",
        )
        max_tokens_spin = self.create_spinbox(
            "Max tokens:", "",
            "max_tokens",
            default=1024, min_=64, max_=8192, step=64,
            tip="Maximum number of tokens to generate per response",
        )
        generation_layout = QVBoxLayout()
        generation_layout.addWidget(chat_temp_spin)
        generation_layout.addWidget(max_tokens_spin)
        generation_group.setLayout(generation_layout)

        # --- System prompt ---
        system_group = QGroupBox("System Prompt")
        system_prompt_edit = self.create_textedit(
            "Base system prompt sent with every chat message:",
            "chat_system_prompt",
            default=(
                "You are a helpful AI coding assistant working inside "
                "the Spyder IDE. Be concise and provide code examples "
                "when relevant."
            ),
            tip="This prompt is prepended to every conversation. "
                "Editor context (current file) is appended automatically.",
        )
        system_layout = QVBoxLayout()
        system_layout.addWidget(system_prompt_edit)
        system_group.setLayout(system_layout)

        # --- Action prompts (context menu) ---
        # These templates use {filename} and {code} as placeholders.
        actions_group = QGroupBox(
            "Action Prompts (use {filename} and {code} as placeholders)"
        )

        explain_edit = self.create_textedit(
            "Explain:",
            "prompt_explain",
            default="Explain this code from {filename}:\n\n"
                    "```\n{code}\n```",
        )
        fix_edit = self.create_textedit(
            "Fix:",
            "prompt_fix",
            default="Find and fix bugs in this code from {filename}:\n\n"
                    "```\n{code}\n```",
        )
        docstring_edit = self.create_textedit(
            "Add Docstring:",
            "prompt_docstring",
            default="Add a docstring to this code from {filename}:\n\n"
                    "```\n{code}\n```",
        )
        ask_edit = self.create_textedit(
            "Ask AI (pre-filled context):",
            "prompt_ask",
            default="Regarding this code from {filename}:\n\n"
                    "```\n{code}\n```\n\n",
        )

        actions_layout = QVBoxLayout()
        actions_layout.addWidget(explain_edit)
        actions_layout.addWidget(fix_edit)
        actions_layout.addWidget(docstring_edit)
        actions_layout.addWidget(ask_edit)
        actions_group.setLayout(actions_layout)

        # --- Root layout ---
        vlayout = QVBoxLayout()
        vlayout.addWidget(connection_group)
        vlayout.addWidget(models_group)
        vlayout.addWidget(shortcuts_group)
        vlayout.addWidget(generation_group)
        vlayout.addWidget(system_group)
        vlayout.addWidget(actions_group)
        vlayout.addStretch(1)
        self.setLayout(vlayout)
