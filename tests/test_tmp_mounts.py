"""Tests for the temporary bookkeeping mounts under `/tmp/.btrfs`.

These mounts used to be plain `mount <dev> <dir>` calls.  Two things went
wrong: a filesystem with a configured default subvolume answered with paths
relative to that subvolume, so every path from `btrfs sub list` pointed at
the wrong place, and the mount was writable although the README describes it
as a read-only bookkeeping mount.

The mounts are now pinned with `subvolid=5`.  Discovery reads through a
read-only mount; creating or deleting a subvolume asks for a separate
writable mount, so the bookkeeping mount stays read-only.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from my_snaps import main as main_module
from my_snaps.main import BTRFS, BTRFSError


def make_btrfs(mounts=()):
    """A BTRFS instance with one fake device and a fake /proc/mounts."""
    self = BTRFS.__new__(BTRFS)
    self.tmp_dir = '/tmp/.btrfs/'
    self.db = False
    self.dirty = False
    self.devs = {'sda1': SimpleNamespace(
        dev='sda1', device_path='/dev/sda1', label='', uuid='u')}
    self._mounts = list(mounts)
    self._load_mounts = lambda: list(self._mounts)
    return self


@pytest.fixture
def run_calls(monkeypatch):
    """Capture subprocess.run argv lists without mounting anything."""
    calls = []

    def fake_run(cmd, check=False):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(main_module.subprocess, 'run', fake_run)
    monkeypatch.setattr(main_module.os, 'makedirs', lambda *args, **kwargs: None)
    return calls


def test_discovery_mount_is_read_only_and_pinned_to_the_top_level(run_calls):
    self = make_btrfs()

    assert self._ensure_tmp_mount('sda1') == '/tmp/.btrfs/sda1/ro'
    assert run_calls == [[
        'mount', '-o', 'ro,subvolid=5', '/dev/sda1', '/tmp/.btrfs/sda1/ro']]
    assert self.devs['sda1'].ro_path == '/tmp/.btrfs/sda1/ro'
    assert self.devs['sda1'].tmp_path == '/tmp/.btrfs/sda1/ro'


def test_existing_discovery_mount_is_reused(run_calls):
    self = make_btrfs([
        '/dev/sda1 /tmp/.btrfs/sda1/ro btrfs'
        ' ro,relatime,subvolid=5,subvol=/ 0 0'])

    assert self._ensure_tmp_mount('sda1') == '/tmp/.btrfs/sda1/ro'
    assert run_calls == []
    assert self.devs['sda1'].tmp_path == '/tmp/.btrfs/sda1/ro'


def test_a_writable_mount_is_not_reused_for_discovery(run_calls):
    self = make_btrfs([
        '/dev/sda1 /tmp/.btrfs/sda1/ro btrfs'
        ' rw,relatime,subvolid=5,subvol=/ 0 0'])

    self._ensure_tmp_mount('sda1')

    assert run_calls == [
        ['umount', '/tmp/.btrfs/sda1/ro'],
        ['mount', '-o', 'ro,subvolid=5', '/dev/sda1', '/tmp/.btrfs/sda1/ro']]


def test_a_mount_without_the_top_level_is_replaced(run_calls):
    """ A default subvolume (subvolid != 5) would shift every parsed path. """
    self = make_btrfs([
        '/dev/sda1 /tmp/.btrfs/sda1/ro btrfs'
        ' ro,relatime,subvolid=2818,subvol=/deb@root 0 0'])

    self._ensure_tmp_mount('sda1')

    assert run_calls == [
        ['umount', '/tmp/.btrfs/sda1/ro'],
        ['mount', '-o', 'ro,subvolid=5', '/dev/sda1', '/tmp/.btrfs/sda1/ro']]


def test_a_stale_legacy_mount_is_unmounted_first(run_calls, monkeypatch):
    """ The old code mounted at `<tmp_dir>/<dev>`; nesting under it is wrong. """
    monkeypatch.setattr(main_module.os.path, 'ismount',
                        lambda path: path == '/tmp/.btrfs/sda1')
    self = make_btrfs()

    assert self._ensure_tmp_mount('sda1') == '/tmp/.btrfs/sda1/ro'
    assert run_calls == [
        ['umount', '/tmp/.btrfs/sda1'],
        ['mount', '-o', 'ro,subvolid=5', '/dev/sda1', '/tmp/.btrfs/sda1/ro']]


def test_writable_mount_is_created_only_on_request(run_calls):
    self = make_btrfs()
    self._ensure_tmp_mount('sda1')
    run_calls.clear()

    assert self._ensure_rw_mount('sda1') == '/tmp/.btrfs/sda1/rw'
    assert run_calls == [[
        'mount', '-o', 'subvolid=5', '/dev/sda1', '/tmp/.btrfs/sda1/rw']]
    assert self.devs['sda1'].rw_path == '/tmp/.btrfs/sda1/rw'
    # The discovery mount stays the one that `btrfs sub list` paths use.
    assert self.devs['sda1'].tmp_path == '/tmp/.btrfs/sda1/ro'


def test_existing_writable_mount_is_reused(run_calls):
    self = make_btrfs([
        '/dev/sda1 /tmp/.btrfs/sda1/rw btrfs'
        ' rw,relatime,subvolid=5,subvol=/ 0 0'])

    assert self._ensure_rw_mount('sda1') == '/tmp/.btrfs/sda1/rw'
    assert run_calls == []


def test_a_failed_writable_mount_is_an_operational_error(monkeypatch):
    monkeypatch.setattr(
        main_module.subprocess, 'run',
        lambda cmd, check=False: SimpleNamespace(returncode=1))
    monkeypatch.setattr(main_module.os, 'makedirs', lambda *args, **kwargs: None)
    self = make_btrfs()

    with pytest.raises(BTRFSError, match='read-write'):
        self._ensure_rw_mount('sda1')


def test_mount_options_decodes_escaped_spaces():
    self = make_btrfs([
        '/dev/sda1 /tmp/.btrfs/my\\040disk/ro btrfs ro,subvolid=5 0 0'])

    assert self._mount_options('/tmp/.btrfs/my disk/ro') == 'ro,subvolid=5'
    assert self._mount_options('/tmp/.btrfs/other/ro') is None


def test_create_snap_writes_through_the_writable_mount():
    self = make_btrfs()
    self.snap_subvol = SimpleNamespace(path='/@snapshots')
    self._ensure_rw_mount = lambda dev: '/tmp/.btrfs/sda1/rw'
    commands = []

    def slurp(cmd):
        commands.append(list(cmd))
        return [], [], 0

    self._slurp_command = slurp
    subvol = BTRFS.init_subvol_ns(dev='sda1', path='/@root', ident='256')
    subvol.mount = '/'

    assert self._create_snap(subvol, '.abcd') is True
    assert commands == [[
        'btrfs', 'sub', 'snap', '-r', '/',
        '/tmp/.btrfs/sda1/rw/@snapshots/@root.abcd']]


def test_delete_uses_the_writable_mount():
    self = make_btrfs()
    self._ensure_rw_mount = lambda dev: '/tmp/.btrfs/sda1/rw'
    commands = []

    def slurp(cmd):
        commands.append(list(cmd))
        return [], [], 0

    self._slurp_command = slurp
    subvol = BTRFS.init_subvol_ns(
        dev='sda1', path='/@snapshots/@old.abcd', ident='3000')

    assert self._del_subvolume(subvol, ans='y') is True
    assert commands == [['btrfs', 'sub', 'del',
                         '/tmp/.btrfs/sda1/rw/@snapshots/@old.abcd']]


def test_umount_tmps_unmounts_writable_before_read_only(monkeypatch):
    self = make_btrfs()
    dev_ns = self.devs['sda1']
    dev_ns.ro_path = '/tmp/.btrfs/sda1/ro'
    dev_ns.tmp_path = dev_ns.ro_path
    dev_ns.rw_path = '/tmp/.btrfs/sda1/rw'
    calls = []

    monkeypatch.setattr(main_module.subprocess, 'run',
                        lambda cmd, check=False: calls.append(list(cmd)))
    monkeypatch.setattr(main_module.os.path, 'ismount', lambda path: True)

    self.umount_tmps()

    assert calls == [
        ['umount', '/tmp/.btrfs/sda1/rw'],
        ['umount', '/tmp/.btrfs/sda1/ro'],
    ]
