"""Coverage for the semantic colour roles and the appearance preferences.

These are pure functions: no window, no terminal.  The monochrome mapping is
the one that must stay legible when a terminal has no colour at all.
"""
from __future__ import annotations

import curses

import my_snaps.Appearance as appearance
import pytest
from console_window import Theme


def test_semantic_attr_uses_mono_roles_when_colour_is_off():
    appearance.set_colors_enabled(False)
    try:
        assert appearance.semantic_attr(Theme.DANGER) == (
            curses.A_BOLD | curses.A_REVERSE)
        assert appearance.semantic_attr(Theme.HOTSWAP) == curses.A_NORMAL
        assert appearance.semantic_attr(999) == curses.A_NORMAL
    finally:
        appearance.set_colors_enabled(True)


def test_semantic_attr_degrades_without_curses_colour():
    appearance.set_colors_enabled(True)
    # Tests never call initscr(), so has_colors() is false; a role must never
    # raise and must degrade to plain text.
    assert appearance.semantic_attr(Theme.DANGER) == 0


def test_colors_enabled_round_trips():
    appearance.set_colors_enabled(False)
    assert appearance.colors_enabled() is False
    appearance.set_colors_enabled(True)
    assert appearance.colors_enabled() is True


def test_appearance_choices_end_with_mono():
    choices = appearance.appearance_choices()

    assert choices[-1] == appearance.MONO_THEME
    assert choices[:-1] == Theme.list_all()


def test_appearance_label_reports_mono_when_colour_is_off():
    assert appearance.appearance_label('gruvbox', False) == appearance.MONO_THEME
    assert appearance.appearance_label('', True) == 'default'
    assert appearance.appearance_label('nord', True) == 'nord'


@pytest.mark.parametrize('kwargs,expected', [
    ({'cli_theme': 'mono'}, ('', False)),
    ({'cli_theme': 'nord'}, ('nord', True)),
    ({'cli_theme': 'nord', 'no_color': True}, ('nord', False)),
    ({'saved_theme': 'mono'}, ('', False)),
    ({'saved_theme': 'gruvbox'}, ('gruvbox', True)),
    ({'saved_theme': 'gruvbox', 'saved_colors': False}, ('gruvbox', False)),
    ({'environ': {'NO_COLOR': '1'}}, ('default', False)),
    ({'cli_theme': 'mono', 'environ': {'NO_COLOR': '1'}}, ('', False)),
])
def test_resolve_ui_prefs_precedence(kwargs, expected):
    kwargs.setdefault('environ', {})

    assert appearance.resolve_ui_prefs(**kwargs) == expected
