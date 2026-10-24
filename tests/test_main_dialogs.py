"""Regression tests for the confirmations driven by my_snaps.main.

Historically these operations used a modal `ConsoleWindow.answer()` dialog.
They now use console_window's `InlineConfirmation` widget, so the prompts are
drawn as ordinary body lines and keys are routed to `handle_confirm_key()`.
The tests below assert the two behaviours that matter:

* a destructive operation does nothing until the confirmation succeeds, and
* ESC always cancels without touching BTRFS.
"""
from __future__ import annotations

import curses as cs
from types import SimpleNamespace

import pytest
from console_window import InlineConfirmation

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
    self.snap_subvol = None
    self.dirty = False

    # Inline-confirmation state, normally created in BTRFS.__init__().
    self.confirm = InlineConfirmation()
    self._confirm_prompt = ''
    self._confirm_labels = ''
    self._confirm_callback = None
    self._confirm_prev_passthrough = False
    return self


def make_subvol(path='/@root'):
    return BTRFS.init_subvol_ns(dev='dev0', path=path, ident='256')


class FakeWin:
    """The tiny slice of ConsoleWindow that the inline prompts touch."""

    def __init__(self, pick_pos=0):
        self.pick_pos = pick_pos
        self.passthrough_mode = False
        self.handled_keys = set()
        self.alerts = []
        self.flashes = []

    def alert(self, message='', title='ALERT', **_kwargs):
        self.alerts.append((title, message))

    def flash(self, message='', duration=2.0):
        self.flashes.append((message, duration))


def test_get_label_handles_none():
    assert BTRFS._get_label(None) == ''
    assert BTRFS._get_label('') == ''
    assert BTRFS._get_label('@snapshots/root.2024-01-10-174732=Update') == '=Update'


def test_del_subvolume_starts_inline_y_confirmation():
    subvol = make_subvol()
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: pytest.fail(f'unexpected command: {cmd}')

    assert self._del_subvolume(subvol) is None
    assert self.confirm.active is True
    assert self.confirm.mode == 'y'
    assert self.confirm.identity == '/@root'
    assert win.passthrough_mode is True
    # Passthrough mode itself forwards ESC since console-window 2.0.0.10.
    assert win.handled_keys == set()
    assert self.dirty is False


def test_del_subvolume_esc_cancels_without_deleting():
    subvol = make_subvol()
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: pytest.fail(f'unexpected command: {cmd}')

    self._del_subvolume(subvol)
    assert self.handle_confirm_key(27) is True

    assert self.confirm.active is False
    assert win.passthrough_mode is False
    assert win.handled_keys == set()
    assert self.dirty is False


def test_del_subvolume_requires_y():
    subvol = make_subvol()
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: pytest.fail(f'unexpected command: {cmd}')

    self._del_subvolume(subvol)
    # 'n' (and ENTER) must not confirm the single-key prompt.
    self.handle_confirm_key(ord('n'))
    self.handle_confirm_key(10)
    assert self.confirm.active is True
    assert self.dirty is False


def test_del_subvolume_confirm_y_deletes():
    subvol = make_subvol()
    win = FakeWin()
    self = make_btrfs(win)

    class Device:
        tmp_path = '/tmp/.btrfs/dev0'

    self.devs['dev0'] = Device()
    commands = []
    self._slurp_command = lambda cmd: (commands.append(cmd) or ([], [], 0))

    self._del_subvolume(subvol)
    self.handle_confirm_key(ord('y'))

    assert self.confirm.active is False
    assert win.passthrough_mode is False
    assert commands == ['btrfs sub del /tmp/.btrfs/dev0/@root']
    assert self.dirty is True


def test_del_subvolume_reports_failed_command():
    subvol = make_subvol()
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    self._slurp_command = lambda cmd: (['out'], ['err'], 1)

    # ans='y' skips the prompt; a failing "btrfs sub del" must alert, not raise.
    assert self._del_subvolume(subvol, ans='y') is False
    assert win.alerts and 'FAILED' in win.alerts[0][1]
    assert 'err' in win.alerts[0][1]
    assert self.dirty is False


