"""Coverage for the non-curses state machine in `my_snaps.main`.

The tests replace `btrfs`, `df`, `/proc/mounts`, `os.system()` and the window
with fakes. Nothing is mounted, snapshotted or deleted.
"""
from __future__ import annotations

import curses as cs
from types import SimpleNamespace
from unittest.mock import mock_open

import my_snaps.main as main_module
import pytest
from console_window import InlineConfirmation, Theme
from my_snaps.main import BTRFS, DEFAULT_KEEP_SNAPS, BTRFSError


class FakeWin:
    """The slice of ConsoleWindow used by the state machine."""

    def __init__(self, pick_pos=0):
        self.pick_pos = pick_pos
        # ScreenStack saves/restores these on a push/pop.
        self.scroll_pos = 0
        self.free_scroll = False
        self.passthrough_mode = False
        self.handled_keys = set()
        self.alerts = []
        self.flashes = []
        self.pick_mode = True
        self.body = []
        self.headers = []
        self.fancy = []
        self.stopped = False
        self.keys = []
        self.help_nav = False
        self.help_body = False

    def alert(self, message='', title='ALERT', **_kwargs):
        self.alerts.append((title, message))

    def flash(self, message='', duration=2.0):
        self.flashes.append((message, duration))

    def clear(self):
        pass

    def set_pick_mode(self, mode):
        self.pick_mode = mode

    def render(self):
        pass

    def stop_curses(self):
        self.stopped = True

    def add_body(self, line, attr=None, **_kwargs):
        self.body.append((line, attr))

    def add_header(self, line, attr=None, **_kwargs):
        self.headers.append((line, attr))

    def add_fancy_header(self, line, **_kwargs):
        self.fancy.append(line)

    def prompt(self, seconds=0):
        return self.keys.pop(0)


def make_btrfs(**overrides):
    self = object.__new__(BTRFS)
    defaults = {
        'tmp_dir': '/tmp/.btrfs/',
        'temps': {},
        'devs': {},
        'dev_aliases': {},
        'snap_targets': [],
        'label_set': set(),
        'snap_subvol': None,
        'db': False,
        'add_limit': 0,
        'keep': DEFAULT_KEEP_SNAPS,
        'label': None,
        'win': None,
        'rows': [],
        'dirty': False,
        'show_size': False,
        'show_progress': False,
        'dev_fingerprint': None,
        'mount_fingerprint': None,
        'appearance_theme': '',
        'appearance_colors': True,
        'screens': {},
        'ss': None,
        'spin': None,
        'confirm': InlineConfirmation(),
        '_confirm_prompt': '',
        '_confirm_labels': '',
        '_confirm_callback': None,
        '_confirm_prev_passthrough': False,
    }
    defaults.update(overrides)
    for key, value in defaults.items():
        setattr(self, key, value)
    return self


def make_subvol(path='/@root', dev='sda1', ident='256'):
    return BTRFS.init_subvol_ns(dev=dev, path=path, ident=ident)


def make_opts(**overrides):
    opts = SimpleNamespace(
        db=False, cron=None, add_snap_max=0, keep=DEFAULT_KEEP_SNAPS,
        label=None, print=False)
    for key, value in overrides.items():
        setattr(opts, key, value)
    return opts


# --- construction branches --------------------------------------------------

def prevent_atexit(monkeypatch):
    monkeypatch.setattr(main_module.atexit, 'register', lambda *_a: None)


@pytest.mark.parametrize('opts,expected_code', [
    (make_opts(db=True), 0),
    (make_opts(print=True), 0),
])
def test_constructor_handles_the_diagnostic_modes(monkeypatch, opts,
                                                  expected_code):
    prevent_atexit(monkeypatch)
    called = []
    monkeypatch.setattr(BTRFS, '_refresh_if_dirty', lambda _self: None)
    monkeypatch.setattr(BTRFS, '_get_disk_usage',
                        lambda _self: called.append('du'))
    monkeypatch.setattr(BTRFS, '_print',
                        lambda _self: called.append('print'))

    with pytest.raises(SystemExit) as caught:
        BTRFS(opts)

    assert caught.value.code == expected_code
    assert called


def test_constructor_installs_a_cron_job_and_exits(monkeypatch):
    prevent_atexit(monkeypatch)
    called = []
    monkeypatch.setattr(BTRFS, '_install_cron_job',
                        lambda _self, opts: called.append(opts.cron))

    with pytest.raises(SystemExit) as caught:
        BTRFS(make_opts(cron='daily'))

    assert caught.value.code == 0
    assert called == ['daily']


@pytest.mark.parametrize('success,expected_code', [(True, 0), (False, 1)])
def test_constructor_noninteractive_add_mode(monkeypatch, capsys, success,
                                             expected_code):
    prevent_atexit(monkeypatch)
    seen = []
    monkeypatch.setattr(BTRFS, '_refresh_if_dirty', lambda _self: None)
    monkeypatch.setattr(BTRFS, '_replace_eldest_snaps',
                        lambda _self: seen.append(_self.label) or success)
    monkeypatch.setattr(BTRFS, '_print', lambda _self: None)

    with pytest.raises(SystemExit) as caught:
        BTRFS(make_opts(add_snap_max=9, print=True))

    assert caught.value.code == expected_code
    assert seen == ['']
    assert 'keeping 9 snapshots' in capsys.readouterr().err


def test_constructor_starts_the_window_by_default(monkeypatch):
    prevent_atexit(monkeypatch)
    called = []
    monkeypatch.setattr(BTRFS, '_refresh_if_dirty', lambda _self: None)
    monkeypatch.setattr(BTRFS, '_start_window',
                        lambda _self: called.append('window'))

    BTRFS(make_opts())

    assert called == ['window']


# --- cron job ---------------------------------------------------------------

