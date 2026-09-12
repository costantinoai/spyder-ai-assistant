"""One owner for every visual decision in the assistant's interface.

Colour, typography, radius and spacing all resolve here, so a change lands in
one place instead of being retyped into widget stylesheets and message HTML.
Two rules in particular are easy to get wrong and are therefore centralised:

**Surfaces are solved, not hardcoded.** A bubble has to lift off whatever pane
sits behind it in six presets across light and dark. Stepping HSL lightness by
a constant does not achieve that: sRGB luminance is steeply non-linear near
white, so a step that reads clearly on a dark ground is nearly invisible on a
cream one (measured 1.31-1.55 on dark presets against 1.07-1.11 on light ones
for the same step). So walk the lightness away from the pane until the pair
reaches a target contrast ratio.

**Saturation bleeds as the step grows.** Targeting the ratio alone turned
solarized light amber and gruvbox light mustard, because holding saturation
while dropping lightness on an already saturated cream intensifies the chroma,
and those palettes need the largest steps. A surface travelling far from its
ground should read as a surface, not as a colour statement. Since desaturating
also moves luminance, the decay happens inside the search rather than after it.

Fonts are resolved against what is actually installed. Qt substitutes a missing
family silently, which is how an interface looks correct to its author and
wrong to everybody else.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from qtpy.QtGui import QColor, QFontDatabase
from qtpy.QtWidgets import QApplication


# --- Typography --------------------------------------------------------------
# Ordered by preference. Clarity at small sizes on a dark ground wants a large
# x-height and open apertures, which is why Inter leads; the rest are ordinary
# system faces so the chain always terminates in something sane.
UI_FONT_CANDIDATES = (
    "Inter",
    "IBM Plex Sans",
    "Source Sans 3",
    "Source Sans Pro",
    "Noto Sans",
    "Open Sans",
    "Ubuntu",
    "Cantarell",
    "Roboto",
    "DejaVu Sans",
    "Liberation Sans",
)

MONO_FONT_CANDIDATES = (
    "JetBrains Mono",
    "Fira Code",
    "Cascadia Code",
    "Source Code Pro",
    "IBM Plex Mono",
    "Roboto Mono",
    "Ubuntu Mono",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Monospace",
)

_font_cache: dict[tuple[str, ...], str] = {}


def resolve_font(candidates=UI_FONT_CANDIDATES, fallback="sans-serif"):
    """Return the first installed family, or `fallback` when none is present.

    Cached because QFontDatabase construction is not free and this is consulted
    on every stylesheet rebuild.
    """
    key = tuple(candidates)
    if key in _font_cache:
        return _font_cache[key]

    # QFontDatabase aborts the process, rather than raising, when no
    # QApplication exists yet. Importing this module must never be able to
    # kill its caller, so answer with the fallback instead of asking Qt.
    if QApplication.instance() is None:
        return fallback

    try:
        installed = set(QFontDatabase().families())
    except Exception:  # pragma: no cover - only when Qt has no font backend
        installed = set()
    chosen = next((name for name in candidates if name in installed), fallback)
    _font_cache[key] = chosen
    return chosen


# --- Contrast ----------------------------------------------------------------
def relative_luminance(color):
    """WCAG relative luminance for a QColor."""
    def channel(value):
        value /= 255.0
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.red())
        + 0.7152 * channel(color.green())
        + 0.0722 * channel(color.blue())
    )


def contrast_ratio(first, second):
    """Contrast ratio between two QColors, lighter over darker."""
    high = max(relative_luminance(first), relative_luminance(second))
    low = min(relative_luminance(first), relative_luminance(second))
    return (high + 0.05) / (low + 0.05)


# --- Surfaces ----------------------------------------------------------------
SURFACE_TARGET_RATIO = 1.25
SURFACE_FLOOR_RATIO = 1.20
_SATURATION_DECAY_SCALE = 95.0
_MAX_SATURATION_DECAY = 0.62


def _stepped(hue, saturation, lightness, alpha, direction, step):
    """One candidate surface: lightness moved by `step`, chroma bled by it."""
    moved = max(0, min(255, lightness + direction * step))
    decay = 1.0 - min(_MAX_SATURATION_DECAY, step / _SATURATION_DECAY_SCALE)
    return QColor.fromHsl(hue, int(saturation * decay), moved, alpha)


@dataclass(frozen=True)
class Surface:
    """A drawable surface: gradient stops, its flat mid tone, and a hairline."""

    top: str
    mid: str
    bottom: str
    border: str
    ratio: float

    def gradient(self, vertical=True):
        """QSS gradient string for this surface."""
        coords = "x1:0,y1:0,x2:0,y2:1" if vertical else "x1:0,y1:0,x2:1,y2:1"
        return (
            f"qlineargradient({coords},"
            f"stop:0 {self.top}, stop:1 {self.bottom})"
        )


def solve_surface(behind, is_dark, target=SURFACE_TARGET_RATIO, spread=None,
                  lighter=None):
    """Build a surface that provably separates from the colour behind it.

    Walks lightness away from `behind` (up on dark grounds, down on light ones),
    bleeding saturation as it goes, and stops at the first step that reaches
    `target`. When a palette cannot reach the target at all, the best available
    step is used rather than failing, so an extreme preset degrades instead of
    breaking.

    `lighter` overrides that direction for surfaces whose role fixes it. A code
    card has to recede whatever the theme: lifting it on a dark theme not only
    reads wrong, it makes the card light enough that the syntax highlighter
    picks its *light* palette, so a dark theme ends up with navy keywords on a
    grey block.
    """
    pane = QColor(behind)
    if not pane.isValid():
        pane = QColor("#808080")
    hue, saturation, lightness, alpha = pane.getHsl()
    go_lighter = is_dark if lighter is None else lighter
    direction = 1 if go_lighter else -1

    best_step, best_ratio, best = 0, 1.0, pane
    for step in range(1, 140):
        candidate = _stepped(hue, saturation, lightness, alpha, direction, step)
        ratio = contrast_ratio(pane, candidate)
        if ratio > best_ratio:
            best_step, best_ratio, best = step, ratio, candidate
        if ratio >= target:
            break
        if candidate.lightness() in (0, 255):
            break

    mid_hue, mid_saturation, mid_lightness, mid_alpha = best.getHsl()
    gap = spread if spread is not None else max(5, best_step // 2)
    top = QColor.fromHsl(
        mid_hue, mid_saturation, max(0, min(255, mid_lightness + gap)), mid_alpha
    )
    bottom = QColor.fromHsl(
        mid_hue, mid_saturation, max(0, min(255, mid_lightness - gap)), mid_alpha
    )
    border = top.lighter(115) if is_dark else bottom.darker(107)
    return Surface(
        top=top.name(),
        mid=best.name(),
        bottom=bottom.name(),
        border=border.name(),
        ratio=round(best_ratio, 3),
    )


def tint(color, alpha):
    """An rgba() string for `color` at `alpha` (0-1), for accent washes."""
    base = QColor(color)
    if not base.isValid():
        base = QColor("#808080")
    return f"rgba({base.red()},{base.green()},{base.blue()},{alpha:.3f})"


# --- The token set -----------------------------------------------------------
@dataclass(frozen=True)
class UiTokens:
    """Every value the interface draws with, resolved for one theme."""

    is_dark: bool
    ui_font: str
    mono_font: str

    pane: str
    text: str
    dim: str
    accent: str
    accent_soft: str
    accent_line: str

    user: Surface
    assistant: Surface
    control: Surface
    code_bg: str
    code_text: str

    radius: int = 14
    radius_small: int = 10
    radius_pill: int = 11
    gap: int = 10
    pad: int = 12
    font_size: int = 13
    mono_size: int = 12
    label_size: int = 10

    extra: dict = field(default_factory=dict)


def ghost_palette(tokens):
    """Colours for an inline suggestion, derived from the assistant's accent.

    Returns the ``(foreground, background, underline)`` triple the ghost text
    manager expects. Three properties are load-bearing and must survive any
    recolour: the background keeps an alpha, the underline stays dotted, and
    the text stays italic, because that combination is how the manager
    recognises its own selection when clearing it.

    The suggestion sits on the editor's ground, not the chat pane's, so the
    accent is softened rather than used at full strength: it has to read as
    provisional text, not as a highlight.
    """
    accent = QColor(tokens.accent)
    if not accent.isValid():
        accent = QColor("#5ac8fa")

    hue, saturation, lightness, _ = accent.getHsl()
    if tokens.is_dark:
        foreground = QColor.fromHsl(hue, int(saturation * 0.55), min(235, lightness + 40))
        background = QColor.fromHsl(hue, int(saturation * 0.60), max(38, lightness - 62))
        background.setAlpha(150)
    else:
        foreground = QColor.fromHsl(hue, int(saturation * 0.70), max(60, lightness - 55))
        background = QColor.fromHsl(hue, int(saturation * 0.45), min(232, lightness + 78))
        background.setAlpha(130)
    underline = QColor(foreground)
    return foreground, background, underline


def build_tokens(theme_colors, is_dark):
    """Resolve one theme's colour dict into the full token set.

    `theme_colors` is the mapping `chat_themes.get_theme_colors` returns, so
    presets and user overrides flow through unchanged and this module never
    needs its own palette.
    """
    pane = theme_colors.get("assistant_bg", "#252525" if is_dark else "#f0f0f0")
    accent = theme_colors.get("link_color", "#5ac8fa")

    return UiTokens(
        is_dark=is_dark,
        ui_font=resolve_font(UI_FONT_CANDIDATES),
        mono_font=resolve_font(MONO_FONT_CANDIDATES, fallback="monospace"),
        pane=pane,
        text=theme_colors.get("assistant_text", "#e8ecf6" if is_dark else "#1a1a1a"),
        dim=theme_colors.get("assistant_label", "#8b93a7"),
        accent=accent,
        accent_soft=tint(accent, 0.15),
        accent_line=tint(accent, 0.34),
        user=solve_surface(theme_colors.get("user_bg", accent), is_dark),
        assistant=solve_surface(pane, is_dark),
        control=solve_surface(pane, is_dark, target=1.18),
        code_bg=theme_colors.get("code_block_bg", "#1e1e1e"),
        code_text=theme_colors.get("code_block_text", "#dcdcdc"),
    )
