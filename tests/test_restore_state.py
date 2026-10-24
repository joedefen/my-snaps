"""Coverage for the state-machine and error paths of `my-restore`.

The tests stay pure: `/proc/mounts`, `btrfs` output and `os.system()` are all
replaced with fakes, so nothing is mounted, renamed or deleted.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest
from my_snaps import my_restore
from my_snaps.my_restore import MENU_KEYS, BtrfsRestore


class SeqMenu:
    """A `Menu` stand-in that returns a scripted list of selections."""

    selections = []
    last_title = ''

    get_next_key = staticmethod(my_restore.Menu.get_next_key)

    def __init__(self, prompts, default=None, title=''):
        self.prompts = prompts
        SeqMenu.last_title = title

    def get_prompt_obj(self):
        key, next_key = SeqMenu.selections.pop(0)
        return SimpleNamespace(key=key, next=next_key)

    def get_command(self, choice):
        prompt = self.prompts[choice.key]
        if isinstance(prompt, (tuple, list)):
            return prompt[1]
        return prompt


def make_instance(dry_run=False):
    """Build a `BtrfsRestore` without reading the real mount table."""
    inst = object.__new__(BtrfsRestore)
    inst.dry_run = dry_run
    inst.filesystems = []
    inst.selected_fs = None
    inst.last_key = None
    inst.mounted_ids = set()
    inst.root_subvol = None
    inst.mounted_subpaths = set()
    inst.todel_subpaths = []
    inst.slash_mnt = SimpleNamespace(device='', fstype='')
    inst.is_bootable = True
    return inst


# --- /proc/mounts and device names -----------------------------------------

def test_iter_mounts_yields_well_formed_rows(monkeypatch):
    data = (
        '/dev/sda1 / btrfs rw,subvolid=5,subvol=/ 0 0\n'
        'garbage\n'
        '/dev/sdb1 /mnt ext4 rw 0 0\n')

    monkeypatch.setattr(my_restore, 'open',
                        lambda *_a, **_k: io.StringIO(data), raising=False)

    assert list(my_restore.iter_mounts()) == [
        ('/dev/sda1', '/', 'btrfs', 'rw,subvolid=5,subvol=/'),
        ('/dev/sdb1', '/mnt', 'ext4', 'rw'),
    ]


def test_iter_mounts_reports_an_unreadable_proc_mounts(monkeypatch, capsys):
    def unreadable(*_args, **_kwargs):
        raise OSError('permission denied')

    monkeypatch.setattr(my_restore, 'open', unreadable, raising=False)

    with pytest.raises(SystemExit) as caught:
        list(my_restore.iter_mounts())

    assert caught.value.code == 1
    assert 'cannot read /proc/mounts' in capsys.readouterr().err


def test_same_device_resolves_symlinks_and_rejects_other_spellings(monkeypatch):
    links = {'/dev/disk/by-uuid/abc': '/dev/sda1'}

    def realpath(path):
        return links.get(path, path)

    monkeypatch.setattr(my_restore.os.path, 'realpath', realpath)

    assert my_restore.same_device('/dev/sda1', '/dev/sda1') is True
    assert my_restore.same_device('/dev/disk/by-uuid/abc', '/dev/sda1') is True
    assert my_restore.same_device('/dev/sda1', '/dev/sdb1') is False
    assert my_restore.same_device('sda1', 'sda1') is True
    assert my_restore.same_device('sda1', 'sdb1') is False


def test_construction_is_inert_and_records_the_mount_state(monkeypatch):
    monkeypatch.setattr(
        BtrfsRestore, 'get_slash_mnt',
        lambda _self: SimpleNamespace(device='/dev/sda1', fstype='btrfs'))

    inst = BtrfsRestore(dry_run=True)

    assert inst.dry_run is True
    assert inst.root_subvol is None
    assert inst.mounted_ids == set()
    assert inst.todel_subpaths == []
    assert inst.slash_mnt.device == '/dev/sda1'


# --- menu loop --------------------------------------------------------------

def prepare_menu(monkeypatch, selections):
    SeqMenu.selections = list(selections)
    monkeypatch.setattr(my_restore, 'Menu', SeqMenu)


def test_do_command_exits_after_the_bootability_check(monkeypatch):
    inst = make_instance()
    prepare_menu(monkeypatch, [('x', 'x')])
    called = []
    monkeypatch.setattr(inst, 'check_bootable',
                        lambda: called.append('boot') or True)

    with pytest.raises(SystemExit) as caught:
        inst.do_command({'a': 'true'})

    assert caught.value.code == 0
    assert called == ['boot']


def test_do_command_toggles_dry_run_without_running_a_command(monkeypatch):
    inst = make_instance()
    prepare_menu(monkeypatch, [('y', 'a'), ('x', 'x')])
    monkeypatch.setattr(inst, 'check_bootable', lambda: True)
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda _cmd: pytest.fail('must not run a command'))

    with pytest.raises(SystemExit):
        inst.do_command({'a': 'true'})

    assert inst.dry_run is True
    assert 'DRY-RUN' in SeqMenu.last_title


def test_do_command_runs_one_command_and_returns_the_next_key(monkeypatch):
    inst = make_instance()
    prepare_menu(monkeypatch, [('a', 'b')])
    calls = []
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)

    assert inst.do_command({'a': 'echo hi'}, precmd='pre', once=True) == 'b'
    assert calls == ['clear;set -x; pre echo hi']


def test_do_command_skips_comment_only_entries(monkeypatch):
    inst = make_instance()
    prepare_menu(monkeypatch, [('a', 'a')])
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda _cmd: pytest.fail('comment must not run'))

    assert inst.do_command({'a': '# KEEP mounted'}, once=True) == 'a'


def test_do_command_reports_a_failing_command(monkeypatch, capsys):
    inst = make_instance()
    prepare_menu(monkeypatch, [('a', 'a')])
    monkeypatch.setattr(my_restore.os, 'system', lambda _cmd: 3)

    inst.do_command({'a': 'false'}, once=True)

    assert 'command failed (wait status 3)' in capsys.readouterr().err


def test_do_command_echoes_instead_of_running_in_dry_run(monkeypatch):
    inst = make_instance(dry_run=True)
    prepare_menu(monkeypatch, [('a', 'a')])
    calls = []
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)

    inst.do_command({'a': 'touch /tmp/x'}, precmd='pre', once=True)

    assert calls == ["echo WOULD + 'pre' 'touch /tmp/x'"]


def test_do_command_does_not_reboot_when_not_bootable(monkeypatch):
    inst = make_instance()
    prepare_menu(monkeypatch, [('a', 'a')])
    monkeypatch.setattr(inst, 'check_bootable', lambda: False)
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda _cmd: pytest.fail('must not reboot'))

    inst.do_command({'a': 'reboot now'}, once=True)


# --- mount discovery and selection -----------------------------------------

def test_get_slash_mnt_records_root_and_btrfs_subvolume_ids(monkeypatch):
    rows = [
        ('/dev/sda1', '/', 'btrfs', 'rw,subvolid=256,subvol=/eos@root'),
        ('/dev/sdb1', '/mnt', 'btrfs', 'rw,subvolid=300,subvol=/mntroot'),
        ('/dev/sdb1', '/mnt', 'btrfs', 'rw,subvolid=bad,subvol='),
        ('/dev/sdc1', '/boot', 'ext4', 'rw'),
    ]
    monkeypatch.setattr(my_restore, 'iter_mounts', lambda: iter(rows))
    inst = make_instance()

    rv = inst.get_slash_mnt()

    assert rv.device == '/dev/sdb1'
    assert rv.fstype == 'btrfs'
    # Both /mnt and / contribute their mounted subvolume ids.
    assert inst.mounted_ids == {256, 300}
    assert inst.root_subvol == 'eos@root'


def test_mount_choices_refuses_more_filesystems_than_it_can_label(
        monkeypatch, capsys):
    inst = make_instance()
    inst.filesystems = [
        SimpleNamespace(label='', path=f'/dev/sd{i}')
        for i in range(len(MENU_KEYS) + 1)]

    with pytest.raises(SystemExit) as caught:
        inst.mount_choices()

    assert caught.value.code == 1
    assert 'more than' in capsys.readouterr().err


def test_select_mount_reports_an_empty_filesystem_list(monkeypatch, capsys):
    inst = make_instance()
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *_a, **_k: b'')

    with pytest.raises(SystemExit) as caught:
        inst.select_mount()

    assert caught.value.code == 1
    assert 'no BTRFS filesystems found' in capsys.readouterr().err


def test_select_mount_rejects_a_key_that_was_not_offered(monkeypatch, capsys):
    inst = make_instance()
    monkeypatch.setattr(my_restore.subprocess, 'check_output',
                        lambda *_a, **_k: b'Label: none\n')
    monkeypatch.setattr(
        my_restore, 'parse_filesystems',
        lambda _out: [SimpleNamespace(label='', path='/dev/sda1')])

    def fake_do_command(*_args, **_kwargs):
        inst.last_key = 'z'

    monkeypatch.setattr(inst, 'do_command', fake_do_command)

    with pytest.raises(SystemExit) as caught:
        inst.select_mount()

    assert caught.value.code == 1
    assert 'unexpected selection' in capsys.readouterr().err


# --- bootability ------------------------------------------------------------

def test_check_bootable_is_true_without_a_root_subvolume():
    inst = make_instance()
    assert inst.check_bootable() is True


def test_check_bootable_skips_the_check_when_efi_has_no_matching_module(
        monkeypatch):
    inst = make_instance()
    inst.root_subvol = 'root'
    monkeypatch.setattr(my_restore.os, 'listdir', lambda _path: ['6.1.0'])
    monkeypatch.setattr(my_restore.os, 'walk', lambda _path: iter([]))

    assert inst.check_bootable() is True


def test_check_bootable_tolerates_an_unreadable_module_directory(monkeypatch):
    inst = make_instance()
    inst.root_subvol = 'root'
    monkeypatch.setattr(
        my_restore.os, 'listdir',
        lambda _path: (_ for _ in ()).throw(OSError('no such file')))

    assert inst.check_bootable() is True


def test_check_bootable_tolerates_an_unreadable_efi_tree(monkeypatch):
    inst = make_instance()
    inst.root_subvol = 'root'
    monkeypatch.setattr(my_restore.os, 'listdir', lambda _path: ['6.1.0'])
    monkeypatch.setattr(
        my_restore.os, 'walk',
        lambda _path: (_ for _ in ()).throw(OSError('no such file')))

    assert inst.check_bootable() is True


# --- /efi mirror guards -----------------------------------------------------

def test_efi_sync_reports_an_unreadable_backup(monkeypatch, capsys):
    inst = make_instance()
    inst.root_subvol = 'root'
    monkeypatch.setattr(my_restore.os.path, 'isdir', lambda _path: True)
    monkeypatch.setattr(my_restore.os.path, 'ismount', lambda _path: True)
    monkeypatch.setattr(
        my_restore.os, 'listdir',
        lambda _path: (_ for _ in ()).throw(OSError('permission denied')))

    assert inst.efi_sync_cmd('root', '/snap') == ''
    assert 'cannot read' in capsys.readouterr().err


# --- selection and command generation --------------------------------------

def test_select_restores_dry_run_explains_that_it_did_not_mount(
        monkeypatch, capsys):
    inst = make_instance(dry_run=True)
    monkeypatch.setattr(inst, 'mnt_problem', lambda: '/mnt is empty')

    with pytest.raises(SystemExit) as caught:
        inst.select_restores()

    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert 'refusing to touch subvolumes' in err
    assert 'dry-run does not mount' in err


def test_commands_for_a_mounted_revert_free_the_name_safely():
    inst = make_instance()
    inst.root_subvol = 'root'
    inst.mounted_subpaths = {'root'}
    state = {'root': SimpleNamespace(
        revert='root.2024-01-14-101010=Reverted', snaps=[])}

    cmds = inst._commands_for_state(state)
    action = cmds['a'][1]

    assert cmds['a'][0].startswith('root: revert ')
    assert 'mv "root" "root.' in action
    assert 'ToDel" && mv "root.2024-01-14-101010=Reverted" "root"' in action
    assert 'btrfs sub del "root" &&' not in action


def test_commands_for_an_unmounted_subvolume_rename_then_restore():
    inst = make_instance()
    inst.root_subvol = 'root'
    state = {'root': SimpleNamespace(revert=None, snaps=[
        '@snapshots/root.2024-01-10-174732=Update'])}

    cmds = inst._commands_for_state(state)
    action = cmds['a'][1]

    assert 'mv "root" "root.' in action
    assert action.endswith(
        'btrfs sub snap "@snapshots/root.2024-01-10-174732=Update" "root"')
    assert 'btrfs sub del "root"' not in action


def test_freed_name_cmd_deletes_when_the_subvolume_is_not_mounted():
    inst = make_instance()
    assert inst._freed_name_cmd('root') == 'btrfs sub del "root" && '


def test_commands_offer_pending_todel_entries_before_reboot():
    inst = make_instance()
    inst.todel_subpaths = ['root.2024-01-15-101010ToDel']

    cmds = inst._commands_for_state({})

    assert cmds['a'] == ['del root.2024-01-15-101010ToDel',
                         'btrfs sub del "root.2024-01-15-101010ToDel"']
    assert cmds['b'] == 'reboot now'


# --- list scanning ----------------------------------------------------------

def test_scan_list_lines_classifies_top_level_entries():
    inst = make_instance()
    inst.mounted_ids = {2}
    lines = [
        'ID 1 gen 1 top level 5 path root',
        'ID 2 gen 1 top level 5 path root@snapshot',
        'ID 3 gen 1 top level 5 path root.2024-01-14-101010=Reverted',
        'ID 4 gen 1 top level 5 path root.2024-01-15-101010ToDel',
        'ID 5 gen 1 top level 5 path @snapshots/snap.2024-01-10-174732=Daily',
        'ID 6 gen 1 top level 5 path root/child',
        'not a subvolume line',
    ]

    subnames, subs, reverts = inst._scan_list_lines(lines)

    assert subnames == {'root'}
    assert reverts == {'root': 'root.2024-01-14-101010=Reverted'}
    assert inst.todel_subpaths == ['root.2024-01-15-101010ToDel']
    assert inst.mounted_subpaths == {'root@snapshot'}
    assert subs == {'snap': ['@snapshots/snap.2024-01-10-174732=Daily']}


# --- top level --------------------------------------------------------------

def test_main_selection_loop_passes_the_next_key(monkeypatch):
    inst = make_instance()
    calls = []
    monkeypatch.setattr(inst, 'select_mount', lambda: calls.append('mount'))
    monkeypatch.setattr(my_restore.os, 'system',
                        lambda cmd: calls.append(cmd) or 0)

    def select_restores(todo='a'):
        calls.append(('restore', todo))
        raise KeyboardInterrupt

    monkeypatch.setattr(inst, 'select_restores', select_restores)

    with pytest.raises(KeyboardInterrupt):
        inst.main()

    assert calls[0] == 'mount'
    assert 'clear' in calls[1]
    assert calls[2] == ('restore', 'a')


def test_run_swallows_keyboard_interrupt(monkeypatch):
    class FakeRestore:
        def __init__(self, dry_run=False):
            pass

        def main(self):
            raise KeyboardInterrupt

    monkeypatch.setattr(my_restore, 'BtrfsRestore', FakeRestore)

    assert my_restore.run(['-n']) is None


def test_run_reports_an_unexpected_exception(monkeypatch, capsys):
    class FakeRestore:
        def __init__(self, dry_run=False):
            pass

        def main(self):
            raise ValueError('boom')

    monkeypatch.setattr(my_restore, 'BtrfsRestore', FakeRestore)

    my_restore.run(['-n'])

    out = capsys.readouterr().out
    assert 'exception: boom' in out
    assert 'Traceback' in out