def test_install_cron_job_writes_a_script(monkeypatch, capsys):
    opened = mock_open()
    chmod = []
    monkeypatch.setattr(main_module.os.path, 'isdir', lambda _path: True)
    monkeypatch.setattr('builtins.open', opened)
    monkeypatch.setattr(main_module.os, 'chmod',
                        lambda path, mode: chmod.append((path, mode)))
    self = make_btrfs()
    opts = make_opts(cron='weekly', add_snap_max=3, label='=Weekly')

    self._install_cron_job(opts)

    opened.assert_called_once_with('/etc/cron.weekly/weekly-snaps', mode='w',
                                   encoding='utf-8')
    text = opened().write.call_args.args[0]
    assert '-p -s3 -L=Weekly' in text
    assert chmod == [('/etc/cron.weekly/weekly-snaps', 0o755)]
    assert 'OK: to' in capsys.readouterr().out


def test_install_cron_job_refuses_a_missing_directory(monkeypatch, capsys):
    monkeypatch.setattr(main_module.os.path, 'isdir', lambda _path: False)
    self = make_btrfs()

    with pytest.raises(SystemExit) as caught:
        self._install_cron_job(make_opts(cron='daily'))

    assert caught.value.code == -1
    assert 'does not exist' in capsys.readouterr().out


def test_install_cron_job_removes_the_script_when_disabled(monkeypatch):
    removed = []
    monkeypatch.setattr(main_module.os.path, 'isdir', lambda _path: True)
    monkeypatch.setattr(main_module.os.path, 'isfile', lambda _path: True)
    monkeypatch.setattr(main_module.os, 'remove', removed.append)
    self = make_btrfs()

    self._install_cron_job(make_opts(cron='daily', add_snap_max=0))

    assert removed == ['/etc/cron.daily/daily-snaps']


# --- progress and refresh ---------------------------------------------------

def test_progress_writes_only_when_interactive(capsys):
    self = make_btrfs(show_progress=True)
    self._progress('phase')
    self._progress('', final=True)
    assert 'phase' in capsys.readouterr().err

    self.show_progress = False
    self._progress('hidden')
    assert capsys.readouterr().err == ''

    self.show_progress = True
    self.win = object()
    self._progress('hidden too')
    assert capsys.readouterr().err == ''


def test_refresh_if_dirty_preserves_sizes_and_clears_dirty(monkeypatch):
    subvol = make_subvol()
    subvol.size = 42
    self = make_btrfs(dirty=True)
    calls = []

    monkeypatch.setattr(self, 'subvol_iter', lambda: iter([subvol]))
    monkeypatch.setattr(self, '_progress',
                        lambda *_a, **_k: calls.append('progress'))
    monkeypatch.setattr(self, '_load_devs',
                        lambda: calls.append('devs'))
    monkeypatch.setattr(self, '_mount_tmps',
                        lambda: calls.append('mount'))
    monkeypatch.setattr(self, '_determine_mount_points',
                        lambda: calls.append('mounts'))
    monkeypatch.setattr(self, 'gather_snapshots',
                        lambda: calls.append('snaps'))
    monkeypatch.setattr(self, 'make_rows',
                        lambda: calls.append('rows'))

    self._refresh_if_dirty()

    assert subvol.size == 42
    assert self.dirty is False
    assert 'rows' in calls
    assert 'devs' in calls and 'mount' in calls

    # A clean model is not refreshed again.
    before = list(calls)
    self._refresh_if_dirty()
    assert calls == before


def test_subvol_iter_walks_top_down_and_bottom_up():
    top = make_subvol('/top')
    child = make_subvol('/top/child')
    grandchild = make_subvol('/top/child/leaf')
    top.children = [child]
    child.children = [grandchild]
    self = make_btrfs(devs={'u': SimpleNamespace(subvols=[top])})

    assert [ns.path for ns in self.subvol_iter()] == [
        '/top', '/top/child', '/top/child/leaf']
    assert [ns.path for ns in self.subvol_iter(top_down=False)] == [
        '/top/child/leaf', '/top/child', '/top']
    assert [ns.path for ns in self.subvol_iter(child)] == [
        '/top/child', '/top/child/leaf']


def test_cur_snap_suffix_and_stop_curses():
    self = make_btrfs()
    assert self._cur_snap_suffix().startswith('.')
    self.label = '=Daily'
    assert self._cur_snap_suffix().endswith('=Daily')

    self.win = FakeWin()
    self.stop_curses()
    assert self.win.stopped is True

    self.win = None
    self.stop_curses()


# --- screen actions ---------------------------------------------------------

def test_home_screen_actions_delegate_to_the_model(monkeypatch):
    self = make_btrfs(win=FakeWin())
    screen = main_module.HomeScreen(self)
    calls = []
    for name in ('_create_snap', '_del_subvolume', '_confirm_disk_usage'):
        monkeypatch.setattr(
            self, name, lambda *_a, _n=name, **_k: calls.append(_n))
    replaced = []
    monkeypatch.setattr(
        self, '_replace_eldest_snaps',
        lambda *_a, **kwargs: replaced.append(kwargs))

    screen.snap_ACTION()
    screen.delete_ACTION()
    screen.usage_ACTION()
    screen.replace_ACTION()
    screen.add_ACTION()

    assert calls == ['_create_snap', '_del_subvolume', '_confirm_disk_usage']
    assert replaced == [{}, {'just_add': True}]


def test_freshen_action_marks_the_model_dirty():
    self = make_btrfs(win=FakeWin())

    main_module.HomeScreen(self).freshen_ACTION()

    assert self.dirty is True


def test_quit_action_stops_curses_and_exits(capsys):
    self = make_btrfs(win=FakeWin())

    with pytest.raises(SystemExit) as caught:
        main_module.MySnapsScreen(self).quit_ACTION()

    assert caught.value.code == 0
    assert self.win.stopped is True
    assert 'QUITTING NOW' in capsys.readouterr().out


