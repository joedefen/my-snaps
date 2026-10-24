"""Regression tests for the menu's arrow-key handling.

`get_prompt_obj()` used to match `\\033[C` (right arrow) in both the up/left
and the down/right branch, while `\\033[D` (left) was ignored.  The mapping
now lives in `Menu.ARROW_DELTAS`, which is pure and testable without a tty.
"""
from __future__ import annotations

import pytest
from my_snaps.InlineMenu import Menu


@pytest.mark.parametrize("sequence, delta", [
    ('\033[A', -1),  # up
    ('\033[D', -1),  # left
    ('\033[B', 1),   # down
    ('\033[C', 1),   # right
])
def test_every_arrow_has_exactly_one_direction(sequence, delta):
    assert Menu.arrow_delta(sequence) == delta


@pytest.mark.parametrize("other", [
    'a', 'A', '\r', '\n', ' ', '\033', '\033[H', '',
])
def test_non_arrows_do_not_move_the_cursor(other):
    assert Menu.arrow_delta(other) is None