def test_create_snap_starts_inline_suffix_prompt():
    subvol = make_subvol()
    subvol.mount = '/mnt/@root'
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')

    assert self._create_snap(subvol) is None
    assert self.confirm.active is True
    assert self.confirm.mode == 'text'
    assert self.confirm.identity == '/@root'
    assert self.confirm.input_buffer.startswith('.')


def test_replace_eldest_snaps_starts_inline_suffix_prompt():
    win = FakeWin()
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]

    assert self._replace_eldest_snaps() is None
    assert self.confirm.active is True
    assert self.confirm.mode == 'text'
    assert self.confirm.identity is None
    assert self.confirm.input_buffer.startswith('.')
    assert self.label is None


def test_replace_eldest_snaps_abort_on_esc():
    win = FakeWin()
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]

    self._replace_eldest_snaps()
    assert self.handle_confirm_key(27) is True
    assert self.confirm.active is False
    assert self.label is None


def test_handle_confirm_key_ignores_prompt_timeout():
    win = FakeWin()
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]

    self._replace_eldest_snaps()
    # A resize/timeout makes win.prompt() return None; the prompt must stay up.
    assert self.handle_confirm_key(None) is True
    assert self.confirm.active is True


def test_replace_eldest_snaps_confirm_runs_retention():
    win = FakeWin()
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]
    calls = []
    self._do_replace_eldest_snaps = (
        lambda suffix, just_add=False: calls.append((suffix, just_add)))

    self._replace_eldest_snaps(just_add=True)
    suffix = self.confirm.input_buffer
    self.handle_confirm_key(10)  # ENTER accepts the seeded suffix

    assert calls == [(suffix, True)]
    assert self.confirm.active is False


def test_replace_eldest_snaps_invalid_suffix_reprompts_inline():
    win = FakeWin()
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]

    self._replace_eldest_snaps()
    self.confirm.input_buffer = '.bad'  # too short; no alert dialog wanted
    self.handle_confirm_key(10)

    assert self.confirm.active is True
    assert self.confirm.input_buffer == '.bad'
    assert 'Suffix must be' in self._confirm_prompt
    assert win.alerts == []


def test_confirm_line_leads_with_warning_glyph_and_short_hint():
    self = make_btrfs(FakeWin())
    self._begin_confirm('Delete "/@root"', mode='y')

    line = self._confirm_line()

    assert line.lstrip().startswith('⚠️')
    assert '[Press "y" or ESC]' in line
    # The old verbose wording must be gone.
    assert 'to confirm' not in line


def test_confirm_line_text_mode_shows_the_typed_buffer_and_caret():
    self = make_btrfs(FakeWin())
    self._begin_confirm('Set snap suffix, then ENTER or ESC', mode='text',
                        seed='.2026-10-24')

    line = self._confirm_line()

    assert line.lstrip().startswith('⚠️')
    # The keys come BEFORE the text being edited, the caret sits after it.
    assert line.index('ENTER') < line.index('.2026-10-24')
    assert line.endswith('│')
    assert '.2026-10-24│' in line


def test_inline_prompt_uses_passthrough_and_leaves_handled_keys_alone():
    win = FakeWin()
    self = make_btrfs(win)
    self._begin_confirm('Set snap suffix, then ENTER or ESC', mode='text',
                        seed='.2026')

    # 2.0.0.10 forwards ESC/Delete in passthrough mode, so the app needs no
    # handled_keys bookkeeping at all.
    assert win.passthrough_mode is True
    assert win.handled_keys == set()

    self.handle_confirm_key(27)

    assert self.confirm.active is False
    assert win.passthrough_mode is False
    assert win.handled_keys == set()


def test_inline_prompt_seeds_the_buffer_through_start():
    self = make_btrfs(FakeWin())
    self._begin_confirm('Set snap suffix, then ENTER or ESC', mode='text',
                        seed='.2026')

    # The seed is what start(initial=...) was given, caret at its end, so a
    # plain read of the widget is enough to render (no app-side caret state).
    assert self.confirm.input_buffer == '.2026'
    assert self.confirm.cursor_pos == len('.2026')
    assert '.2026│' in self._confirm_line()


