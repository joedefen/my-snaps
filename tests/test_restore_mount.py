"""Regression tests for the /mnt selection path of `my-restore`.

The mount-selection command used to be `umount /dev && mount <dev> /mnt`
whenever `/mnt` was occupied. As root, unmounting `/dev` can tear down
devtmpfs, and a failure there short-circuits the `&&`, leaving the chosen
filesystem unmounted while `select_restores()` chdirs into `/mnt` and
operates on whatever happens to be there.

These tests are pure: no btrfs command, mount, root privilege or tty is
needed. `BtrfsRestore.__init__` is bypassed because it re-execs under sudo.
"""
from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from my_snaps import my_restore

SHOW_OUTPUT = """\
Label: none  uuid: 8f60fc2f-872d-4327-aff9-34c4c4cefde7
\tTotal devices 1 FS bytes used 15.00GiB
\tdevid    1 size 100.00GiB used 20.00GiB path /dev/sda1

Label: 'data'  uuid: 11111111-2222-3333-4444-555555555555
\tTotal devices 1 FS bytes used 1.00GiB
\tdevid    1 size 50.00GiB used 2.00GiB path /dev/sdb1
"""


class FakeMenu:
    """ Stand-in for `InlineMenu.Menu` that picks `selected_key` blindly. """

    selected_key = '0'

    def __init__(self, prompts, default=None, title=''):
        self.prompts = prompts

    def get_prompt_obj(self):
        key = self.selected_key
        return SimpleNamespace(key=key, next=key)

    def get_command(self, choice):
        return self.prompts[choice.key]


def make_instance(mounted_device=''):
    """ Build a `BtrfsRestore` without running the sudo re-exec in __init__. """
    inst = object.__new__(my_restore.BtrfsRestore)
    inst.dry_run = False
    inst.filesystems = [
        SimpleNamespace(label='', path='/dev/sda1'),
        SimpleNamespace(label='data', path='/dev/sdb1'),
    ]
    inst.selected_fs = None
    inst.last_key = None
    inst.mounted_ids = set()
    inst.mounted_subpaths = set()
    inst.root_subvol = None
    inst.is_bootable = True
    inst.slash_mnt = SimpleNamespace(
        device=mounted_device,
        fstype='btrfs' if mounted_device else '')
    return inst


def fake_mounts(rows):
    """ Return an `iter_mounts` replacement yielding the given rows. """
    def _iter():
        yield from rows
    return _iter


# --- command construction -------------------------------------------------

def test_switching_filesystems_unmounts_mnt_not_dev():
    inst = make_instance(mounted_device='/dev/sda1')
    cmds, _ = inst.mount_choices()
    assert cmds['1'] == 'umount /mnt && mount /dev/sdb1 /mnt # data'
    assert 'umount /dev ' not in cmds['1']


def test_empty_mnt_is_mounted_without_unmounting_anything():
    inst = make_instance(mounted_device='')
    cmds, _ = inst.mount_choices()
    assert cmds['0'] == 'mount /dev/sda1 /mnt # no-label'
    assert 'umount' not in cmds['0']


def test_already_mounted_filesystem_is_kept():
    inst = make_instance(mounted_device='/dev/sda1')
    cmds, _ = inst.mount_choices()
    assert cmds['0'] == "# KEEP '/dev/sda1' mounted on /mnt"
    assert 'umount' not in cmds['0']


# --- selection honors dry-run ---------------------------------------------

def test_select_mount_only_echoes_in_dry_run(monkeypatch):
    inst = make_instance(mounted_device='/dev/sda1')
    inst.dry_run = True
    FakeMenu.selected_key = '1'
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *a, **k: SHOW_OUTPUT.encode())
    calls = []
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)

    inst.select_mount()

    assert calls == ["echo WOULD + '' 'umount /mnt && mount /dev/sdb1 /mnt # data'"]
    assert inst.selected_fs.path == '/dev/sdb1'


def test_select_mount_runs_the_command_outside_dry_run(monkeypatch):
    inst = make_instance(mounted_device='/dev/sda1')
    inst.dry_run = False
    FakeMenu.selected_key = '1'
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *a, **k: SHOW_OUTPUT.encode())
    calls = []
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)

    inst.select_mount()

    assert len(calls) == 1
    assert ' '.join(calls[0].split()) == (
        'clear;set -x; umount /mnt && mount /dev/sdb1 /mnt # data')
    assert inst.selected_fs.path == '/dev/sdb1'


def test_select_mount_reports_a_failed_btrfs_listing(monkeypatch):
    inst = make_instance()
    monkeypatch.setattr(
        my_restore.subprocess, 'check_output',
        lambda *a, **k: (_ for _ in ()).throw(
            subprocess.CalledProcessError(1, 'btrfs')))
    with pytest.raises(SystemExit) as caught:
        inst.select_mount()
    assert caught.value.code == 1