def test_help_and_theme_actions_push_their_screens():
    self = make_btrfs(win=FakeWin())
    pushed = []
    self.ss = SimpleNamespace(
        push=lambda num, pos: pushed.append((num, pos)))
    screen = main_module.MySnapsScreen(self)

    screen.help_ACTION()
    screen.theme_screen_ACTION()

    assert pushed == [(main_module.HELP_ST, 0),
                      (main_module.THEMES_ST, 0)]


# --- window loop ------------------------------------------------------------

class LoopWindow(FakeWin):
    def __init__(self, keys):
        super().__init__()
        self.keys = list(keys)
        self.renders = 0

    def render(self):
        self.renders += 1


class LoopStack:
    """Minimal ScreenStack: one key is enough to leave the loop."""

    def __init__(self, curr_num=main_module.HOME_ST):
        self.curr = SimpleNamespace(num=curr_num)
        self.performed = []

    def perform_actions(self, spin):
        self.performed.append(spin)
        raise SystemExit(0)


class LoopSpin:
    def __init__(self):
        self.seen = []

    def do_key(self, key, win):
        self.seen.append(key)
        return key


def make_loop_btrfs(win, drawn):
    self = make_btrfs(win=win)
    self.screens = {main_module.HOME_ST: SimpleNamespace(
        draw_screen=lambda: drawn.append('draw'))}
    self.ss = LoopStack()
    self.spin = LoopSpin()
    return self


def test_main_loop_draws_then_dispatches_and_exits():
    drawn = []
    self = make_loop_btrfs(LoopWindow([ord('x')]), drawn)

    with pytest.raises(SystemExit) as caught:
        self._main_loop()

    assert caught.value.code == 0
    assert drawn == ['draw']
    assert self.win.renders == 1
    assert self.ss.performed == [self.spin]
    assert self.spin.seen == [ord('x')]


def test_main_loop_polls_devices_on_the_idle_timeout(monkeypatch):
    drawn = []
    self = make_loop_btrfs(LoopWindow([None, ord('x')]), drawn)
    polled = []
    monkeypatch.setattr(
        self, '_poll_device_changes', lambda: polled.append(1))

    with pytest.raises(SystemExit):
        self._main_loop()

    assert polled == [1]
    assert self.spin.seen == [ord('x')]
    assert drawn == ['draw', 'draw']


def test_main_loop_gives_every_key_to_an_active_prompt(monkeypatch):
    drawn = []
    self = make_loop_btrfs(LoopWindow([ord('a')]), drawn)
    self.confirm.start(mode='y')
    seen = []

    def handle(key):
        seen.append(key)
        raise SystemExit(0)

    monkeypatch.setattr(self, 'handle_confirm_key', handle)

    with pytest.raises(SystemExit):
        self._main_loop()

    assert seen == [ord('a')]
    assert self.spin.seen == []


def test_main_loop_reports_an_exception_and_restores_the_terminal(capsys):
    class Window(FakeWin):
        def prompt(self, seconds=0):
            raise RuntimeError('boom')

    self = make_btrfs(win=Window())
    self.screens = {main_module.HOME_ST: SimpleNamespace(
        draw_screen=lambda: None)}
    self.ss = SimpleNamespace(curr=SimpleNamespace(num=main_module.HOME_ST))

    with pytest.raises(SystemExit) as caught:
        self._main_loop()

    assert caught.value.code == 15
    assert self.win.stopped is True
    assert 'exception: boom' in capsys.readouterr().out


def test_start_window_builds_the_ui_then_runs_the_loop(monkeypatch):
    self = make_btrfs()
    calls = []
    monkeypatch.setattr(self, '_init_ui', lambda: calls.append('init'))
    monkeypatch.setattr(self, '_main_loop', lambda: calls.append('loop'))

    self._start_window()

    assert calls == ['init', 'loop']


def test_init_ui_records_mono_when_colour_is_off(monkeypatch):
    class Window(FakeWin):
        def __init__(self, opts=None):
            super().__init__()
            self.opts = opts
            self.theme = 'default'

        def set_handled_keys(self, spin):
            pass

    monkeypatch.setattr(main_module, 'ConsoleWindow', Window)
    self = make_btrfs()
    self.appearance_colors = False

    self._init_ui()

    assert self.win.theme == main_module.MONO_THEME


def test_apply_appearance_mono_turns_colour_off(monkeypatch):
    class Window(FakeWin):
        def set_theme(self, name):
            self.theme = name
            return True

    win = Window()
    win.theme = 'default'
    self = make_btrfs(win=win)
    try:
        self.apply_appearance(main_module.MONO_THEME)

        assert self.appearance_colors is False
        assert self.appearance_theme == ''
        assert self.win.theme == main_module.MONO_THEME
    finally:
        main_module.set_colors_enabled(True)


def test_cycle_appearance_advances_the_picker(monkeypatch):
    self = make_btrfs(win=FakeWin())
    applied = []
    monkeypatch.setattr(
        self, 'apply_appearance', lambda name: applied.append(name))
    self.appearance_theme = 'default'
    self.appearance_colors = True

    choices = self.appearance_choices()
    expected = choices[(choices.index('default') + 1) % len(choices)]

    self.cycle_appearance()

    assert applied == [expected]


def test_init_ui_builds_the_window_stack_and_bindings(monkeypatch):
    class Window(FakeWin):
        def __init__(self, opts=None):
            super().__init__()
            self.opts = opts
            self.handled_keys = set()

        def set_handled_keys(self, spin):
            self.handled_keys = set(spin.keys)

    monkeypatch.setattr(main_module, 'ConsoleWindow', Window)
    self = make_btrfs()

    self._init_ui()

    assert self.win.opts.theme == 'default'
    assert set(self.screens) == {
        main_module.HOME_ST, main_module.HELP_ST, main_module.THEMES_ST}
    assert self.ss.screen_objects is self.screens
    assert self.ss.curr.num == main_module.HOME_ST
    # The action keys are what the window forwards from prompt().
    assert ord('s') in self.win.handled_keys
    assert ord('x') in self.win.handled_keys