def test_arrows_move_the_caret_and_typing_inserts_there():
    self = make_btrfs(FakeWin())
    self._begin_confirm('Set snap suffix, then ENTER or ESC', mode='text',
                        seed='.2026')

    self.handle_confirm_key(cs.KEY_LEFT)
    self.handle_confirm_key(cs.KEY_LEFT)
    assert self.confirm.input_buffer == '.2026'
    assert self.confirm.cursor_pos == 3
    assert '.20│26' in self._confirm_line()

    self.handle_confirm_key(ord('X'))
    assert self.confirm.input_buffer == '.20X26'
    assert '.20X│26' in self._confirm_line()

    # Past the start / past the end the caret just stops.
    for _ in range(10):
        self.handle_confirm_key(cs.KEY_LEFT)
    assert self.confirm.cursor_pos == 0
    for _ in range(10):
        self.handle_confirm_key(cs.KEY_RIGHT)
    assert self.confirm.cursor_pos == len('.20X26')


def test_backspace_and_delete_act_at_the_caret():
    self = make_btrfs(FakeWin())
    self._begin_confirm('Set snap suffix, then ENTER or ESC', mode='text',
                        seed='.abcdef')

    for _ in range(3):  # caret between 'c' and 'd'
        self.handle_confirm_key(cs.KEY_LEFT)
    self.handle_confirm_key(127)  # backspace eats 'c'
    assert self.confirm.input_buffer == '.abdef'
    self.handle_confirm_key(cs.KEY_DC)  # delete eats 'd'
    assert self.confirm.input_buffer == '.abef'
    assert '.ab│ef' in self._confirm_line()


def test_home_and_end_jump_to_the_edges():
    self = make_btrfs(FakeWin())
    self._begin_confirm('Set snap suffix, then ENTER or ESC', mode='text',
                        seed='.2026')

    self.handle_confirm_key(cs.KEY_HOME)
    assert self.confirm.cursor_pos == 0
    self.handle_confirm_key(ord('X'))
    assert self.confirm.input_buffer == 'X.2026'

    self.handle_confirm_key(cs.KEY_END)
    self.handle_confirm_key(ord('Y'))
    assert self.confirm.input_buffer == 'X.2026Y'
    assert self.confirm.input_buffer.endswith('Y')


def test_edited_text_is_what_the_callback_receives():
    win = FakeWin()
    self = make_btrfs(win)
    self.snap_targets = [make_subvol()]
    seen = []
    self._do_replace_eldest_snaps = (
        lambda suffix, just_add=False: seen.append(suffix))

    self._replace_eldest_snaps()
    seed = self.confirm.input_buffer
    self.handle_confirm_key(cs.KEY_END)
    self.handle_confirm_key(ord('Z'))
    self.handle_confirm_key(10)  # ENTER

    assert seen == [seed + 'Z']
    assert self.confirm.active is False


def test_create_snap_prompt_does_not_repeat_the_subvolume_name():
    subvol = make_subvol()
    subvol.mount = '/mnt/@root'
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')

    self._create_snap(subvol)

    assert self._confirm_prompt == 'Set snap suffix, then ENTER or ESC'
    assert '/@root' not in self._confirm_line()


def test_create_snap_refusals_flash_instead_of_alerting():
    win = FakeWin()
    self = make_btrfs(win)
    self.devs['dev0'] = SimpleNamespace(tmp_path='/tmp/.btrfs/dev0')
    snapping_snap = make_subvol('/@snapshots/snap.2024=Update')
    snapping_snap.snap_of = '/@root'

    assert self._create_snap(snapping_snap) is False
    assert win.alerts == []
    assert win.flashes == [('Cannot snap a snapshot', 4.0)]
    assert self.confirm.active is False

    unmounted = make_subvol('/@home')
    unmounted.mount = ''
    assert self._create_snap(unmounted) is False
    assert win.flashes[-1] == ('Cannot snap an unmounted subvolume', 4.0)

    snap_subvol = make_subvol('/@snapshots')
    snap_subvol.mount = '/.snapshots'
    assert self._create_snap(snap_subvol) is False
    assert win.flashes[-1] == ('Cannot snap the snapshot subvolume', 4.0)
    assert win.alerts == []


