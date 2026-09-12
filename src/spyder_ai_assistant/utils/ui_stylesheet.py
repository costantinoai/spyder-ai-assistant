"""Render the design tokens into Qt stylesheets.

Values live in :mod:`ui_tokens`; this module only turns them into QSS, so a
colour or radius is never spelled out here and the two concerns stay apart.

One rule governs everything below. Spyder sets a qdarkstyle-derived sheet on
the main window, and a stylesheet on a descendant **replaces** the matching
rules rather than merging with them. So every selector we touch must also
redefine its hover, pressed, checked and disabled states: styling only the
resting state silently strips the feedback Spyder provided, and the control
ends up looking dead. Where we do not need to restyle something, we leave the
selector alone entirely and let Spyder keep it.

Scoping matters for the same reason. Rules are written against object names
and the pane's own class where possible, so the sheet cannot leak out into
the editor, the consoles, or dialogs we did not intend to touch.
"""

from __future__ import annotations

from spyder_ai_assistant.utils.ui_tokens import UiTokens, tint


def _states(tokens: UiTokens):
    """Hover/pressed washes derived from the accent, used by every control."""
    return {
        "hover": tint(tokens.accent, 0.14),
        "pressed": tint(tokens.accent, 0.22),
        "line": tokens.accent_line,
    }


def transcript_qss(tokens: UiTokens):
    """The scroll area that holds the message bubbles, and its scrollbar.

    The viewport is transparent so the pane's own ground shows through; the
    bubbles supply all the colour.
    """
    return f"""
QScrollArea#aiChatTranscript {{
    background: {tokens.pane};
    border: none;
}}
QScrollArea#aiChatTranscript > QWidget > QWidget {{
    background: transparent;
}}
QScrollArea#aiChatTranscript QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0;
}}
QScrollArea#aiChatTranscript QScrollBar::handle:vertical {{
    background: {tint(tokens.text, 0.20)};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollArea#aiChatTranscript QScrollBar::handle:vertical:hover {{
    background: {tint(tokens.text, 0.34)};
}}
QScrollArea#aiChatTranscript QScrollBar::add-line:vertical,
QScrollArea#aiChatTranscript QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollArea#aiChatTranscript QScrollBar::add-page:vertical,
QScrollArea#aiChatTranscript QScrollBar::sub-page:vertical {{
    background: transparent;
}}
"""


def input_qss(tokens: UiTokens):
    """The composer. A focus ring replaces Qt's default blue outline."""
    surface = tokens.control
    return f"""
ChatInput {{
    background: {surface.gradient()};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius}px;
    padding: {tokens.pad - 2}px {tokens.pad}px;
    color: {tokens.text};
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size}px;
    selection-background-color: {tokens.accent_soft};
}}
ChatInput:focus {{
    border: 1px solid {tokens.accent_line};
    background: {tint(tokens.accent, 0.07)};
}}
ChatInput:disabled {{
    color: {tokens.dim};
    border: 1px solid {tint(tokens.text, 0.10)};
}}
"""


def buttons_qss(tokens: UiTokens):
    """Send and Stop, plus the four tool buttons in the action row.

    Send is the only filled control in the pane, so the accent lands once and
    everything else stays quiet around it.
    """
    states = _states(tokens)
    surface = tokens.control
    return f"""
QPushButton#aiChatSend {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {tokens.accent}, stop:1 {tint(tokens.accent, 0.80)});
    color: {tokens.pane};
    border: none;
    border-radius: {tokens.radius_small}px;
    padding: 6px 18px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size}px;
    font-weight: 600;
}}
QPushButton#aiChatSend:hover {{
    background: {tint(tokens.accent, 0.90)};
}}
QPushButton#aiChatSend:pressed {{
    background: {tint(tokens.accent, 0.70)};
}}
QPushButton#aiChatSend:disabled {{
    background: {tint(tokens.text, 0.08)};
    color: {tokens.dim};
}}
QPushButton#aiChatStop {{
    background: {surface.gradient()};
    color: {tokens.text};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius_small}px;
    padding: 6px 16px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size}px;
}}
QPushButton#aiChatStop:hover {{
    background: {states['hover']};
    border: 1px solid {states['line']};
}}
QPushButton#aiChatStop:pressed {{
    background: {states['pressed']};
}}
QPushButton#aiChatStop:disabled {{
    color: {tokens.dim};
}}
ChatWidget QToolButton {{
    background: transparent;
    color: {tokens.text};
    border: 1px solid transparent;
    border-radius: {tokens.radius_small}px;
    padding: 5px 9px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size - 1}px;
}}
ChatWidget QToolButton:hover {{
    background: {states['hover']};
    border: 1px solid {states['line']};
}}
ChatWidget QToolButton:pressed,
ChatWidget QToolButton:checked {{
    background: {states['pressed']};
}}
ChatWidget QToolButton:disabled {{
    color: {tokens.dim};
}}
ChatWidget QToolButton::menu-indicator {{
    image: none;
    width: 0;
}}
"""


def combo_qss(tokens: UiTokens):
    """Model and console selectors, and the popup list they open."""
    surface = tokens.control
    states = _states(tokens)
    return f"""
ChatWidget QComboBox {{
    background: {surface.gradient()};
    color: {tokens.text};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius_small}px;
    padding: 4px 10px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size - 1}px;
}}
ChatWidget QComboBox:hover {{
    border: 1px solid {states['line']};
}}
ChatWidget QComboBox:disabled {{
    color: {tokens.dim};
}}
ChatWidget QComboBox::drop-down {{
    border: none;
    width: 18px;
}}
ChatWidget QComboBox QAbstractItemView {{
    background: {surface.mid};
    color: {tokens.text};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius_small}px;
    padding: 4px;
    outline: none;
    selection-background-color: {tokens.accent_soft};
    selection-color: {tokens.text};
}}
"""


