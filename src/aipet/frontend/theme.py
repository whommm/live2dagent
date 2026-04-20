"""Material Design 3 theme system for PySide6 QWidget frontends."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget


class MaterialTheme:
    """MD3 light color scheme (Tonal Spot)."""

    # Primary
    primary = "#6750A4"
    on_primary = "#FFFFFF"
    primary_container = "#EADDFF"
    on_primary_container = "#4F378B"

    # Secondary
    secondary = "#625B71"
    on_secondary = "#FFFFFF"
    secondary_container = "#E8DEF8"
    on_secondary_container = "#1D192B"

    # Surface
    surface = "#FEF7FF"
    on_surface = "#1D1B20"
    on_surface_variant = "#49454F"
    surface_variant = "#E7E0EC"

    # Outline
    outline = "#79747E"
    outline_variant = "#CAC4D0"

    # Error
    error = "#B3261E"
    on_error = "#FFFFFF"
    error_container = "#F9DEDC"
    on_error_container = "#410E0B"

    # Shadow / Elevation
    shadow = "#000000"

    # Success (for online status, etc.)
    success = "#4CAF50"
    on_success = "#FFFFFF"
    success_container = "#E8F5E9"
    on_success_container = "#1B5E20"

    # Inverse (for overlays like scale hint)
    inverse_surface = "#322F35"
    inverse_on_surface = "#F5EFF7"

    # Font
    font_family = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif'

    @classmethod
    def apply_elevation(cls, widget: QWidget, level: int = 1) -> QGraphicsDropShadowEffect:
        """Apply MD3-style shadow to a widget.

        Levels:
        0 = none
        1 = ambient (y=1, blur=3, alpha=26)
        2 = low      (y=2, blur=6, alpha=22)
        3 = medium   (y=4, blur=8, alpha=18)
        4 = high     (y=6, blur=12, alpha=14)
        """
        effect = QGraphicsDropShadowEffect(widget)
        offsets = {0: (0, 0), 1: (0, 1), 2: (0, 2), 3: (0, 4), 4: (0, 6)}
        blurs = {0: 0, 1: 3, 2: 6, 3: 8, 4: 12}
        alphas = {0: 0, 1: 30, 2: 26, 3: 22, 4: 18}
        ox, oy = offsets.get(level, (0, 1))
        blur = blurs.get(level, 3)
        alpha = alphas.get(level, 30)
        effect.setOffset(ox, oy)
        effect.setBlurRadius(blur)
        effect.setColor(QColor(0, 0, 0, alpha))
        widget.setGraphicsEffect(effect)
        return effect

    @classmethod
    def filled_button(cls, bg_color: str | None = None, text_color: str | None = None) -> str:
        bg = bg_color or cls.primary
        fg = text_color or cls.on_primary
        return f"""
            QPushButton {{
                background-color: {bg};
                color: {fg};
                border: none;
                border-radius: 20px;
                padding: 10px 24px;
                font-weight: 500;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {cls._lighten(bg, 8)};
            }}
            QPushButton:pressed {{
                background-color: {cls._darken(bg, 8)};
            }}
            QPushButton:disabled {{
                background-color: {cls.surface_variant};
                color: {cls.on_surface_variant};
            }}
        """

    @classmethod
    def outlined_button(cls, text_color: str | None = None) -> str:
        fg = text_color or cls.primary
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {fg};
                border: 1px solid {cls.outline_variant};
                border-radius: 20px;
                padding: 10px 24px;
                font-weight: 500;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {cls._alpha(cls.primary, 8)};
                border: 1px solid {cls.outline};
            }}
            QPushButton:pressed {{
                background-color: {cls._alpha(cls.primary, 12)};
            }}
            QPushButton:disabled {{
                color: {cls.on_surface_variant};
                border: 1px solid {cls.outline_variant};
            }}
        """

    @classmethod
    def text_button(cls, text_color: str | None = None) -> str:
        fg = text_color or cls.primary
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {fg};
                border: none;
                border-radius: 20px;
                padding: 10px 12px;
                font-weight: 500;
                font-size: 14px;
            }}
            QPushButton:hover {{
                background-color: {cls._alpha(cls.primary, 8)};
            }}
            QPushButton:pressed {{
                background-color: {cls._alpha(cls.primary, 12)};
            }}
        """

    @classmethod
    def outlined_input(cls) -> str:
        return f"""
            QLineEdit, QTextEdit {{
                background-color: {cls.surface};
                color: {cls.on_surface};
                border: 1px solid {cls.outline_variant};
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 14px;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: 2px solid {cls.primary};
                background-color: {cls.surface};
            }}
            QLineEdit:disabled, QTextEdit:disabled {{
                background-color: {cls.surface_variant};
                color: {cls.on_surface_variant};
                border: 1px solid {cls.outline_variant};
            }}
        """

    @classmethod
    def combo_box(cls) -> str:
        return f"""
            QComboBox {{
                background-color: {cls.surface_variant};
                color: {cls.on_surface};
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
                font-weight: 500;
                min-height: 24px;
            }}
            QComboBox:hover {{
                background-color: {cls._alpha(cls.on_surface, 8)};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 24px;
                border: none;
            }}
            QComboBox QAbstractItemView {{
                background-color: {cls.surface};
                color: {cls.on_surface};
                border: 1px solid {cls.outline_variant};
                selection-background-color: {cls.primary_container};
                selection-color: {cls.on_primary_container};
            }}
        """

    @classmethod
    def list_widget_card(cls) -> str:
        return f"""
            QListWidget {{
                background-color: {cls.surface};
                color: {cls.on_surface};
                border: 1px solid {cls.outline_variant};
                border-radius: 12px;
                padding: 8px;
                outline: none;
            }}
            QListWidget::item {{
                background-color: transparent;
                border-radius: 8px;
                padding: 8px 12px;
                margin: 2px 0px;
            }}
            QListWidget::item:selected {{
                background-color: {cls.secondary_container};
                color: {cls.on_secondary_container};
            }}
            QListWidget::item:hover:!selected {{
                background-color: {cls._alpha(cls.on_surface, 4)};
            }}
        """

    @classmethod
    def dialog_bg(cls) -> str:
        return f"background-color: {cls.surface};"

    @classmethod
    def top_bar(cls) -> str:
        return f"""
            QWidget {{
                background-color: {cls.surface};
                border-bottom: 1px solid {cls.outline_variant};
            }}
        """

    @classmethod
    def rgba(cls, hex_color: str, alpha: int) -> str:
        """Convert hex color to rgba string with given alpha (0-255)."""
        c = QColor(hex_color)
        return f"rgba({c.red()}, {c.green()}, {c.blue()}, {alpha})"

    @classmethod
    def _alpha(cls, hex_color: str, percent: int) -> str:
        """Return a black state layer at given opacity percent for MD3 state layers.

        The hex_color argument is kept for API compatibility but ignored;
        MD3 state layers are always black/white overlaid at a fixed opacity.
        """
        alpha = int(255 * percent / 100)
        return f"rgba(0, 0, 0, {alpha})"

    @classmethod
    def _lighten(cls, hex_color: str, percent: int) -> str:
        c = QColor(hex_color)
        factor = 100 + percent
        c.setRed(min(255, int(c.red() * factor / 100)))
        c.setGreen(min(255, int(c.green() * factor / 100)))
        c.setBlue(min(255, int(c.blue() * factor / 100)))
        return c.name()

    @classmethod
    def _darken(cls, hex_color: str, percent: int) -> str:
        c = QColor(hex_color)
        factor = 100 - percent
        c.setRed(max(0, int(c.red() * factor / 100)))
        c.setGreen(max(0, int(c.green() * factor / 100)))
        c.setBlue(max(0, int(c.blue() * factor / 100)))
        return c.name()