def test_labels_are_drawn_on_their_own_dim_line():
    win = FakeWin()
    self = make_btrfs(win)
    self.label_set = {'=Daily', '=Update'}
    self.snap_targets = [make_subvol()]

    self._replace_eldest_snaps()
    lines = self._confirm_lines()

    assert lines[0][1] == cs.A_BOLD
    assert 'labels' not in lines[0][0]
    assert lines[1][0].strip() == 'labels: =Daily =Update'
    assert lines[1][1] == cs.A_DIM


class RenderWin(FakeWin):
    """FakeWin that records the body lines and their attributes."""

    def __init__(self):
        super().__init__()
        self.body = []

    def add_header(self, *_args, **_kwargs):
        pass

    def add_fancy_header(self, *_args, **_kwargs):
        pass

    def add_body(self, line, attr=None, **_kwargs):
        self.body.append((line, attr))


def test_refresh_info_draws_prompt_bold_without_reverse_video():
    win = RenderWin()
    self = make_btrfs(win)
    self.show_size = False
    self.snap_subvol = '/@snapshots'
    subvol = make_subvol('/@snapshots/snap.2024-01-10-174732=Update')
    self.rows = [SimpleNamespace(path=subvol.path, mount='', dev='dev0',
                                 size=None, subvol_ns=subvol)]

    self._begin_confirm('Delete "/@root"', mode='y',
                        identity=subvol.path)
    self.refresh_info()

    prompts = [(line, attr) for line, attr in win.body
               if line.lstrip().startswith('⚠️')]
    assert len(prompts) == 1
    assert prompts[0][1] == cs.A_BOLD
    assert not prompts[0][1] & cs.A_REVERSE


def make_labeled_snap_setup(pick_pos=1, label='=Daily'):
    """Rows show a subvolume then one labeled snapshot of it."""
    subvol = make_subvol('/@root')
    snap = make_subvol(f'/@snapshots/root.2026-10-23-073714{label}')
    snap.snap_of = subvol
    snap.snap_label = label
    subvol.snaps = [snap]
    subvol.label_groups = {label: [snap]}

    win = FakeWin(pick_pos=pick_pos)
    self = make_btrfs(win)
    self.snap_subvol = make_subvol('/@snapshots')
    self.devs['dev0'] = SimpleNamespace(subvols=[subvol])
    self.snap_targets = [subvol]
    self.rows = [
        SimpleNamespace(path=subvol.path, mount='/', dev='dev0',
                        size=None, subvol_ns=subvol),
        SimpleNamespace(path=snap.path, mount='', dev='dev0',
                        size=None, subvol_ns=snap),
    ]
    return self, subvol, snap


def test_replace_on_labeled_snap_asks_yes_no_for_that_label():
    self, subvol, snap = make_labeled_snap_setup()
    calls = []
    self._do_replace_eldest_snaps = (
        lambda suffix, just_add=False, targets=None:
            calls.append((suffix, just_add, targets)))

    assert self._replace_eldest_snaps() is None
    assert self.confirm.active is True
    assert self.confirm.mode == 'y'
    assert self.confirm.identity == snap.path
    assert self._confirm_prompt == 'Replace a =Daily snap for 1 subvol?'
    assert calls == []

    self.handle_confirm_key(ord('y'))

    assert self.confirm.active is False
    suffix, just_add, targets = calls[0]
    assert suffix.endswith('=Daily')
    assert just_add is False
    assert targets == [subvol]


def test_add_on_labeled_snap_asks_yes_no_and_uses_just_add():
    self, subvol, _snap = make_labeled_snap_setup()
    calls = []
    self._do_replace_eldest_snaps = (
        lambda suffix, just_add=False, targets=None:
            calls.append((suffix, just_add, targets)))

    self._replace_eldest_snaps(just_add=True)

    assert self.confirm.mode == 'y'
    assert self._confirm_prompt == 'Add a =Daily snap for 1 subvol?'

    self.handle_confirm_key(ord('y'))

    assert calls[0][1] is True
    assert calls[0][2] == [subvol]