def tabs_qss(tokens: UiTokens):
    """Conversation tabs. The active tab is the one carrying the accent."""
    states = _states(tokens)
    return f"""
ChatWidget QTabWidget::pane {{
    border: none;
    background: transparent;
}}
ChatWidget QTabBar {{
    background: transparent;
    qproperty-drawBase: 0;
}}
ChatWidget QTabBar::tab {{
    background: transparent;
    color: {tokens.dim};
    border: 1px solid transparent;
    border-radius: {tokens.radius_small}px;
    padding: 5px 12px;
    margin-right: 4px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size - 1}px;
}}
ChatWidget QTabBar::tab:hover {{
    background: {states['hover']};
    color: {tokens.text};
}}
ChatWidget QTabBar::tab:selected {{
    background: {tokens.accent_soft};
    color: {tokens.text};
    border: 1px solid {tokens.accent_line};
}}
ChatWidget QTabBar::close-button {{
    subcontrol-position: right;
}}
"""


def labels_qss(tokens: UiTokens):
    """Status, context, hint and the inline provider problem line."""
    return f"""
ChatWidget QLabel {{
    background: transparent;
    color: {tokens.text};
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size - 1}px;
}}
QLabel#aiChatStatus, QLabel#aiChatHint, QLabel#aiChatContext,
QLabel#aiChatRuntime {{
    color: {tokens.dim};
    font-size: {tokens.label_size + 1}px;
}}
QLabel#aiChatProviderIssue {{
    color: {tokens.text};
    background: {tint(tokens.accent, 0.10)};
    border: 1px solid {tokens.accent_line};
    border-radius: {tokens.radius_small}px;
    padding: 6px 10px;
    font-size: {tokens.label_size + 1}px;
}}
"""


def dialog_qss(tokens: UiTokens):
    """Settings, provider profiles, history and the other dialogs.

    Scoped by class so the same visual language reaches every dialog the
    assistant opens without touching Spyder's own.
    """
    surface = tokens.control
    states = _states(tokens)
    return f"""
QDialog#aiChatDialog {{
    background: {tokens.pane};
}}
QDialog#aiChatDialog QGroupBox {{
    background: {surface.gradient()};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius}px;
    margin-top: 14px;
    padding: {tokens.pad}px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size}px;
    color: {tokens.text};
}}
QDialog#aiChatDialog QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: {tokens.pad}px;
    padding: 0 6px;
    color: {tokens.dim};
    font-size: {tokens.label_size + 1}px;
}}
QDialog#aiChatDialog QTabBar::tab {{
    background: transparent;
    color: {tokens.dim};
    border: 1px solid transparent;
    border-radius: {tokens.radius_small}px;
    padding: 6px 14px;
    margin-right: 4px;
}}
QDialog#aiChatDialog QTabBar::tab:selected {{
    background: {tokens.accent_soft};
    color: {tokens.text};
    border: 1px solid {tokens.accent_line};
}}
QDialog#aiChatDialog QTabBar::tab:hover {{
    background: {states['hover']};
    color: {tokens.text};
}}
QDialog#aiChatDialog QLineEdit,
QDialog#aiChatDialog QSpinBox,
QDialog#aiChatDialog QDoubleSpinBox,
QDialog#aiChatDialog QPlainTextEdit,
QDialog#aiChatDialog QTextEdit,
QDialog#aiChatDialog QComboBox {{
    background: {tint(tokens.text, 0.06)};
    color: {tokens.text};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius_small}px;
    padding: 4px 8px;
    selection-background-color: {tokens.accent_soft};
}}
QDialog#aiChatDialog QLineEdit:focus,
QDialog#aiChatDialog QSpinBox:focus,
QDialog#aiChatDialog QDoubleSpinBox:focus,
QDialog#aiChatDialog QPlainTextEdit:focus,
QDialog#aiChatDialog QTextEdit:focus,
QDialog#aiChatDialog QComboBox:focus {{
    border: 1px solid {tokens.accent_line};
}}
QDialog#aiChatDialog QPushButton {{
    background: {surface.gradient()};
    color: {tokens.text};
    border: 1px solid {surface.border};
    border-radius: {tokens.radius_small}px;
    padding: 6px 16px;
    font-family: "{tokens.ui_font}";
    font-size: {tokens.font_size}px;
}}
QDialog#aiChatDialog QPushButton:hover {{
    background: {states['hover']};
    border: 1px solid {states['line']};
}}
QDialog#aiChatDialog QPushButton:pressed {{
    background: {states['pressed']};
}}
QDialog#aiChatDialog QPushButton:default {{
    background: qlineargradient(x1:0,y1:0,x2:0,y2:1,
        stop:0 {tokens.accent}, stop:1 {tint(tokens.accent, 0.80)});
    color: {tokens.pane};
    border: none;
    font-weight: 600;
}}
QDialog#aiChatDialog QPushButton:disabled {{
    color: {tokens.dim};
}}
"""


def pane_stylesheet(tokens: UiTokens):
    """The whole pane in one sheet: transcript, composer, controls, tabs."""
    return "".join((
        f"ChatWidget {{ background: {tokens.pane}; }}",
        transcript_qss(tokens),
        input_qss(tokens),
        buttons_qss(tokens),
        combo_qss(tokens),
        tabs_qss(tokens),
        labels_qss(tokens),
    ))
