"""Coverage for the terminal-independent parts of `InlineMenu`.

The menu is exercised with a fake stdout and a scripted `_get_char()`, so no
real terminal, termios call or input is required.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import my_snaps.InlineMenu as inline
import pytest
from my_snaps.InlineMenu import Menu, Term


def fake_terminal_size(monkeypatch, cols=80, rows=24):
    """ Pin the menu's terminal size.

    Patch `InlineMenu.terminal_size()` rather than
    `inline.shutil.get_terminal_size`: `inline.shutil` *is* the `shutil`
    module, so patching it changes every caller in the process, including
    pytest's own terminal writer, which calls it as
    `shutil.get_terminal_size(fallback=...)` while reporting progress.
    """
    monkeypatch.setattr(inline, 'terminal_size',
                        lambda: os.terminal_size((cols, rows)))


def fake_alarm(monkeypatch):
    """ Replace the menu's `signal` binding with an alarm-free stub. """
    monkeypatch.setattr(inline, 'signal', SimpleNamespace(
        SIGALRM=1,
        ITIMER_REAL=2,
        SIG_IGN='IGN',
        signal=lambda *_a: None,
        setitimer=lambda *_a: None))


def make_menu(monkeypatch, prompts, default=None, title=''):
    fake_terminal_size(monkeypatch)
    monkeypatch.setattr(Menu, '_refresh', lambda _self, clear=True: None)
    return Menu(prompts, default, title)


# --- escape sequences ------------------------------------------------------

def test_terminal_size_always_passes_a_fallback(monkeypatch):
    seen = {}

    def fake_get_terminal_size(*_args, fallback=None, **_kwargs):
        seen['fallback'] = fallback
        return os.terminal_size(fallback)

    monkeypatch.setattr(inline, 'shutil', SimpleNamespace(
        get_terminal_size=fake_get_terminal_size))

    assert inline.terminal_size() == os.terminal_size((80, 24))
    assert seen['fallback'] == inline.DEFAULT_TERMINAL_SIZE


def test_term_sequences():
    assert Term.erase_line() == '\x1b[2K'
    assert Term.bold() == '\x1b[1m'
    assert Term.reverse_video() == '\x1b[7m'
    assert Term.normal_video() == '\x1b[m'
    assert Term.pos_up(0) == ''
    assert Term.pos_down(0) == ''
    assert Term.pos_up(2) == '\x1b[2F'
    assert Term.pos_down(3) == '\x1b[3E'
    assert Term.col(5) == '\x1b[5G'
    assert Term.clear_screen() == '\x1b[H\x1b[2J\x1b[3J'


# --- prompt parsing --------------------------------------------------------

def test_parse_prompts_handles_strings_payloads_and_multiline_extras(capsys):
    menu = Menu.__new__(Menu)
    menu.prompts = {
        'a': 'One',
        'b': ('Two\nsecond line', 'payload'),
        'c': ('Three', 'one', 'two'),
        'd': ['Four', {'key': 'value'}],
    }
    menu.objects = {}
    menu.lines = []
    menu.keys = []

    menu._parse_prompts()

    assert menu.lines == ['a: One', 'b: Two', 'c: Three', 'd: Four']
    assert menu.keys == ['a', 'b', 'c', 'd']
    first = menu.objects['a']
    assert (first.prompt, first.extra, first.payload) == ('One', None, None)
    second = menu.objects['b']
    assert (second.prompt, second.extra, second.payload) == (
        'Two', 'second line', 'payload')
    third = menu.objects['c']
    assert third.payload == ('one', 'two')
    assert menu.objects['d'].payload == {'key': 'value'}
    assert [ns.next for ns in menu.objects.values()] == ['b', 'c', 'd', 'd']
    assert capsys.readouterr().out == ''


def test_parse_prompts_skips_a_duplicate_key(capsys):
    menu = Menu.__new__(Menu)
    menu.prompts = {'a': 'Duplicate', 'b': 'Kept'}
    menu.objects = {'a': SimpleNamespace()}
    menu.lines = []
    menu.keys = []

    menu._parse_prompts()

    assert 'WARNING: skip dup key (a)' in capsys.readouterr().out
    assert menu.lines == ['b: Kept']
    assert 'a' not in menu.keys


# --- construction and cursor movement --------------------------------------

