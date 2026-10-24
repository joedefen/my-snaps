"""Regression tests for the `*ToDel` policy of `my-restore`.

`get_state()` used to run `btrfs sub del` on every top-level `*ToDel`
subvolume while merely inspecting state. That made `my-restore --dry-run`
destructive: starting the tool deleted data and there was no confirmation.
It also cleaned up subvolumes the user had not finished evaluating, which
contradicts the README's "delay deletion until a restore passes muster".

`get_state()` is now read-only; pending `*ToDel` subvolumes are offered as
explicit menu entries built by `select_restores()` and therefore flow through
`do_command()`, which honors dry-run.

These tests are pure: no btrfs command, mount or root privilege is needed.
`BtrfsRestore.__init__` is bypassed because it re-execs under sudo.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from my_snaps import my_restore

SUB_LIST = """\
ID 256 gen 100 top level 5 path eos@root
ID 257 gen 100 top level 5 path eos@snapshots
ID 265 gen 100 top level 5 path eos@opt
ID 300 gen 100 top level 5 path eos@snapshots/eos@root.2024-01-10-174732=Update
ID 301 gen 100 top level 5 path eos@snapshots/eos@opt.2024-01-13-084102=Daily
ID 400 gen 100 top level 5 path eos@opt.2024-01-14-101010=Reverted
ID 401 gen 100 top level 5 path eos@opt.2024-01-15-101010ToDel
"""

TODEL = 'eos@opt.2024-01-15-101010ToDel'


class FakeMenu:
    """ Stand-in for `InlineMenu.Menu` that picks `selected_key` blindly. """

    selected_key = 'a'
    # `select_restores()` builds keys through `Menu.get_next_key()`.
    get_next_key = staticmethod(my_restore.Menu.get_next_key)

    def __init__(self, prompts, default=None, title=''):
        self.prompts = prompts

    def get_prompt_obj(self):
        key = self.selected_key
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
    inst.root_subvol = 'eos@root'
    inst.is_bootable = True
    inst.slash_mnt = SimpleNamespace(device='/dev/sda1', fstype='btrfs')
    return inst


def forbid_system(monkeypatch):
    """ Fail the test if anything would actually run a command. """
    monkeypatch.setattr(
        my_restore.os, 'system',
        lambda cmd: pytest.fail(f'unexpected command: {cmd!r}'))


# --- get_state() is read-only ---------------------------------------------

def test_get_state_never_deletes_todel_subvolumes(monkeypatch):
    inst = make_instance()
    forbid_system(monkeypatch)
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *a, **k: SUB_LIST.encode())

    state = inst.get_state()

    assert inst.todel_subpaths == [TODEL]
    assert TODEL not in state
    assert state['eos@opt'].revert == 'eos@opt.2024-01-14-101010=Reverted'
    assert state['eos@opt'].snaps == [
        'eos@snapshots/eos@opt.2024-01-13-084102=Daily']


def test_get_state_pending_deletion_is_harmless_in_dry_run(monkeypatch):
    """ A dry-run inspection must not touch the filesystem at all. """
    inst = make_instance(dry_run=True)
    forbid_system(monkeypatch)
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *a, **k: SUB_LIST.encode())

    inst.get_state()

    assert inst.todel_subpaths == [TODEL]


def test_mounted_todel_subvolume_is_not_offered_for_deletion(monkeypatch):
    inst = make_instance()
    inst.mounted_ids = {401}
    forbid_system(monkeypatch)
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *a, **k: SUB_LIST.encode())

    inst.get_state()

    assert inst.todel_subpaths == []


def test_duplicate_todel_lines_are_reported_once(monkeypatch):
    inst = make_instance()
    forbid_system(monkeypatch)
    monkeypatch.setattr(
        my_restore.subprocess, 'check_output',
        lambda *a, **k: (SUB_LIST + SUB_LIST.splitlines()[-1] + '\n').encode())

    inst.get_state()

    assert inst.todel_subpaths == [TODEL]


# --- deletion requires an explicit menu choice ----------------------------

def prepare_restore(monkeypatch, inst, selected_key, state=None):
    """ Wire `select_restores()` up to a fake menu and record commands. """
    FakeMenu.selected_key = selected_key
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.os, 'chdir', lambda path: None)
    monkeypatch.setattr(inst, 'mnt_problem', lambda: '')

    def fake_state():
        inst.todel_subpaths = [TODEL]
        return state if state is not None else {}

    monkeypatch.setattr(inst, 'get_state', fake_state)
    calls = []
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)
    return calls


def test_select_restores_deletes_todel_only_when_chosen(monkeypatch):
    inst = make_instance()
    calls = prepare_restore(monkeypatch, inst, 'a')

    inst.select_restores()

    assert len(calls) == 1
    assert ' '.join(calls[0].split()) == (
        'clear;set -x; btrfs sub del "eos@opt.2024-01-15-101010ToDel"')


def test_select_restores_does_not_delete_todel_in_dry_run(monkeypatch):
    inst = make_instance(dry_run=True)
    calls = prepare_restore(monkeypatch, inst, 'a')

    inst.select_restores()

    assert calls == [
        'echo WOULD + \'\' \'btrfs sub del "eos@opt.2024-01-15-101010ToDel"\'']


def test_reboot_choice_never_contains_a_deletion(monkeypatch):
    """ The `ToDel` entry must not share the key of the reboot entry. """
    inst = make_instance()
    calls = prepare_restore(monkeypatch, inst, 'b')

    inst.select_restores()

    assert len(calls) == 1
    assert 'reboot now' in calls[0]
    assert 'btrfs sub del' not in calls[0]


def test_generated_keys_never_collide_with_dry_run_or_exit(monkeypatch):
    """ `x` and `y` belong to `do_command()`; snapshots must skip them. """
    inst = make_instance()
    snaps = [f'eos@opt.2024-01-{day:02d}-101010=Daily'
             for day in range(1, 30)]
    state = {'eos@opt': SimpleNamespace(revert=None, snaps=snaps)}
    FakeMenu.selected_key = 'a'
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.os, 'chdir', lambda path: None)
    monkeypatch.setattr(inst, 'mnt_problem', lambda: '')

    def fake_state():
        inst.todel_subpaths = [TODEL]
        return state

    monkeypatch.setattr(inst, 'get_state', fake_state)

    seen = {}

    class RecordingMenu(FakeMenu):
        """ Capture the prompt keys that `do_command()` was handed. """
        def __init__(self, prompts, default=None, title=''):
            seen['keys'] = list(prompts)
            super().__init__(prompts, default, title)

    monkeypatch.setattr(my_restore, 'Menu', RecordingMenu)
    monkeypatch.setattr(my_restore.os, 'system', lambda cmd: 0)

    inst.select_restores()

    keys = seen['keys']
    assert keys.count('x') == 1 and keys.count('y') == 1
    assert len(keys) == len(set(keys))