# --- /mnt verification before any subvolume operation ---------------------

@pytest.mark.parametrize('rows,expected', [
    ([], 'nothing is mounted on /mnt'),
    ([('/dev/sda1', '/mnt', 'ext4', 'rw')], '/mnt holds ext4'),
    ([('/dev/sdc1', '/mnt', 'btrfs', 'rw')], '/mnt holds /dev/sdc1'),
    ([('/dev/sda1', '/', 'btrfs', 'rw')], 'nothing is mounted on /mnt'),
    ([('/dev/sda1', '/mnt', 'btrfs', 'rw')], ''),
])
def test_mnt_problem(monkeypatch, rows, expected):
    inst = make_instance()
    inst.selected_fs = SimpleNamespace(path='/dev/sda1')
    monkeypatch.setattr(my_restore, 'iter_mounts', fake_mounts(rows))
    assert expected in inst.mnt_problem()


def test_select_restores_refuses_when_mnt_is_not_the_chosen_filesystem(
        monkeypatch, capsys):
    inst = make_instance()
    inst.selected_fs = SimpleNamespace(path='/dev/sda1')
    monkeypatch.setattr(my_restore, 'iter_mounts', fake_mounts([]))
    monkeypatch.setattr(inst, 'get_state',
                        lambda: pytest.fail('must not inspect /mnt'))
    with pytest.raises(SystemExit) as caught:
        inst.select_restores()
    assert caught.value.code == 1
    assert 'nothing is mounted on /mnt' in capsys.readouterr().err


# --- more than ten filesystems --------------------------------------------

def test_mount_choices_use_single_character_keys_past_nine():
    inst = make_instance()
    inst.filesystems = [SimpleNamespace(label='', path=f'/dev/sd{i}')
                        for i in range(12)]
    cmds, todo = inst.mount_choices()
    assert list(cmds) == list('0123456789ab')
    assert todo == '0'


def test_select_mount_maps_a_letter_key_to_the_right_filesystem(monkeypatch):
    fss = [SimpleNamespace(label='', path=f'/dev/sd{i}') for i in range(11)]
    inst = make_instance()
    monkeypatch.setattr(my_restore, 'parse_filesystems', lambda _out: fss)
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *a, **k: b'')
    monkeypatch.setattr(my_restore.os, 'system', lambda cmd: 0)
    FakeMenu.selected_key = 'a'
    monkeypatch.setattr(my_restore, 'Menu', FakeMenu)

    inst.select_mount()

    assert inst.selected_fs is fss[10]


# --- bootability does not assume one ESP layout ---------------------------

def _bootable_instance(tmp_path, monkeypatch, efi_walk):
    """ Build an instance whose roots exist under tmp_path and whose /efi
    is supplied by `efi_walk`. """
    mnt = tmp_path / 'mnt'
    (mnt / 'root/usr/lib/modules/6.1.0-test').mkdir(parents=True)
    (mnt / 'root/.efi-back/machine-id/6.1.0-test').mkdir(parents=True)
    monkeypatch.setattr(my_restore, 'MNT', str(mnt))
    real_walk = my_restore.os.walk

    def fake_walk(folder):
        if folder == '/efi':
            return iter(efi_walk)
        return real_walk(folder)

    monkeypatch.setattr(my_restore.os, 'walk', fake_walk)
    inst = object.__new__(my_restore.BtrfsRestore)
    inst.root_subvol = 'root'
    inst.is_bootable = True
    return inst


def test_check_bootable_accepts_a_nested_efi_layout(tmp_path, monkeypatch):
    inst = _bootable_instance(
        tmp_path, monkeypatch,
        [('/efi/machine-id', ['6.1.0-test'], [])])
    assert inst.check_bootable() is True


def test_check_bootable_accepts_a_flat_efi_layout(tmp_path, monkeypatch):
    inst = _bootable_instance(
        tmp_path, monkeypatch,
        [('/efi', [], ['vmlinuz-6.1.0-test'])])
    assert inst.check_bootable() is True


def test_check_bootable_reports_a_missing_kernel(tmp_path, monkeypatch, capsys):
    mnt = tmp_path / 'mnt'
    (mnt / 'root/usr/lib/modules/6.1.0-test').mkdir(parents=True)
    (mnt / 'root/.efi-back/machine-id/5.0.0-old').mkdir(parents=True)
    monkeypatch.setattr(my_restore, 'MNT', str(mnt))
    real_walk = my_restore.os.walk

    def fake_walk(folder):
        if folder == '/efi':
            return iter([('/efi/machine-id', ['6.1.0-test'], [])])
        return real_walk(folder)

    monkeypatch.setattr(my_restore.os, 'walk', fake_walk)
    inst = object.__new__(my_restore.BtrfsRestore)
    inst.root_subvol = 'root'
    inst.is_bootable = True

    assert inst.check_bootable() is False
    assert 'NOT bootable' in capsys.readouterr().out