# --- inline prompt plumbing -------------------------------------------------

def test_begin_confirm_without_a_window_is_a_no_op():
    self = make_btrfs(win=None)
    assert self._begin_confirm('prompt') is False


def test_begin_and_end_confirm_restore_passthrough():
    win = FakeWin()
    win.passthrough_mode = True
    self = make_btrfs(win=win)

    assert self._begin_confirm('prompt', mode='y', identity='/@root') is True
    assert self.confirm.active is True
    assert win.passthrough_mode is True

    self._end_confirm()

    assert self.confirm.active is False
    assert win.passthrough_mode is True


def test_handle_confirm_key_when_no_prompt_is_active():
    self = make_btrfs(win=FakeWin())
    assert self.handle_confirm_key(ord('y')) is False


def test_handle_confirm_key_ignores_a_timeout():
    self = make_btrfs(win=FakeWin())
    self._begin_confirm('prompt', mode='y')
    assert self.handle_confirm_key(None) is True
    assert self.confirm.active is True


def test_confirm_line_supports_upper_case_mode():
    self = make_btrfs(win=FakeWin())
    self._begin_confirm('Delete "/@root"', mode='Y')
    assert 'Press "Y" or ESC' in self._confirm_line()


# --- retention helpers ------------------------------------------------------

def test_replace_eldest_returns_true_without_snapshots():
    self = make_btrfs()
    assert self._replace_eldest_snaps_of_subvol(
        make_subvol(), [], discard=3) is True


def test_replace_eldest_stops_when_a_deletion_fails(monkeypatch):
    subvol = make_subvol()
    snap = make_subvol('/@snapshots/root.x=Daily')
    subvol.snaps = [snap]
    self = make_btrfs()
    monkeypatch.setattr(self, '_del_subvolume', lambda *_a, **_k: False)

    assert self._replace_eldest_snaps_of_subvol(
        subvol, subvol.snaps, discard=1) is False


def test_replace_eldest_deletes_then_creates(monkeypatch):
    subvol = make_subvol()
    one = make_subvol('/@snapshots/root.1=Daily')
    two = make_subvol('/@snapshots/root.2=Daily')
    subvol.snaps = [one, two]
    self = make_btrfs()
    monkeypatch.setattr(self, '_del_subvolume', lambda *_a, **_k: True)
    monkeypatch.setattr(self, '_create_snap', lambda *_a, **_k: True)

    assert self._replace_eldest_snaps_of_subvol(
        subvol, subvol.snaps, suffix='=Daily', discard=2) is True
    assert subvol.snaps == []


def test_picked_subvol_and_label():
    subvol = make_subvol('/@root')
    snap = make_subvol('/@snapshots/root.1=Daily')
    snap.snap_of = subvol
    snap.snap_label = '=Daily'
    self = make_btrfs(win=None, rows=[])
    assert self._picked_subvol_ns() is None

    self.win = SimpleNamespace(pick_pos='bad')
    assert self._picked_subvol_ns() is None

    self.rows = [SimpleNamespace(subvol_ns=subvol),
                 SimpleNamespace(subvol_ns=snap)]
    self.win.pick_pos = 0
    assert self._picked_subvol_ns() is subvol
    assert self._picked_snap_label() == ''
    self.win.pick_pos = 1
    assert self._picked_subvol_ns() is snap
    assert self._picked_snap_label() == '=Daily'


def test_labeled_targets_require_the_label_group():
    one = make_subvol('/@root')
    two = make_subvol('/@home')
    one.label_groups = {'=Daily': [object()]}
    two.label_groups = {}
    self = make_btrfs(snap_targets=[one, two])
    assert self._labeled_targets('=Daily') == [one]


def test_confirm_labeled_snaps_rejects_an_unowned_snapshot(monkeypatch):
    self = make_btrfs(win=FakeWin(), snap_targets=[])
    monkeypatch.setattr(self, '_picked_subvol_ns', lambda: None)

    assert self._confirm_labeled_snaps('=Daily', False) is False
    assert 'this subvolume' in self.win.flashes[-1][0]


def test_confirm_labeled_snaps_rejects_a_stale_owner(monkeypatch):
    owner = make_subvol('/kub@home')
    picked = make_subvol('/@snapshots/kub.1=Daily')
    picked.snap_of = owner
    self = make_btrfs(win=FakeWin(), snap_targets=[])
    monkeypatch.setattr(self, '_picked_subvol_ns', lambda: picked)

    assert self._confirm_labeled_snaps('=Daily', False) is False
    assert owner.path in self.win.flashes[-1][0]


def test_confirm_labeled_snaps_rejects_an_empty_label_set(monkeypatch):
    owner = make_subvol('/@root')
    owner.label_groups = {}
    picked = make_subvol('/@snapshots/root.1=Daily')
    picked.snap_of = owner
    self = make_btrfs(win=FakeWin(), snap_targets=[owner])
    monkeypatch.setattr(self, '_picked_subvol_ns', lambda: picked)

    assert self._confirm_labeled_snaps('=Daily', False) is False
    assert 'No subvolumes have' in self.win.flashes[-1][0]


def test_labeled_confirmation_ignores_a_negative_answer(monkeypatch):
    self = make_btrfs()
    monkeypatch.setattr(self, '_do_replace_eldest_snaps',
                        lambda *_a, **_k: pytest.fail('must not run'))

    assert self._labeled_snaps_confirmed('=Daily', False, [], 'n') is None


def test_suffix_entered_reprompts_for_bad_input(monkeypatch):
    self = make_btrfs()
    calls = []
    monkeypatch.setattr(self, '_begin_confirm',
                        lambda *a, **k: calls.append((a, k)) or True)

    self._suffix_entered('bad', False)
    self._suffix_entered('', False)
    assert len(calls) == 1

    done = []
    monkeypatch.setattr(self, '_do_replace_eldest_snaps',
                        lambda suffix, just_add=False: done.append(
                            (suffix, just_add)))
    self._suffix_entered('.x=Daily', True)
    assert done == [('.x=Daily', True)]
    # The stubbed `_do_replace_eldest_snaps()` does not clear it the way the
    # real method does, so the parsed label remains visible here.
    assert self.label == '=Daily'


