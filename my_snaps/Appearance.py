# ruff: noqa: N999
"""Appearance preferences for my-snaps, on top of console-window's layer."""

from console_window import (
    MONO_ROLES,
    Theme,
    colors_enabled,
    resolve_appearance,
    semantic_attr,
    set_colors_enabled,
)

__all__ = [
    'MONO_ROLES',
    'MONO_THEME',
    'appearance_choices',
    'colors_enabled',
    'resolve_ui_prefs',
    'semantic_attr',
    'set_colors_enabled',
]

MONO_THEME = Theme.MONO


def appearance_choices():
    """Every appearance the command line and the picker accept, in order."""
    return Theme.list_all()


def resolve_ui_prefs(cli_theme=None, no_color=False, saved_theme='', environ=None):
    """The theme to start on, from the flags, the environment and the prefs."""
    if no_color:
        return MONO_THEME
    return Theme.resolve(resolve_appearance(
        cli_theme=cli_theme, saved_theme=saved_theme, environ=environ))
