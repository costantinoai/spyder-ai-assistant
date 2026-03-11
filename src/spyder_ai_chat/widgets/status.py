"""Status bar widget for the AI completion provider.

Shows the current state of the AI completion system in Spyder's status bar:
- "AI: model-name" when ready and enabled
- "AI: disabled" when completions are turned off
- "AI: offline" when the Ollama server is unreachable

Registered via STATUS_BAR_CLASSES on the completion provider.
"""

import logging

from spyder.api.widgets.status import StatusBarWidget

logger = logging.getLogger(__name__)


class AIChatCompletionStatus(StatusBarWidget):
    """Status bar widget showing AI completion provider state.

    Displays a short label in Spyder's status bar indicating whether
    the AI completion system is active and which model is in use.
    Updated via sig_call_statusbar from the completion provider.
    """

    # Unique ID for signal routing from the provider
    ID = "ai_chat_completion_status"

    def get_tooltip(self):
        """Tooltip shown on hover over the status bar item."""
        return "AI Code Completion status (Ollama)"

    def set_value(self, value):
        """Update the displayed status text.

        Called via sig_call_statusbar.emit(ID, "set_value", (value,), {})
        from the completion provider. Accepts either a plain string or
        a dict with 'short' and 'long' keys (for tooltip).

        Args:
            value: Status string (e.g., "AI: model-name") or dict with
                   'short' (display text) and 'long' (tooltip) keys.
        """
        if isinstance(value, dict):
            self.setToolTip(value.get("long", self.get_tooltip()))
            value = value.get("short", "")
        super().set_value(value)