def test_labeled_prompt_esc_cancels_without_touching_btrfs():
    self, _subvol, _snap = make_labeled_snap_setup()
    self._do_replace_eldest_snaps = (
        lambda *_args, **_kwargs: pytest.fail('must not run'))

    self._replace_eldest_snaps()
    assert self.handle_confirm_key(27) is True

    assert self.confirm.active is False
    assert self.label is None


def test_replace_on_subvolume_row_still_prompts_for_suffix():
    self, _subvol, _snap = make_labeled_snap_setup(pick_pos=0)

    self._replace_eldest_snaps()

    assert self.confirm.mode == 'text'
    assert self.confirm.identity is None


def test_replace_on_unlabeled_snap_still_prompts_for_suffix():
    self, _subvol, snap = make_labeled_snap_setup()
    snap.snap_label = ''

    self._replace_eldest_snaps()

    assert self.confirm.mode == 'text'
    assert self.confirm.identity is None


def test_labeled_prompt_counts_subvols_with_that_label():
    self, subvol, _snap = make_labeled_snap_setup()
    other = make_subvol('/@home')
    other.label_groups = {
        '=Daily': [make_subvol('/@snapshots/home.2026-10-23-073714=Daily')]}
    other_snap = other.label_groups['=Daily'][0]
    other_snap.snap_of = other
    other_snap.snap_label = '=Daily'
    self.devs['dev0'].subvols.append(other)
    self.snap_targets.append(other)

    self._replace_eldest_snaps()

    assert self._confirm_prompt == 'Replace a =Daily snap for 2 subvols?'
    assert subvol in self._labeled_targets('=Daily')
    assert other in self._labeled_targets('=Daily')


def test_do_replace_eldest_snaps_honors_targets():
    self, subvol, _snap = make_labeled_snap_setup()
    other = make_subvol('/@home')
    other.label_groups = {
        '=Daily': [make_subvol('/@snapshots/home.2026-10-23-073714=Daily')]}
    self.devs['dev0'].subvols.append(other)

    done = []
    self._replace_eldest_snaps_of_subvol = (
        lambda sv, like, suffix=None, discard=1:
            done.append(sv.path) or True)

    self.label = '=Daily'
    self._do_replace_eldest_snaps('.x=Daily', targets=[subvol])

    assert done == ['/@root']


def add_stale_unmounted_family(self):
    """A /kub@home-like subvolume: has a =Daily snap but is not a target."""
    stale = make_subvol('/kub@home')
    stale.mount = ''
    stale_snap = make_subvol('/@snapshots/kub@home.2026-01-31-073612=Daily')
    stale_snap.snap_of = stale
    stale_snap.snap_label = '=Daily'
    stale.label_groups = {'=Daily': [stale_snap]}
    self.devs['dev0'].subvols.append(stale)
    return stale, stale_snap


def test_labeled_targets_skip_stale_unmounted_subvolumes():
    self, subvol, _snap = make_labeled_snap_setup()
    add_stale_unmounted_family(self)

    # The stale family is visible in the tree but is not a snapshot target.
    assert [ns.path for ns in self.subvol_iter()] == ['/@root', '/kub@home']
    assert self._labeled_targets('=Daily') == [subvol]

    self._replace_eldest_snaps()

    assert self._confirm_prompt == 'Replace a =Daily snap for 1 subvol?'


def test_labeled_action_on_stale_snap_is_refused():
    self, _subvol, _snap = make_labeled_snap_setup()
    _stale, stale_snap = add_stale_unmounted_family(self)
    self.rows.append(SimpleNamespace(path=stale_snap.path, mount='',
                                     dev='dev0', size=None,
                                     subvol_ns=stale_snap))
    self.win.pick_pos = 2
    self._do_replace_eldest_snaps = (
        lambda *_args, **_kwargs: pytest.fail('must not run'))

    assert self._replace_eldest_snaps() is False

    assert self.confirm.active is False
    assert self.win.flashes == [(
        'Cannot replace snaps of /kub@home '
        '(not a mounted top-level subvolume)', 4.0)]