def test_do_replace_with_no_targets_returns_none():
    self = make_btrfs(snap_targets=[], keep=3)
    assert self._do_replace_eldest_snaps('.x=Daily') is None
    assert self.label is None


def test_do_replace_reports_a_failure_from_any_target(monkeypatch):
    one = make_subvol('/@root')
    two = make_subvol('/@home')
    self = make_btrfs(snap_targets=[one, two])
    monkeypatch.setattr(
        self, '_replace_eldest_snaps_of_subvol',
        lambda subvol, *_a, **_k: subvol is one)

    assert self._do_replace_eldest_snaps('.x=Daily') is False


@pytest.mark.parametrize('subvol,blocker', [
    (make_subvol('/@snapshots/root.1=Daily'), 'Cannot snap a snapshot'),
    (make_subvol('/@home'), 'Cannot snap an unmounted subvolume'),
])
def test_snap_blockers(subvol, blocker):
    if blocker == 'Cannot snap a snapshot':
        subvol.snap_of = make_subvol()
    else:
        subvol.mount = ''
    self = make_btrfs()
    assert self._snap_blocker(subvol) == blocker


def test_create_snap_uses_the_picked_row(monkeypatch):
    subvol = make_subvol('/@root')
    subvol.mount = '/'
    self = make_btrfs(win=FakeWin(pick_pos=0),
                      rows=[SimpleNamespace(subvol_ns=subvol)])
    monkeypatch.setattr(self, '_begin_confirm', lambda *_a, **_k: True)

    assert self._create_snap() is None
    assert self.confirm.active is False


def test_del_subvolume_uses_the_picked_row(monkeypatch):
    subvol = make_subvol('/@root')
    self = make_btrfs(win=FakeWin(pick_pos=0),
                      rows=[SimpleNamespace(subvol_ns=subvol)])
    monkeypatch.setattr(self, '_begin_confirm', lambda *_a, **_k: True)

    assert self._del_subvolume() is None


def test_del_subvolume_refuses_a_mounted_descendant():
    top = make_subvol('/@root')
    child = make_subvol('/@root/child')
    child.mount = '/child'
    top.children = [child]
    self = make_btrfs(win=FakeWin())

    assert self._del_subvolume(top, ans='y') is False
    assert 'Cannot delete mounted /child' in self.win.flashes[-1][0]


def test_gather_disk_usage_rejects_a_negative_answer():
    self = make_btrfs(win=FakeWin())
    assert self._gather_disk_usage('n') is False


# --- command plumbing -------------------------------------------------------

def test_slurp_command_debug_output(monkeypatch, capsys):
    class FakePopen:
        def __init__(self, command, **_kwargs):
            self.command = command
            self.returncode = 0

        def communicate(self):
            return b'out\n', b'err\n'

    monkeypatch.setattr(main_module.subprocess, 'Popen', FakePopen)
    self = make_btrfs(db=True)

    assert self._slurp_command(['btrfs', 'sub', 'list', '.']) == (
        ['out'], ['err'], 0)
    assert 'DB: + btrfs sub list .' in capsys.readouterr().out


def test_slurp_file_strips_lines(tmp_path):
    path = tmp_path / 'mounts'
    path.write_text(' a \n b\n', encoding='utf-8')
    assert BTRFS._slurp_file(str(path)) == ['a', 'b']


def test_dev_path_and_alias_lookup():
    assert BTRFS.dev_path('sda1') == '/dev/sda1'

    dev_ns = object()
    self = make_btrfs(devs={'uuid': dev_ns}, dev_aliases={'sda1': 'uuid'})
    assert self._dev_ns('sda1') is dev_ns
    assert self._dev_ns('missing') is None

    plain = make_btrfs()
    assert plain._dev_ns('anything') is None


# --- device scan and mount plumbing ----------------------------------------

def test_scan_block_devices_clears_the_blkid_cache(monkeypatch):
    self = make_btrfs()
    self.blkid_records = [{'stale': True}]
    monkeypatch.setattr(
        self, '_slurp_command',
        lambda _cmd: (['NAME="sda1" PATH="/dev/sda1" FSTYPE="btrfs"'], [],
                      0))

    records = self._scan_block_devices()

    assert records[0]['NAME'] == 'sda1'
    assert self.blkid_records is None


def test_load_devs_skips_unnamed_and_non_btrfs_records(monkeypatch, capsys):
    records = [
        {'NAME': 'sr0', 'PATH': '/dev/sr0', 'TYPE': 'rom',
         'FSTYPE': 'btrfs'},
        {'NAME': 'sda1', 'PATH': '/dev/sda1', 'FSTYPE': 'ext4'},
        {'NAME': 'sdb1', 'PATH': '/dev/sdb1', 'FSTYPE': 'btrfs'},
    ]
    self = make_btrfs(db=True)
    monkeypatch.setattr(self, '_scan_block_devices', lambda: records)
    monkeypatch.setattr(self, '_slurp_command',
                        lambda _cmd: (['header'], [], 0))

    self._load_devs()

    assert set(self.devs) == {'sdb1'}
    assert self.devs['sdb1'].diskfree == ''
    out = capsys.readouterr().out
    assert 'after load_devs()' in out


def test_fingerprint_records_falls_back_to_the_path_basename():
    assert BTRFS._fingerprint_records([
        {'PATH': '/dev/sda1', 'FSTYPE': 'btrfs', 'UUID': 'u', 'LABEL': 'l'}
    ]) == (('sda1', 'btrfs', 'u', 'l'),)


def test_record_name_prefers_name_then_basename():
    assert BTRFS._record_name({'NAME': 'sda1'}) == 'sda1'
    assert BTRFS._record_name({'PATH': '/dev/sdb1'}) == 'sdb1'


