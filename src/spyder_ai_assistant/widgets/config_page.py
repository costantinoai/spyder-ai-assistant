"""Read-only preferences page that points users to the chat-pane settings."""

from qtpy.QtWidgets import QLabel, QVBoxLayout

from spyder.api.preferences import PluginConfigPage


class AIChatConfigPage(PluginConfigPage):
    """Keep Spyder Preferences aligned with the in-pane settings flow."""

    def setup_page(self):
        layout = QVBoxLayout()

        title = QLabel(
            "<b>AI Chat settings now live in the AI Chat pane.</b>"
        )
        title.setWordWrap(True)

        body = QLabel(
            "Use the AI Chat pane and open <b>Settings</b>. "
            "<b>Assistant Settings...</b> now holds chat/completion models, "
            "generation defaults, prompts, and shortcuts. "
            "Use <b>Settings → Provider Profiles...</b> there to manage "
            "recognized OpenAI-compatible endpoints."
        )
        body.setWordWrap(True)

        layout.addWidget(title)
        layout.addWidget(body)
        layout.addStretch(1)
        self.setLayout(layout)
