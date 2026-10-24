"""Regression tests for the /efi mirror built by `my-restore`.

`sync_cmd()` used to emit::

    rsync -a -del -H "<snapshot>/.efi-back/" "/efi/"

A single dash is not an abbreviation of `--delete`: rsync parses the cluster
as `-d` (--dirs) plus `-e l` (--rsh=l), so the remote shell becomes the
nonexistent command ``l`` and no deletion ever happens.  The copy exits 0, the
following ``&&`` chain continues, and the ESP keeps kernels and boot entries
that the restored root no longer has -- exactly the failure the `.efi-back`
workaround exists to prevent, invisible to `check_bootable()` (which only
looks for overlap).

The mirror is now `rsync -a -H --delete` and is only emitted when it is safe:
the snapshot must carry a non-empty `.efi-back`, and `/efi` must be the
mounted ESP.  It is also emitted before the current subvolume is renamed.

These tests are pure: no rsync, btrfs command, mount or root privilege is
needed.  `BtrfsRestore.__init__` is bypassed because it re-execs under sudo.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from my_snaps import my_restore

ROOT = 'eos@root'
SNAP = 'eos@snapshots/eos@root.2024-01-10-174732=Update'
REVERT = 'eos@root.2024-01-14-101010=Reverted'


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


def make_backup(parent, snap=SNAP, entries=('vmlinuz-linux',)):
    """ Create `<parent>/<snap>/.efi-back` and return the snapshot dir. """
    snap_dir = os.path.join(str(parent), snap)
    backup = os.path.join(snap_dir, '.efi-back')
    os.makedirs(backup, exist_ok=True)
    for entry in entries:
        with open(os.path.join(backup, entry), 'w', encoding='utf-8') as fh:
            fh.write('x')
    return str(snap_dir)


@pytest.fixture(name='efi_mounted')
def efi_mounted_fixture(monkeypatch):
    """ Pretend /efi is the mounted ESP (and nothing else is). """
    monkeypatch.setattr(my_restore.os.path, 'ismount',
                        lambda path: path == '/efi')


def normalize(cmd):
    return ' '.join(cmd.split())


# --- the command itself ----------------------------------------------------

def test_delete_is_spelled_out(tmp_path, efi_mounted):
    """ `-del` is `-d -e l`, not `--delete`; the long form cannot be clustered. """
    inst = make_instance()
    whence = make_backup(tmp_path)

    cmd = inst.efi_sync_cmd(ROOT, whence)

    assert '--delete' in cmd
    assert '-H' in cmd
    assert ' -del' not in cmd
    assert ' -e ' not in cmd
    assert cmd.endswith(' && '), cmd
    assert cmd.startswith('rsync -a '), cmd


def test_non_root_subvolume_is_not_synced(tmp_path, efi_mounted):
    """ Only root owns the ESP; a home snapshot must not touch /efi. """
    inst = make_instance()
    whence = make_backup(tmp_path)

    assert inst.efi_sync_cmd('eos@home', whence) == ''


def test_missing_backup_is_not_synced(tmp_path, efi_mounted):
    """ Snapshots predating the pacman hook carry no `.efi-back` at all. """
    inst = make_instance()

    assert inst.efi_sync_cmd(ROOT, str(tmp_path)) == ''


# --- guards that make `--delete` safe --------------------------------------

def test_empty_backup_is_refused(tmp_path, efi_mounted, capsys):
    """ `--delete` from an empty source would empty the ESP. """
    inst = make_instance()
    whence = make_backup(tmp_path, entries=())

    assert inst.efi_sync_cmd(ROOT, whence) == ''
    assert 'empty' in capsys.readouterr().err


def test_unmounted_efi_is_refused(tmp_path, monkeypatch, capsys):
    """ Without the mount, `--delete` would hit a plain directory. """
    monkeypatch.setattr(my_restore.os.path, 'ismount', lambda path: False)
    inst = make_instance()
    whence = make_backup(tmp_path)

    assert inst.efi_sync_cmd(ROOT, whence) == ''
    assert 'not a mount point' in capsys.readouterr().err


# --- integration with `select_restores()` ----------------------------------

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


def test_sync_precedes_the_rename(tmp_path, monkeypatch, efi_mounted):
    """ A failed rsync must not leave `{subvol}` moved aside with no replacement.

    `sync_cmd()` only needs the chosen snapshot and `self.root_subvol`, so
    hoisting it ahead of `prep` is behavior-preserving on success and leaves
    the system untouched on failure.
    """
    monkeypatch.chdir(tmp_path)
    make_backup(tmp_path)
    inst = make_instance()
    inst.mounted_subpaths = {ROOT}
    state = {ROOT: SimpleNamespace(revert=None, snaps=[SNAP])}

    calls = prepare(monkeypatch, inst, 'a', state)

    cmd = normalize(calls[0])
    assert '--delete' in cmd
    assert ' -del' not in cmd
    assert cmd.index('rsync') < cmd.index(f'mv "{ROOT}"'), cmd
    assert cmd.index('rsync') < cmd.index('btrfs sub snap'), cmd
    assert cmd.endswith(f'btrfs sub snap "{SNAP}" "{ROOT}"'), cmd


def test_revert_branch_also_mirrors_the_esp(tmp_path, monkeypatch, efi_mounted):
    """ Reverting back to the reverted tip restores that tip's ESP image. """
    monkeypatch.chdir(tmp_path)
    make_backup(tmp_path, snap=REVERT)
    inst = make_instance()
    inst.mounted_subpaths = {ROOT}
    state = {ROOT: SimpleNamespace(revert=REVERT, snaps=[])}

    calls = prepare(monkeypatch, inst, 'a', state)

    cmd = normalize(calls[0])
    assert '--delete' in cmd
    assert cmd.index('rsync') < cmd.index(f'mv "{REVERT}" "{ROOT}"'), cmd