def test_mount_dir_and_remember_mount():
    self = make_btrfs()
    dev_ns = SimpleNamespace()

    assert self._mount_dir('sda1', 'ro') == '/tmp/.btrfs/sda1/ro'
    self._remember_tmp_mount(dev_ns, 'ro', '/tmp/.btrfs/sda1/ro')
    assert dev_ns.ro_path == '/tmp/.btrfs/sda1/ro'
    assert dev_ns.tmp_path == '/tmp/.btrfs/sda1/ro'
    self._remember_tmp_mount(dev_ns, 'rw', '/tmp/.btrfs/sda1/rw')
    assert dev_ns.rw_path == '/tmp/.btrfs/sda1/rw'
    assert dev_ns.tmp_path == '/tmp/.btrfs/sda1/ro'


def test_ensure_tmp_mount_rejects_an_unknown_device():
    self = make_btrfs()
    assert self._ensure_tmp_mount('missing') is None


def test_ensure_tmp_mount_reports_a_failed_mount(monkeypatch):
    self = make_btrfs(devs={'sda1': SimpleNamespace(device_path='/dev/sda1')})
    monkeypatch.setattr(main_module.os.path, 'ismount', lambda _path: False)
    monkeypatch.setattr(main_module.os, 'makedirs', lambda *_a, **_k: None)
    monkeypatch.setattr(
        main_module.subprocess, 'run',
        lambda *_a, **_k: SimpleNamespace(returncode=1))

    assert self._ensure_tmp_mount('sda1') is None


def test_mount_tmps_builds_each_subvolume_tree(monkeypatch):
    dev_ns = SimpleNamespace(dev='sda1')
    self = make_btrfs(devs={'sda1': dev_ns})
    calls = []
    monkeypatch.setattr(main_module.os, 'makedirs', lambda *_a, **_k: None)
    monkeypatch.setattr(self, '_progress', lambda *_a, **_k: None)
    monkeypatch.setattr(self, '_ensure_tmp_mount',
                        lambda dev, mode: f'/tmp/{dev}/{mode}')
    monkeypatch.setattr(
        self, '_slurp_command',
        lambda _cmd: (['ID 1 gen 1 top level 5 path @root'], [], 0))
    monkeypatch.setattr(
        self, '_init_dev_subvols',
        lambda dev, triples: calls.append((dev.dev, triples)))

    self._mount_tmps()

    assert calls == [('sda1', [('1', '5', '/@root')])]


def test_mount_tmps_reports_a_failed_discovery_mount(monkeypatch):
    self = make_btrfs(devs={'sda1': SimpleNamespace(dev='sda1')})
    monkeypatch.setattr(main_module.os, 'makedirs', lambda *_a, **_k: None)
    monkeypatch.setattr(self, '_progress', lambda *_a, **_k: None)
    monkeypatch.setattr(self, '_ensure_tmp_mount', lambda *_a, **_k: None)

    with pytest.raises(BTRFSError, match='cannot mount sda1'):
        self._mount_tmps()


def test_umount_tmps_does_not_unmount_the_same_path_twice(monkeypatch):
    dev_ns = SimpleNamespace(ro_path='/tmp/ro', tmp_path='/tmp/ro',
                             rw_path='/tmp/rw')
    self = make_btrfs(devs={'sda1': dev_ns})
    calls = []
    monkeypatch.setattr(main_module.os.path, 'ismount', lambda _path: True)
    monkeypatch.setattr(
        main_module.subprocess, 'run',
        lambda cmd, check=False: calls.append(list(cmd)))

    self.umount_tmps()

    assert calls == [['umount', '/tmp/rw'], ['umount', '/tmp/ro']]


def test_all_mounts_skips_unparseable_lines(monkeypatch):
    self = make_btrfs()
    monkeypatch.setattr(self, '_load_mounts', lambda: [
        '/dev/sda1 / btrfs rw,subvolid=5,subvol=/ 0 0', 'garbage'])
    assert self._all_mounts() == [
        ('/dev/sda1', '/', 'btrfs', '5', '/')]


def test_attach_mounts_sets_mounts_and_the_snapshot_subvolume():
    root = make_subvol('/@root', ident='256')
    snaps = make_subvol('/@snapshots', ident='300')
    self = make_btrfs(devs={'u': SimpleNamespace(subvols=[root, snaps])})
    self.subvol_iter = lambda: iter([root, snaps])

    self._attach_mounts([
        ('/dev/sda1', '/', 'btrfs', '256', '/@root'),
        ('/dev/sda2', '/.snapshots', 'btrfs', '300', '/@snapshots'),
        ('/dev/sdb1', '/data', 'ext4', None, None),
    ])

    assert root.mount == '/'
    assert snaps.mount == '/.snapshots'
    assert self.snap_subvol is snaps


def test_attach_mounts_rejects_a_non_btrfs_root():
    self = make_btrfs()

    with pytest.raises(BTRFSError, match='root is not BTRFS'):
        self._attach_mounts([
            ('/dev/sda1', '/', 'ext4', None, None)])


def test_collect_snap_targets_sorts_and_filters():
    snap_subvol = make_subvol('/@snapshots')
    mounted = make_subvol('/@root')
    mounted.mount = '/'
    unmounted = make_subvol('/@home')
    nested = make_subvol('/@root/nested')
    nested.mount = '/x'
    nested.depth = 1
    dev = SimpleNamespace(subvols=[unmounted, nested, snap_subvol, mounted])
    self = make_btrfs(devs={'u': dev}, snap_subvol=snap_subvol)

    self._collect_snap_targets()

    assert self.snap_targets == [mounted]
    assert dev.subvols[0] is mounted


