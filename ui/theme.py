"""
Theme: palette, Arabic typography, RTL policy.

The look target is "premium modern legal software" — deep navy for authority,
slate for structure, parchment for the reading surface, and brass used sparingly
as the single accent. Restraint is the point: no gradients, no shadows heavier
than a hairline.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

from core import config

T = config.THEME


def resolve_font(candidates: list[str], fallback: str = "Segoe UI") -> str:
    """First installed family from `candidates`, else a known-safe fallback."""
    available = set(QFontDatabase.families())
    for name in candidates:
        if name in available:
            return name
    return fallback


def apply_theme(app: QApplication) -> None:
    """Install fonts, layout direction, and the global stylesheet."""
    # -- Arabic typography -------------------------------------------------
    family = resolve_font(config.FONT_STACK)
    base = QFont(family, config.BASE_FONT_SIZE)
    base.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(base)

    # -- Direction ---------------------------------------------------------
    from PyQt6.QtCore import Qt

    app.setLayoutDirection(
        Qt.LayoutDirection.RightToLeft if config.UI_RTL else Qt.LayoutDirection.LeftToRight
    )

    app.setStyleSheet(build_stylesheet(family))


def build_stylesheet(font_family: str) -> str:
    """Load the dedicated QSS file and substitute {{token}} placeholders.

    Keeping the palette in ``config.THEME`` (and the type sizes in the font-size
    constants) makes ``core/config.py`` the single source of truth: the stylesheet
    only holds ``{{token}}`` markers, so there is no hex duplication to drift. The
    double-brace marker never collides with QSS's own single braces.
    """
    qss = (Path(__file__).with_name("styles.qss")).read_text(encoding="utf-8")
    tokens = {
        **config.THEME,
        "font": font_family,
        "base_pt": config.BASE_FONT_SIZE,
        "heading_pt": config.HEADING_FONT_SIZE,
        "title_pt": config.TITLE_FONT_SIZE,
    }
    for key, val in tokens.items():
        qss = qss.replace("{{%s}}" % key, str(val))
    return qss


def apply_shadow(widget, radius: int = 26, dy: int = 6, alpha: int = 70) -> None:
    """Give a widget a single soft drop shadow so it reads as a floating card.

    Qt allows exactly one graphics effect per widget, so this is applied only to
    the handful of top-level content cards (never to widgets living inside a
    QScrollArea viewport, which would clip the shadow).
    """
    from PyQt6.QtGui import QColor
    from PyQt6.QtWidgets import QGraphicsDropShadowEffect

    eff = QGraphicsDropShadowEffect(widget)
    eff.setBlurRadius(radius)
    eff.setXOffset(0)
    eff.setYOffset(dy)
    eff.setColor(QColor(4, 12, 20, alpha))   # near-black navy, soft
    widget.setGraphicsEffect(eff)


def badge(text: str, tone: str = "navy") -> str:
    """Inline HTML pill for chat transcript and status lines."""
    colour = {
        "navy": T["navy"], "brass": T["brass"], "muted": T["muted"],
        "danger": T["danger"], "success": T["success"],
    }.get(tone, T["navy"])
    return (
        f'<span style="background:{colour};color:#FBF8F1;border-radius:9px;'
        f'padding:1px 8px;font-size:9pt;">{text}</span>'
    )
