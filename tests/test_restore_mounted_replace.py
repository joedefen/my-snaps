"""Regression tests for replacing a *mounted* subvolume in `my-restore`.

A revert leaves the writable subvolume mounted (typically the live root) and
the replaced tip named `*.YYYY-MM-DD-HHMMSS=Reverted`. Choosing any snapshot
other than the reverted tip used to build

    btrfs sub del "eos@root" && btrfs sub snap <snapshot> "eos@root"

`btrfs sub del` fails with EBUSY on a subvolume that is the root of an active
mount, and the `&&` then skips the snapshot creation. So the README's
"restore a different snapshot, reboot, iterate" loop could not work on the
installed system -- only from a live installer, where no subvolume of the
target filesystem is mounted.

The name is now freed by renaming the mounted subvolume to a timestamped
`*ToDel` name. That is safe because a mount follows the subvolume id, not the
path: the running system keeps reading the old extents until reboot, and the
replaced tip stays recoverable as a `*ToDel` entry that `get_state()` offers
for explicit deletion later.

These tests are pure: no btrfs command, mount or root privilege is needed.
`BtrfsRestore.__init__` is bypassed because it re-execs under sudo.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from my_snaps import my_restore

ROOT = 'eos@root'
REVERT = 'eos@root.2024-01-14-101010=Reverted'
SNAP = 'eos@snapshots/eos@root.2024-01-10-174732=Update'

#: The rename that frees a mounted subvolume's name, with a generated stamp.
ASIDE_RE = re.compile(
    r'mv "eos@root" "(eos@root\.\d{4}-\d\d-\d\d-\d{6}ToDel)" && ')


class FakeMenu:
    """ Stand-in for `InlineMenu.Menu` that picks `selected_key` blindly. """

    selected_key = 'a'
    # `select_restores()` builds keys through `Menu.get_next_key()`.
    get_next_key = staticmethod(my_restore.Menu.get_next_key)

    def __init__(self, prompts, default=None, title=''):
        self.prompts = prompts

    def get_prompt_obj(self):
        key = FakeMenu.selected_key
        return SimpleNamespace(key=key, next=key)

    def get_command(self, choice):
        """ Mirror `Menu.get_command()`: the payload is the second element. """
        prompt = self.prompts[choice.key]
        if isinstance(prompt, (tuple, list)):
            return prompt[1]
        return prompt


def make_instance(dry_run=False):
    """ Build a `BtrfsRestore` without running the sudo re-exec in __init__. """
    inst = object.__new__(my_restore.BtrfsRestore)
    inst.dry_run = dry_run
    inst.filesystems = []
    inst.selected_fs = None
    inst.last_key = None
    inst.mounted_ids = set()
    inst.mounted_subpaths = set()
    inst.todel_subpaths = []
    inst.root_subvol = ROOT
    inst.is_bootable = True
    inst.slash_mnt = SimpleNamespace(device='/dev/sda1', fstype='btrfs')
    return inst


def prepare(monkeypatch, inst, key, state):
    """ Wire `select_restores()` to a fake menu and record commands. """
    FakeMenu.selected_key = key
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.os, 'chdir', lambda path: None)
    monkeypatch.setattr(inst, 'mnt_problem', lambda: '')
    monkeypatch.setattr(inst, 'get_state', lambda: state)
    calls = []
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)
    inst.select_restores()
    return calls


def revert_state():
    """ State left behind by a revert whose tip is still present. """
    return {ROOT: SimpleNamespace(revert=REVERT, snaps=[SNAP])}


def normalize(cmd):
    return ' '.join(cmd.split())


# --- restoring a snapshot other than the reverted tip ----------------------

def test_mounted_subvolume_is_renamed_not_deleted(monkeypatch):
    """ `btrfs sub del` on the live root would fail with EBUSY. """
    inst = make_instance()
    inst.mounted_subpaths = {ROOT}
    # keys: 'a' revert, 'b' del revert, 'c' the snapshot.
    calls = prepare(monkeypatch, inst, 'c', revert_state())

    cmd = normalize(calls[0])

    assert 'btrfs sub del "eos@root"' not in cmd
    assert ASIDE_RE.search(cmd), cmd
    assert cmd.endswith(f'btrfs sub snap "{SNAP}" "eos@root"')


def test_unmounted_subvolume_still_uses_delete(monkeypatch):
    """ Nothing is mounted there, so the cheap `del` path is kept. """
    inst = make_instance()
    calls = prepare(monkeypatch, inst, 'c', revert_state())

    cmd = normalize(calls[0])

    assert not ASIDE_RE.search(cmd)
    assert 'clear;set -x; btrfs sub del "eos@root" && ' in cmd
    assert cmd.endswith(f'btrfs sub snap "{SNAP}" "eos@root"')


def test_mounted_restore_is_only_echoed_in_dry_run(monkeypatch):
    inst = make_instance(dry_run=True)
    inst.mounted_subpaths = {ROOT}
    calls = prepare(monkeypatch, inst, 'c', revert_state())

    assert len(calls) == 1
    assert calls[0].startswith('echo WOULD + ')
    assert 'btrfs sub del' not in calls[0]


# --- revert of a mounted subvolume ----------------------------------------

def test_revert_moves_the_mounted_subvolume_aside(monkeypatch):
    inst = make_instance()
    inst.mounted_subpaths = {ROOT}
    calls = prepare(monkeypatch, inst, 'a', revert_state())

    cmd = normalize(calls[0])

    assert ASIDE_RE.search(cmd), cmd
    assert cmd.endswith(f'mv "{REVERT}" "eos@root"')


def test_aside_name_is_timestamped(monkeypatch):
    """ The old fixed `{subvol}.ToDel` collided with an earlier leftover. """
    inst = make_instance()
    inst.mounted_subpaths = {ROOT}
    calls = prepare(monkeypatch, inst, 'a', revert_state())

    aside = ASIDE_RE.search(normalize(calls[0]))

    assert aside
    assert aside.group(1) != f'{ROOT}.ToDel'


# --- failures are not silent ----------------------------------------------

def test_failed_command_is_reported(monkeypatch, capsys):
    """ `os.system` status used to be discarded, hiding EBUSY-style errors. """
    inst = make_instance()
    FakeMenu.selected_key = 'a'
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.os, 'system', lambda cmd: 256)

    inst.do_command({'a': 'mv "a" "b"'}, once=True, todo='a')

    err = capsys.readouterr().err
    assert 'ERROR: command failed' in err
    assert 'mv "a" "b"' in err


def test_successful_command_is_quiet(monkeypatch, capsys):
    inst = make_instance()
    FakeMenu.selected_key = 'a'
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.os, 'system', lambda cmd: 0)

    inst.do_command({'a': 'mv "a" "b"'}, once=True, todo='a')

    assert capsys.readouterr().err == ''
