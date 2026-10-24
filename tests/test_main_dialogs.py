"""Regression tests for the ConsoleWindow dialogs driven by my_snaps.main.

`ConsoleWindow.answer()` returns ``None`` when the user aborts the dialog
(ESC). Callers must treat that as "no answer" instead of dereferencing it,
which is what crashed `_del_subvolume()` with
``AttributeError: 'NoneType' object has no attribute 'strip'``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from my_snaps.main import BTRFS


def make_btrfs(win):
    """Build a BTRFS instance without touching the real BTRFS state."""
    self = BTRFS.__new__(BTRFS)
    self.win = win
    self.rows = []
    self.devs = {}
    self.label = None
    self.label_set = set()
    self.add_limit = 0
    self.snap_targets = []
    self.dirty = False
    return self


def make_subvol(path='/@root'):
    return BTRFS.init_subvol_ns(dev='dev0', path=path, ident='256')


class FakeWin:
    """Records the dialogs it is asked to show and replays canned answers."""

    def __init__(self, answer=None, pick_pos=0):
        self.answer_value = answer
        self.pick_pos = pick_pos
        self.answers = []
        self.alerts = []

    def answer(self, prompt='', seed='', **kwargs):
        self.answers.append(prompt)
        return self.answer_value

    def alert(self, message='', title='ALERT', **_kwargs):
        self.alerts.append((title, message))


def test_get_label_handles_none():
    assert BTRFS._get_label(None) == ''
    assert BTRFS._get_label('') == ''
    assert BTRFS._get_label('@snapshots/root.2024-01-10-174732=Update') == '=Update'


def test_del_subvolume_abort_is_not_a_yes():
    subvol = make_subvol()
    win = FakeWin(answer=None)  # user pressed ESC in the y/N dialog
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: pytest.fail(f'unexpected command: {cmd}')

    assert self._del_subvolume(subvol) is False
    assert win.answers, 'the confirmation dialog should have been shown'
    assert self.dirty is False


def test_del_subvolume_requires_y():
    subvol = make_subvol()
    self = make_btrfs(FakeWin(answer='nope'))
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: pytest.fail(f'unexpected command: {cmd}')

    assert self._del_subvolume(subvol) is False


def test_del_subvolume_reports_failed_command():
    subvol = make_subvol()
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: (['out'], ['err'], 1)

    # ans='y' skips the dialog; a failing "btrfs sub del" must alert, not raise.
    assert self._del_subvolume(subvol, ans='y') is False
    assert win.alerts and 'FAILED' in win.alerts[0][1]
    assert 'err' in win.alerts[0][1]
    assert self.dirty is False


def test_del_subvolume_deletes_with_yes():
    subvol = make_subvol()
    self = make_btrfs(FakeWin())

    class Device:
        tmp_path = '/tmp/.btrfs/dev0'

    self.devs['dev0'] = Device()
    commands = []
    self._slurp_command = lambda cmd: (commands.append(cmd) or ([], [], 0))

    assert self._del_subvolume(subvol, ans='y') is True
    assert commands == ['btrfs sub del /tmp/.btrfs/dev0/@root']
    assert self.dirty is True


def test_replace_eldest_snaps_abort_on_esc():
    win = FakeWin(answer=None)  # user pressed ESC in the suffix dialog
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]

    # Must return (None, as before) instead of calling _get_label(None).
    assert self._replace_eldest_snaps() is None
    assert self.label is None
