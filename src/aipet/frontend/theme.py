"""Material Design 3 theme system for PySide6 QWidget frontends."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QWidget


class MaterialTheme:
    """Soft desktop theme tokens for the live2dagent QWidget frontend."""

    # Primary
    primary = "#4F67A5"
    on_primary = "#FFFFFF"
    primary_container = "#E8EEFF"
    on_primary_container = "#1E2F5F"

    # Secondary
    secondary = "#4F6F68"
    on_secondary = "#FFFFFF"
    secondary_container = "#DDEDE8"
    on_secondary_container = "#16342E"

    # Surface
    surface = "#FBFAFD"
    surface_container = "#FFFFFF"
    surface_container_high = "#F2F4F7"
    on_surface = "#171A1F"
    on_surface_variant = "#59606C"
    surface_variant = "#EEF0F5"

    # Outline
    outline = "#7E8794"
    outline_variant = "#D8DDE6"

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

    radius_sm = 6
    radius_md = 8
    radius_lg = 12

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
                border-radius: {cls.radius_md}px;
                padding: 8px 16px;
                font-weight: 600;
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
                border-radius: {cls.radius_md}px;
                padding: 8px 16px;
                font-weight: 600;
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
                border-radius: {cls.radius_md}px;
                padding: 8px 10px;
                font-weight: 600;
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
                background-color: {cls.surface_container};
                color: {cls.on_surface};
                border: 1px solid {cls.outline_variant};
                border-radius: {cls.radius_md}px;
                padding: 8px 12px;
                font-size: 14px;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: 2px solid {cls.primary};
                background-color: {cls.surface_container};
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
                background-color: {cls.surface_container_high};
                color: {cls.on_surface};
                border: 1px solid {cls.outline_variant};
                border-radius: {cls.radius_md}px;
                padding: 6px 12px;
                font-weight: 600;
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
                background-color: {cls.surface_container};
                border-bottom: 1px solid {cls.outline_variant};
            }}
        """

    @classmethod
    def icon_button(cls, size: int = 34, danger: bool = False) -> str:
        hover_bg = cls.error if danger else cls._alpha(cls.on_surface, 7)
        hover_fg = cls.on_error if danger else cls.on_surface
        font_size = 12 if size <= 26 else 14
        return f"""
            QPushButton {{
                background-color: transparent;
                color: {cls.on_surface_variant};
                border: none;
                border-radius: {size // 2}px;
                padding: 0px;
                font-size: {font_size}px;
                font-weight: 600;
            }}
            QPushButton:hover {{
                background-color: {hover_bg};
                color: {hover_fg};
            }}
            QPushButton:pressed {{
                background-color: {cls._alpha(cls.on_surface, 12)};
            }}
            QPushButton:disabled {{
                color: {cls.outline_variant};
                background-color: transparent;
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