@pytest.mark.parametrize('default,expected', [
    ('b', -1),
    (SimpleNamespace(next='a'), -2),
    ('missing', []),
])
def test_init_selects_the_requested_default(monkeypatch, default, expected):
    moves = []
    monkeypatch.setattr(Menu, '_move',
                        lambda _self, cnt: moves.append(cnt))

    menu = make_menu(monkeypatch, {'a': 'A', 'b': 'B'}, default=default,
                     title='Title')

    assert menu.title == 'Title'
    assert menu.cols == 80
    assert menu.rows == 24
    assert moves == ([expected] if expected else [])


def test_set_pos_str_moves_up_down_or_stays():
    menu = Menu.__new__(Menu)
    menu.lines = ['a', 'b', 'c']
    menu.pos = 2
    assert menu._set_pos_str(1) == '\x1b[1F'
    assert menu.pos == 1
    assert menu._set_pos_str(1) == ''
    assert menu._set_pos_str(3) == '\x1b[2E'
    assert menu.pos == 3


def test_get_line_str_marks_only_the_selected_row():
    menu = Menu.__new__(Menu)
    menu.lines = ['first', 'second']
    menu.selected = 0
    menu.cols = 80
    menu.max_line = 0
    menu.pos = 0

    ordinary = menu._get_line_str(1)
    menu.selected = 1
    selected = menu._get_line_str(1)

    assert '> ' in selected
    assert '\x1b[7m' in selected
    assert '> ' not in ordinary
    assert '\x1b[7m' not in ordinary
    assert '\x1b[2K' in ordinary


def test_move_clamps_at_the_edges_and_redraws(capsys):
    menu = Menu.__new__(Menu)
    menu.lines = ['a', 'b', 'c']
    menu.selected = 1
    menu.cols = 80
    menu.max_line = 0
    menu.pos = 0

    menu._move(1)
    assert menu.selected == 2
    assert capsys.readouterr().out

    menu._move(10)
    assert menu.selected == 2

    menu._move(-10)
    assert menu.selected == 0


def test_bottom_line_keeps_the_last_message():
    menu = Menu.__new__(Menu)
    menu.lines = ['a', 'b']
    menu.selected = 0
    menu.cols = 80
    menu.pos = 0
    menu.prev_bottom = menu.default_bottom = 'bottom'

    menu._bottom_line('error')

    assert menu.prev_bottom == 'error'

    menu._bottom_line()
    assert menu.prev_bottom == 'error'


def test_finish_draws_the_pick_line(capsys):
    menu = Menu.__new__(Menu)
    menu.lines = ['a: one', 'b: two']
    menu.selected = 1
    menu.cols = 80
    menu.pos = 0

    menu._finish()

    assert 'PICK' in capsys.readouterr().out


def test_restore_default_bottom_redraws_only_when_needed(monkeypatch):
    menu = Menu.__new__(Menu)
    menu.default_bottom = 'default'
    menu.prev_bottom = 'error'
    calls = []
    monkeypatch.setattr(menu, '_bottom_line', lambda text: calls.append(text))

    menu._restore_default_bottom()
    # The real `_bottom_line()` records the new message; mirror that here.
    menu.prev_bottom = 'default'
    menu._restore_default_bottom()

    assert calls == ['default']


# --- payload selection -----------------------------------------------------

def test_get_command_prefers_extra_then_string_payload_then_prompt():
    menu = Menu.__new__(Menu)

    assert menu.get_command(
        SimpleNamespace(extra='extra', payload='payload', prompt='prompt')
    ) == 'extra'
    assert menu.get_command(
        SimpleNamespace(extra=None, payload='payload', prompt='prompt')
    ) == 'payload'
    assert menu.get_command(
        SimpleNamespace(extra=None, payload=('one', 'two'), prompt='prompt')
    ) == 'prompt'


def test_prompt_returns_the_key(monkeypatch):
    menu = Menu.__new__(Menu)
    monkeypatch.setattr(menu, 'get_prompt_obj',
                        lambda: SimpleNamespace(key='q'))

    assert menu.prompt() == 'q'


def test_cursor_helpers():
    assert Menu.get_next_key('a') == 'b'
    assert Menu.arrow_delta('\x1b[B') == 1
    assert Menu.arrow_delta('nope') is None


# --- terminal mode and input ----------------------------------------------

