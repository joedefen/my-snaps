#!/usr/bin/env python3
"""Appearance preferences for my-snaps, on top of console-window's layer.

console-window owns the semantic color roles: ``MONO_ROLES``, the
process-wide color switch and :func:`semantic_attr` all live in
:mod:`console_window.appearance`.  This module re-exports them under the names
my-snaps has always used, so the draw code and the tests keep one import, and
adds only what is genuinely application-specific:

* ``mono``, my-snaps' no-color pseudo-theme.  console-window's
  ``dark-mono``/``light-mono`` are still color palettes, so the true
  monochrome appearance is the role-to-attribute mapping the library installs
  when color is off.  ``mono`` joins the picker's rotation as the final
  choice and is not a library theme.
* :func:`appearance_choices` / :func:`appearance_label`, the picker's
  vocabulary.
* :func:`resolve_ui_prefs`, the start-up decision, which normalizes ``mono``
  and then defers to :func:`console_window.appearance.resolve_appearance`.
"""

import os

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
    'appearance_label',
    'colors_enabled',
    'resolve_ui_prefs',
    'semantic_attr',
    'set_colors_enabled',
]

# my-snaps' pseudo-theme: no color at all, only attributes.
MONO_THEME = 'mono'


def appearance_choices():
    """Every appearance the picker cycles, in order.

    console-window's themes come first (in ``Theme.THEMES`` order), then
    my-snaps' no-color pseudo-theme, so one cycle always reaches ``mono``.
    """
    return list(Theme.list_all()) + [MONO_THEME]


def appearance_label(theme, colors):
    """The name to show for the live appearance."""
    if not colors:
        return MONO_THEME
    return theme or 'default'


def resolve_ui_prefs(cli_theme=None, no_color=False, saved_theme='',
                     saved_colors=True, environ=None):
    """Starting ``(theme, colors)`` from the flag, the environment and the prefs.

    ``mono`` is a my-snaps pseudo-theme, not a library palette, so it is
    translated to the library's "no color" decision (``theme=''`` plus
    ``colors=False``) before the rest is delegated to
    :func:`console_window.appearance.resolve_appearance`.  That keeps one
    precedence rule for the whole family: ``--no-color``, then an explicit
    ``--theme``, then ``NO_COLOR``, then the remembered choice.

    The saved branch reports ``'default'`` rather than ``''`` when nothing was
    remembered, because the picker's header shows a name, not an empty string.
    """
    env = os.environ if environ is None else environ
    if cli_theme == MONO_THEME:
        return '', False
    if cli_theme:
        return resolve_appearance(cli_theme=cli_theme, no_color=no_color,
                                  environ=env)
    if saved_theme == MONO_THEME:
        return '', False
    theme, colors = resolve_appearance(
        no_color=no_color, saved_theme=saved_theme,
        saved_colors=saved_colors, environ=env)
    return theme or 'default', colors
