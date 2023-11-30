#!/usr/bin/env python3
"""TBD"""
# pylint: disable=invalid-name,broad-except,too-many-instance-attributes

import sys
import termios
import tty
import shutil
import signal

####################################################################################

class Term:
    """ Escape sequences; e.g., see:
     - https://en.wikipedia.org/wiki/ANSI_escape_code
     - https://gist.github.com/fnky/458719343aabd01cfb17a3a4f7296797#file-ansi-md
    """
    esc = '\x1B'
    # pylint: disable=missing-function-docstring,multiple-statements
    @staticmethod
    def erase_line(): return f'{Term.esc}[2K'
    @staticmethod
    def reverse_video(): return f'{Term.esc}[7m'
    @staticmethod
    def normal_video(): return f'{Term.esc}[m'
    @staticmethod
    def pos_up(cnt): return f'{Term.esc}[{cnt}F' if cnt > 0 else ''
    @staticmethod
    def pos_down(cnt): return f'{Term.esc}[{cnt}E' if cnt > 0 else ''
    @staticmethod
    def col(pos): return f'{Term.esc}[{pos}G'
    @staticmethod
    def clear_screen(): return f'{Term.esc}[H{Term.esc}[2J{Term.esc}[3J'

####################################################################################

class Menu:
    """ A simple menu system """
    fd = sys.stdin.fileno()
    save_attrs = None
    class InputTimeout(Exception):
        """ Raised for obvious reasons. """

    @staticmethod
    def prompt_mode(enable=False):
        """ TBD """
        if enable:
            if not Menu.save_attrs:
                Menu.save_attrs = termios.tcgetattr(Menu.fd)
                tty.setcbreak(Menu.fd)
        else:
            if Menu.save_attrs:
                termios.tcsetattr(Menu.fd, termios.TCSADRAIN, Menu.save_attrs)
                Menu.save_attrs = None

    def __init__(self, prompts, default=None):
        self.prompts = prompts
        self.lines = []
        self.selected = len(prompts)
        self.pos = 0
        self.cols, self.rows = shutil.get_terminal_size()
        self.max_line = 0
        print('\n'*len(prompts))
        # time.sleep(10)
        self.pos = len(prompts)
        for idx, (key, prompt) in enumerate(prompts.items()):
            self.lines.append(f'{key}: {prompt}')
            action = self.draw_line_str(idx)
            print(action, end='', flush=True) # 1st time with newline to advance pos
            # print(action) # 1st time with newline to advance pos
        self.bottom_line(f'Highlight w Up/Down/key and Enter{Term.col(0)}')
        self.input = ''
        self.bottom = ''
        if default in self.prompts:
            idx = list(self.prompts.keys()).index(default)
            self.move(idx - self.selected)

    def refresh(self):
        """ TBD """
        print(Term.clear_screen(), end='', flush=True)
        for idx in range(0, len(self.lines)+1):
            print(self.draw_line_str(idx), end='', flush=True)
        self.bottom_line(self.bottom, do_prefix=False)
        print(self.set_pos_str(self.selected), end='', flush=True)

    def set_pos_str(self, idx):
        """ Goto the given line from where we are at."""
        idx = 0 if idx < 0 else len(self.lines) if idx > len(self.lines) else idx
        if idx == self.pos:
            return ''
        if idx < self.pos:
            self.pos, cnt = idx, self.pos - idx
            return Term.pos_up(cnt)
        self.pos, cnt = idx, idx - self.pos
        return Term.pos_down(cnt)

    def draw_line_str(self, idx):
        """Draw the menu or a subset"""
        idx = 0 if idx < 0 else len(self.lines) if idx > len(self.lines) else idx
        action, pre, on, off = '', ' ', '', ''
        if idx == self.selected and idx < len(self.lines):
            pre, on, off = '>',Term.reverse_video(), Term.normal_video()
        if idx < len(self.lines):
            self.max_line = max(2+len(self.lines[idx]), self.max_line)
            action += self.set_pos_str(idx)
            action += Term.erase_line()
            line = self.lines[idx][:self.cols-2]
            action += f'{pre} {on}{line}{off}'
            action += Term.col(0)
        return action

    def move(self, cnt):
        """ TBD """
        action = ''
        idx = self.selected + cnt
        idx = 0 if idx < 0 else len(self.lines) if idx > len(self.lines) else idx
        if idx != self.selected:
            was_selected, self.selected = self.selected, idx
            action += self.draw_line_str(was_selected)
            action += self.draw_line_str(self.selected)
            action += self.set_pos_str(self.selected)
            print(action, end='', flush=True)

    def get_char(self):
        """ Get one char from stdin """
        def init_timeout_handling():
            # `SIGWINCH` is send on terminal resizes
            def handle_timeout(*ignore):
                raise Menu.InputTimeout
            signal.signal(signal.SIGALRM, handle_timeout)
            signal.setitimer(signal.ITIMER_REAL, 0.25, 0.25)

        def reset_timeout_handling() -> None:
            signal.setitimer(signal.ITIMER_REAL, 0, 0)
            signal.signal(signal.SIGALRM, signal.SIG_IGN)

        rv = None
        init_timeout_handling()
        while True:
            try:
                init_timeout_handling()
                rv = sys.stdin.read(1)
                reset_timeout_handling()
            except Menu.InputTimeout:
                reset_timeout_handling()
            except Exception as caught:
                reset_timeout_handling()
                raise caught

            reset_timeout_handling()
            cols, rows = shutil.get_terminal_size()
            if self.cols != cols or self.rows != rows:
                self.cols, self.rows = cols, rows
                self.bottom_line(f'resize: rows={self.rows}, cols={self.cols}')
                self.refresh()
            if rv is not None:
                return rv

    def bottom_line(self, string, do_prefix=True):
        """ TBD """
        prefix = ''
        if do_prefix:
            prefix = '::::::'
        action = self.set_pos_str(len(self.lines))
        action += Term.erase_line()
        self.bottom = f'{prefix} {string}'
        action += self.bottom[:self.cols-1]
        action += self.set_pos_str(self.selected)
        action += Term.col(0)
        print(action, end='', flush=True)

    def finish(self, string):
        """ TBD """
        cnt = 1 + len(self.lines) - self.selected
        action = Term.pos_down(cnt)
        action += f'{string}'
        print(action, flush=True)

    def prompt(self):
        """ TBD """
        while True:
            try:
                self.prompt_mode(enable=True)
                ans = ''
                while True:
                    key = self.get_char()
                    ans += key
                    if key.isalnum() or key in ('\r', '\n', ' '):
                        break
                if ans in ('\033[A', '\033[C'):
                    self.move(-1)
                elif ans in ('\033[B', '\033[C'):
                    self.move(1)
                elif ans in ('\r', '\n', ' '):
                    if self.selected < len(self.lines):
                        picked = list(self.prompts.keys())[self.selected]
                        self.prompt_mode(enable=False)
                        # self.finish(f'\n\nrunning {repr(picked)}')
                        self.finish('')
                        return picked
                    self.bottom_line('invalid(no selection); pick again')
                elif ans in self.prompts:
                    idx = list(self.prompts.keys()).index(ans)
                    self.move(idx - self.selected)
                else:
                    self.bottom_line(f'invalid({repr(ans)}); pick again')
            finally:
                self.prompt_mode(enable=False)

def runner(_): # def runner(argv):
    """ TBD """
    while True:
        opts = {
            'q': 'Quit',
            'A': 'Set root password [suggested: "pw"]',
            'B': 'Set current user password [suggested: "pw"]',
            'u': 'Update Linux -- run after ChromeOS update (at least)',
            'r': 'Refresh Icons -- fix cases of icons becoming lost',
        }
        menu = Menu(opts, 'u')
        picked = opts[menu.prompt()]
        print(f'\n===> To run: {picked}\n')
        if 'Quit' in picked:
            sys.exit(0)