def test_prompt_mode_saves_and_restores_termios(monkeypatch):
    calls = []
    monkeypatch.setattr(Menu, 'save_attrs', None)
    monkeypatch.setattr(Menu, '_fd', staticmethod(lambda: 3))
    monkeypatch.setattr(inline, 'termios', SimpleNamespace(
        TCSADRAIN='TCSADRAIN',
        tcgetattr=lambda fd: ['attrs', fd],
        tcsetattr=lambda *args: calls.append(('restore', args))))
    monkeypatch.setattr(inline, 'tty', SimpleNamespace(
        setcbreak=lambda fd: calls.append(('cbreak', fd))))

    Menu._prompt_mode(enable=True)
    assert Menu.save_attrs == ['attrs', 3]
    assert calls == [('cbreak', 3)]

    Menu._prompt_mode(enable=False)
    assert Menu.save_attrs is None
    assert calls[-1][0] == 'restore'

    # A second disable is a no-op when nothing was saved.
    Menu._prompt_mode(enable=False)


def test_get_char_returns_a_character(monkeypatch):
    menu = Menu.__new__(Menu)
    menu.cols, menu.rows = 80, 24
    fake_alarm(monkeypatch)
    fake_terminal_size(monkeypatch)
    monkeypatch.setattr(inline.sys.stdin, 'read', lambda _n: 'a')

    assert menu._get_char() == 'a'


def test_get_char_survives_a_timeout(monkeypatch):
    menu = Menu.__new__(Menu)
    menu.cols, menu.rows = 80, 24
    calls = {'count': 0}

    def read(_n):
        calls['count'] += 1
        if calls['count'] == 1:
            raise Menu.InputTimeout
        return 'b'

    fake_alarm(monkeypatch)
    fake_terminal_size(monkeypatch)
    monkeypatch.setattr(inline.sys.stdin, 'read', read)

    assert menu._get_char() == 'b'
    assert calls['count'] == 2


def test_get_char_redraws_after_a_resize(monkeypatch):
    menu = Menu.__new__(Menu)
    menu.cols, menu.rows = 80, 24
    redrawn = []
    monkeypatch.setattr(menu, '_bottom_line',
                        lambda: redrawn.append('bottom'))
    monkeypatch.setattr(menu, '_refresh',
                        lambda: redrawn.append('refresh'))
    fake_alarm(monkeypatch)
    fake_terminal_size(monkeypatch, cols=100, rows=30)
    monkeypatch.setattr(inline.sys.stdin, 'read', lambda _n: 'a')

    assert menu._get_char() == 'a'
    assert (menu.cols, menu.rows) == (100, 30)
    assert redrawn == ['bottom', 'refresh']


# --- the selection loop ----------------------------------------------------

def scripted(monkeypatch, menu, keys):
    chars = iter(keys)
    monkeypatch.setattr(menu, '_get_char', lambda: next(chars))
    monkeypatch.setattr(menu, '_prompt_mode', lambda enable=False: None)
    return menu


def test_get_prompt_obj_accepts_a_direct_key(monkeypatch):
    menu = scripted(monkeypatch, make_menu(monkeypatch, {'a': 'A', 'b': 'B'}),
                    ['a', '\r'])

    assert menu.get_prompt_obj().key == 'a'


def test_get_prompt_obj_accepts_an_arrow_sequence(monkeypatch):
    menu = scripted(monkeypatch, make_menu(monkeypatch, {'a': 'A', 'b': 'B'}),
                    ['\x1b', '[', 'B', '\r'])

    assert menu.get_prompt_obj().key == 'b'


def test_get_prompt_obj_reports_an_invalid_key_and_keeps_prompting(monkeypatch):
    menu = scripted(monkeypatch, make_menu(monkeypatch, {'a': 'A'}),
                    ['z', 'a', '\r'])
    messages = []
    monkeypatch.setattr(menu, '_bottom_line',
                        lambda text=None: messages.append(text))

    assert menu.get_prompt_obj().key == 'a'
    assert any(message and 'invalid' in message for message in messages)


def test_get_prompt_obj_requires_a_selection_before_enter(monkeypatch):
    menu = scripted(monkeypatch, make_menu(monkeypatch, {'a': 'A'}),
                    ['\r', 'a', '\r'])
    menu.selected = len(menu.lines)
    messages = []
    monkeypatch.setattr(menu, '_bottom_line',
                        lambda text=None: messages.append(text))

    assert menu.get_prompt_obj().key == 'a'
    assert 'invalid(no selection)' in messages[0]