def test_determine_mount_points_requires_the_snapshots_mount(monkeypatch):
    self = make_btrfs()
    monkeypatch.setattr(self, '_attach_mounts', lambda _mounts: None)
    monkeypatch.setattr(self, '_collect_snap_targets', lambda: None)

    with pytest.raises(BTRFSError, match=r'cannot find a BTRFS mount at '):
        self._determine_mount_points()


# --- snapshot linking and rows ---------------------------------------------

def test_gather_snapshots_links_known_and_unknown_origins():
    root = make_subvol('/@root')
    snapshots = make_subvol('/@snapshots')
    known = make_subvol('/@snapshots/@root.2024-01-10-174732=Daily')
    unknown = make_subvol('/@snapshots/missing.2024-01-10-174732=Daily')
    malformed = make_subvol('/@snapshots/not-a-snapshot')
    dev = SimpleNamespace(paths={'/@root': root, '/@snapshots': snapshots})
    self = make_btrfs(snap_subvol=snapshots)
    self.subvol_iter = lambda: iter([root, snapshots, known, unknown,
                                     malformed])
    self._dev_ns = lambda _dev: dev

    self.gather_snapshots()

    assert root.snaps == [known]
    assert known.snap_of is root
    assert known.snap_label == '=Daily'
    assert root.label_groups == {'=Daily': [known]}
    assert snapshots.snaps == [unknown, malformed]
    # The malformed name has no label; the unknown origin keeps its own.
    assert self.label_set == {'', '=Daily'}


def test_make_rows_includes_snapshot_rows(monkeypatch):
    root = make_subvol('/@root')
    snap = make_subvol('/@snapshots/@root.1=Daily')
    root.snaps = [snap]
    dev = SimpleNamespace(dev='sda1', subvols=[root])
    self = make_btrfs(devs={'u': dev})

    self.make_rows()

    assert [row.path for row in self.rows] == ['/@root',
                                               '/@snapshots/@root.1=Daily']


# --- disk usage -------------------------------------------------------------

def prepare_usage(monkeypatch, exclusive):
    snap = make_subvol('/@snapshots/@root.1=Daily')
    root = make_subvol('/@root')
    snap.snap_of = root
    dev = SimpleNamespace(tmp_path='/tmp/ro',
                          paths={'/@snapshots/@root.1=Daily': snap})
    self = make_btrfs(
        snap_subvol=SimpleNamespace(dev='sda1', path='/@snapshots'),
        rows=[SimpleNamespace(subvol_ns=snap, size=None)])
    self._dev_ns = lambda _dev: dev
    monkeypatch.setattr(main_module.os, 'listdir',
                        lambda _path: ['@root.1=Daily'])
    monkeypatch.setattr(
        self, '_slurp_command',
        lambda *_a, **_k: ([
            'header',
            f'1.0GiB {exclusive} 3 /@snapshots/@root.1=Daily'], [], 0))
    return self, snap, root


@pytest.mark.parametrize('exclusive,expected', [
    ('1.0GiB', 2**30),
    ('1.0MiB', 2**20),
    ('1.0KiB', 2**10),
    ('100B', 100),
])
def test_get_disk_usage_converts_units(monkeypatch, exclusive, expected):
    self, snap, root = prepare_usage(monkeypatch, exclusive)

    self._get_disk_usage()

    assert snap.size == expected
    assert root.size == 2**30
    assert self.rows[0].size == expected
    assert self.show_size is True


def test_get_disk_usage_reports_an_unparseable_size(monkeypatch, capsys):
    self, snap, _root = prepare_usage(monkeypatch, '???')

    self._get_disk_usage()

    assert snap.size == 0
    assert 'cannot parse' in capsys.readouterr().out


def test_get_disk_usage_returns_without_a_snapshot_subvolume():
    self = make_btrfs(snap_subvol=None)
    assert self._get_disk_usage() is None


def test_get_disk_usage_returns_without_a_device():
    self = make_btrfs(snap_subvol=SimpleNamespace(dev='missing'))
    self._dev_ns = lambda _dev: None
    assert self._get_disk_usage() is None


def test_get_disk_usage_tolerates_an_unreadable_snapshot_directory(
        monkeypatch):
    snap_subvol = SimpleNamespace(dev='sda1', path='/@snapshots')
    dev = SimpleNamespace(tmp_path='/tmp/ro', paths={})
    self = make_btrfs(snap_subvol=snap_subvol)
    self._dev_ns = lambda _dev: dev
    monkeypatch.setattr(
        main_module.os, 'listdir',
        lambda _path: (_ for _ in ()).throw(OSError('missing')))

    self._get_disk_usage()

    assert self.show_size is True


# --- rendering --------------------------------------------------------------

def test_add_row_handles_snapshots_and_regular_subvolumes():
    win = FakeWin()
    self = make_btrfs(win=win, show_size=True)
    snapshots = make_subvol('/@snapshots')
    self.snap_subvol = snapshots
    snap = make_subvol('/@snapshots/@root.1=Daily')
    snap.snap_of = make_subvol('/@root')
    snap.ago_str = '1d0h'
    regular = make_subvol('/@home')
    regular.mount = '/home'
    widths = (5, 4, 30)

    self._add_row(win, SimpleNamespace(path=regular.path, mount='/home',
                                      dev='sda1', size=2**20,
                                      subvol_ns=regular), widths, False)
    self._add_row(win, SimpleNamespace(path=snap.path, mount='', dev='sda1',
                                      size=None, subvol_ns=snap), widths,
                  False)
    orphan = make_subvol('/@snapshots/orphan')
    orphan.snap_of = snapshots
    self._add_row(win, SimpleNamespace(path=orphan.path, mount='',
                                      dev='sda1', size=None,
                                      subvol_ns=orphan), widths, False)

    assert any('--> @root.1=Daily 1d0h' in line for line, _attr in win.body)
    assert any('!!! orphan' in line for line, _attr in win.body)


def test_non_snapshot_rows_get_the_hotswap_role(monkeypatch):
    win = FakeWin()
    self = make_btrfs(win=win)
    monkeypatch.setattr(main_module, 'semantic_attr', lambda _role: 41)
    self.snap_subvol = make_subvol('/@snapshots')
    snap = make_subvol('/@snapshots/@root.1=Daily')
    snap.snap_of = make_subvol('/@root')
    regular = make_subvol('/@home')
    regular.mount = '/home'
    widths = (5, 4, 30)

    self._add_row(win, SimpleNamespace(path=regular.path, mount='/home',
                                      dev='sda1', size=None,
                                      subvol_ns=regular), widths, False)
    self._add_row(win, SimpleNamespace(path=snap.path, mount='', dev='sda1',
                                      size=None, subvol_ns=snap), widths,
                  False)

    assert win.body[0][1] == 41
    assert win.body[1][1] is None


ROLE_ATTRS = {Theme.DANGER: 41, Theme.SUCCESS: 42,
              Theme.WARNING: 43, Theme.HOTSWAP: 44}


def row_attr(monkeypatch, path, mount):
    """ Render one row and return the attribute `_row_attr()` gave it. """
    win = FakeWin()
    self = make_btrfs(win=win)
    monkeypatch.setattr(main_module, 'semantic_attr',
                        lambda role: ROLE_ATTRS.get(role, 45))
    ns = make_subvol(path)
    ns.mount = mount
    self._add_row(win, SimpleNamespace(path=path, mount=mount, dev='sda1',
                                       size=None, subvol_ns=ns), (5, 4, 30),
                  False)
    return win.body[0][1]


@pytest.mark.parametrize('path,mount,expected', [
    ('/@root.2024-01-14-101010=Reverted', '', ROLE_ATTRS[Theme.SUCCESS]),
    ('/@root.2024-01-14-101010=Reverted', '/', ROLE_ATTRS[Theme.HOTSWAP]),
    ('/@home', '/home', ROLE_ATTRS[Theme.HOTSWAP]),
    ('/@home', '', ROLE_ATTRS[Theme.WARNING]),
    ('/@optToDel', '', ROLE_ATTRS[Theme.WARNING]),
])
def test_row_roles_follow_the_mount_state(monkeypatch, path, mount, expected):
    assert row_attr(monkeypatch, path, mount) == expected


def test_refresh_info_renders_a_transient_body():
    win = FakeWin()
    self = make_btrfs(win=win)
    self._refresh_if_dirty = lambda: None
    self.devs = {'u': SimpleNamespace(diskfree='df line')}

    self.refresh_info(body='working')

    assert ('working', cs.A_REVERSE) in win.body
    assert any('df: df line' in line for line, _attr in win.headers)


def test_refresh_info_renders_body_lists():
    win = FakeWin()
    self = make_btrfs(win=win)
    self._refresh_if_dirty = lambda: None

    self.refresh_info(body=['one', 'two'])

    assert [line for line, _attr in win.body] == ['one', 'two']


# --- arguments and top level ------------------------------------------------

@pytest.mark.parametrize('cron,label,limit', [
    ('hourly', '=Hourly', 2),
    ('daily', '=Daily', 2),
    ('weekly', '=Weekly', 2),
    ('monthly', '=Monthly', 1),
])
def test_parse_args_cron_defaults(cron, label, limit):
    opts = main_module.parse_args(['--cron', cron])

    assert opts.label == label
    assert opts.add_snap_max == limit
    assert opts.print is False


def test_parse_args_honours_an_explicit_cron_label():
    opts = main_module.parse_args(['--cron', 'daily', '-L', 'Custom'])
    assert opts.label == 'Custom'


def test_main_reexecs_and_unmounts(monkeypatch):
    class FakeBtrfs:
        current = None

        def __init__(self, opts):
            self.opts = opts
            self.unmounted = False

        def umount_tmps(self):
            self.unmounted = True

    calls = []
    monkeypatch.setattr(main_module, 'reexec_as_root',
                        lambda module, argv: calls.append((module, argv)))
    monkeypatch.setattr(main_module, 'BTRFS', FakeBtrfs)

    main_module.main(['-p'])

    assert calls == [('my_snaps.main', ['-p'])]
    assert FakeBtrfs.current.unmounted is True


def test_run_translates_keyboard_interrupt(monkeypatch, capsys):
    class Current:
        win = FakeWin()

        def stop_curses(self):
            self.win.stop_curses()

    monkeypatch.setattr(main_module, 'main',
                        lambda: (_ for _ in ()).throw(KeyboardInterrupt))
    monkeypatch.setattr(main_module.BTRFS, 'current', Current())

    with pytest.raises(SystemExit) as caught:
        main_module.run()

    assert caught.value.code == 0
    assert Current.win.stopped is True
    assert 'QUITTING NOW' in capsys.readouterr().out


def test_run_translates_a_btrfs_error(monkeypatch, capsys):
    class Current:
        win = FakeWin()

        def stop_curses(self):
            self.win.stop_curses()

    monkeypatch.setattr(
        main_module, 'main',
        lambda: (_ for _ in ()).throw(BTRFSError('no snapshots mount')))
    monkeypatch.setattr(main_module.BTRFS, 'current', Current())

    with pytest.raises(SystemExit) as caught:
        main_module.run()

    assert caught.value.code == 1
    assert Current.win.stopped is True
    assert 'ERROR: no snapshots mount' in capsys.readouterr().err


def test_run_translates_an_unexpected_exception(monkeypatch, capsys):
    class Current:
        win = FakeWin()

        def stop_curses(self):
            self.win.stop_curses()

    monkeypatch.setattr(
        main_module, 'main',
        lambda: (_ for _ in ()).throw(ValueError('boom')))
    monkeypatch.setattr(main_module.BTRFS, 'current', Current())

    with pytest.raises(SystemExit) as caught:
        main_module.run()

    assert caught.value.code == 15
    assert Current.win.stopped is True
    out = capsys.readouterr().out
    assert 'exception: boom' in out
    assert 'Traceback' in out
