#!/usr/bin/env python3
""" A simple menu tool w/o the full curses ado.
Create the menu by passing in:
- an optional 'default' key where the menu starts
    - if default is an object with attribute 'next', that will be use
- an optional 'title'
- 'prompts' which is a dict of the form {key: prompt, ...} where:
    - 'key' is one character (typically abc...012..ABC...)
        - often 'x' is reserved for exiting the menu if looped
    - each 'prompt' may be:
        - a single line string
        - a multiple line string
            - the first line is the actual prompt
            - the remainder is called extra
        - list or tuple of values:
            - the first element is a single or multiple line string (as above)
            - if no more elements, 'payload' is None
            - if one more element, 'payload' is prompt[1]
            - if more than one element, 'payload' is prompt[1:]

- Menu.prompt() just returns the key
- Menu.get_prompt_obj() return a SimpleNamespace with members:
    - key: the key selected
    - prompt: the prompt shown
    - extra: described above (None of only single line given)
    - payload: described above when prompt is a list or tuple
    - next: the next key
"""
# pylint: disable=invalid-name,broad-except,too-many-instance-attributes
# pylint: disable=too-few-public-methods

import sys
import termios
import tty
import shutil
import signal
from types import SimpleNamespace

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
    def bold(): return f'{Term.esc}[1m'
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
    def _prompt_mode(enable=False):
        """ Used to start / complete prompting the user so that we can read
            one character at a time, etc.
        """
        if enable:
            if not Menu.save_attrs:
                Menu.save_attrs = termios.tcgetattr(Menu.fd)
                tty.setcbreak(Menu.fd)
        else:
            if Menu.save_attrs:
                termios.tcsetattr(Menu.fd, termios.TCSADRAIN, Menu.save_attrs)
                Menu.save_attrs = None

    def __init__(self, prompts, default=None, title=''):
        self.prompts = prompts
        self.lines, self.keys = [], []
        self.objects = {}
        self.selected = len(prompts)
        self.title = title
        self.cols, self.rows = shutil.get_terminal_size()
        self.max_line = 0
        # time.sleep(10)
        self.pos = 0
        self._parse_prompts()
        self.input = ''
        self.default_bottom = 'Use Up/Down/key to highlight and Enter to select'
        self.prev_bottom = self.default_bottom
        self._refresh(clear=False)
        self.pos = len(self.lines)
        if hasattr(default, 'next'):
            default = default.next
        default = default if default else self.keys[0]
        if default in self.prompts:
            idx = list(self.keys).index(default)
            self._move(idx - self.selected)

    def _parse_prompts(self):
        for key, prompt in self.prompts.items():
            payload, extra = None, None
            if key in self.objects:
                print(f'WARNING: skip dup key ({key})')
                continue
            if isinstance(prompt, (tuple, list)):
                prompt, payload = prompt[0], prompt[1:]
                payload = (None if not payload else
                           payload[0] if len(payload) == 1 else payload)
            lines = prompt.splitlines()
            if len(lines) > 1:
                prompt, extra = lines[0], '\n'.join(lines[1:])
            prompt = prompt.rstrip()
            self.lines.append(f'{key}: {prompt}')
            self.keys.append(key)
            self.objects[key] = SimpleNamespace(key=key, prompt=prompt,
                                   extra=extra, payload=payload, next=None)
        for idx, ns in enumerate(self.objects.values()):
            ns.next = self.keys[ min(idx+1, len(self.keys)-1) ]

    def _refresh(self, clear=True):
        """ Draw/redraw the menu optionally clearing the screen.
            Do this a first and after screen resizes.
        """
        self.pos = len(self.prompts)
        if clear:
            print(Term.clear_screen(), end='', flush=True)
        print(f'\n\n     {Term.bold()}<<<< {self.title} >>>>'
              + Term.normal_video() + '\n'*len(self.prompts))
        for idx in range(0, len(self.lines)+1):
            print(self._get_line_str(idx), end='', flush=True)
        self._bottom_line()
        print(self._set_pos_str(self.selected), end='', flush=True)

    def _set_pos_str(self, idx):
        """ Goto the given line from where we are at."""
        idx = 0 if idx < 0 else len(self.lines) if idx > len(self.lines) else idx
        if idx == self.pos:
            return ''
        if idx < self.pos:
            self.pos, cnt = idx, self.pos - idx
            return Term.pos_up(cnt)
        self.pos, cnt = idx, idx - self.pos
        return Term.pos_down(cnt)

    def _get_line_str(self, idx):
        """ Returns the string to write for the given line of the menu.
            'where' overrides the line to put it on.
        """
        idx = 0 if idx < 0 else len(self.lines) if idx > len(self.lines) else idx
        action, pre, on, off = '', ' ', '', ''
        if idx == self.selected and idx < len(self.lines):
            pre, on, off = '>',Term.reverse_video(), Term.normal_video()
        if idx < len(self.lines):
            self.max_line = max(2+len(self.lines[idx]), self.max_line)
            action += self._set_pos_str(idx)
            action += Term.erase_line()
            line = self.lines[idx][:self.cols-2]
            action += f'{pre} {on}{line}{off}'
            action += Term.col(0)
        return action

    def _move(self, cnt):
        """ Move the cursor by the cnt lines up (negative) or down (positive).
            If actually moving, then the old cursor will be un-highlighted
            and the new cursor is highlighted.
        """
        action = ''
        idx = self.selected + cnt
        idx = 0 if idx < 0 else len(self.lines)-1 if idx >= len(self.lines) else idx
        if idx != self.selected:
            was_selected, self.selected = self.selected, idx
            action += self._get_line_str(was_selected)
            action += self._get_line_str(self.selected)
            action += self._set_pos_str(self.selected)
            print(action, end='', flush=True)

    def _get_char(self):
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
                # self._bottom_line(f'resize: rows={self.rows}, cols={self.cols}')
                self._bottom_line()
                self._refresh()
            if rv is not None:
                return rv

    def _bottom_line(self, string=None):
        """ Draw/redraw the bottom line possibly with an error message """
        self.prev_bottom = string if string else self.prev_bottom
        action = self._set_pos_str(len(self.lines))
        action += Term.erase_line()
        bottom = f':::: {self.prev_bottom} ::::'
        action += bottom[:self.cols-1]
        action += self._set_pos_str(self.selected)
        action += Term.col(0)
        print(action, end='', flush=True)

    def _finish(self, string=''):
        """ Complete the menu selection by moving the cursor after the
            menu and printing any given message.
        """
#       cnt = 1 + len(self.lines) - self.selected
#       action = Term.pos_down(cnt) + string
#       print(action, flush=True)
#       for _ in range(cnt-1, -1):
#           action += Term.pos_up(1) + Term.erase_line()
        # action += '    ' + Term.reverse_video()
        # self.max_line = max(len(self.lines[self.selected]), self.max_line)
        # action += self.lines[self.selected][:self.cols]
#       print(action, 'RUN\n', flush=True)
        for idx in range(len(self.lines)):
            print(self._set_pos_str(idx) + Term.erase_line(), end='')
        print(Term.pos_down(1) + Term.erase_line(), end='', flush=True)
        action = Term.pos_up(1+len(self.lines)) + Term.erase_line()
        action += Term.reverse_video() + 'PICK' + Term.normal_video() + ': '
        action += self.lines[self.selected][:self.cols-6]
        print(action + Term.pos_down(3), end='', flush=True)

    def _restore_default_bottom(self):
        """ After the user does something right, clear the error message. """
        if self.prev_bottom != self.default_bottom:
            self._bottom_line(self.default_bottom)

    @staticmethod
    def get_next_key(key):
        """ Returns the next ASCII char after the given;
            e.g., 'b' = get_next_key('a')
        """
        # old_int = ord(key[0])
        # next_int = old_int + 1
        # next_chr = chr(next_int)
        # next_str = str(next_chr)
        # return next_str
        return str(chr(1+ord(key[0])))
    def get_command(self, obj):
        """ Return the 'command' from the returned Namespace which
            by convention is the 'extra', the 'paload' if a string,
            or the prompt ... in that priority order
        """
        if obj.extra:
            return obj.extra
        if isinstance(obj.payload, str):
            return obj.payload
        return obj.prompt

    def prompt(self):
        """ The simplified external entry point which prompts the user
        to select and choose a menu entry and returns the key  """
        return self.get_prompt_obj().key

    def get_prompt_obj(self):
        """ The external entry point which prompts the user
        to select and choose a menu entry an returns a namespace  """
        while True:
            try:
                self._prompt_mode(enable=True)
                ans = ''
                while True:
                    key = self._get_char()
                    ans += key
                    if key.isalnum() or key in ('\r', '\n', ' '):
                        break
                if ans in ('\033[A', '\033[C'):
                    self._restore_default_bottom()
                    self._move(-1)
                elif ans in ('\033[B', '\033[C'):
                    self._restore_default_bottom()
                    self._move(1)
                elif ans in ('\r', '\n', ' '):
                    if self.selected < len(self.lines):
                        self._restore_default_bottom()
                        picked = list(self.prompts.keys())[self.selected]
                        self._prompt_mode(enable=False)
                        # self.finish(f'\n\nrunning {repr(picked)}')
                        self._finish()
                        return self.objects[picked]
                    self._bottom_line('invalid(no selection); pick again')
                elif ans in self.prompts:
                    self._restore_default_bottom()
                    idx = list(self.prompts.keys()).index(ans)
                    self._move(idx - self.selected)
                else:
                    self._bottom_line(f'invalid({repr(ans)}); pick again')
            finally:
                self._prompt_mode(enable=False)

def runner(_): # def runner(argv):
    """ TBD """
    
    def simple_menu():
        default = 'A'
        while True:
            opts = {
                'q': 'Quit',
                'A': 'Set root password [suggested: "pw"]',
                'B': 'Set current user password [suggested: "pw"]',
                'u': 'Update Linux -- run after ChromeOS update (at least)',
                'r': 'Refresh Icons -- fix cases of icons becoming lost',
            }
            menu = Menu(opts, default=default, title='Simple Menu')
            key = menu.prompt()
            line = opts[key]
            print(f'\n===> To run: {line}\n')
            if 'Quit' in line:
                return 0
            default = menu.objects[key].next

    def multiline_menu():
        picked = 'A'
        while True:
            opts = {
                'A': 'Set root password [suggested: "pw"]\n'
                     'sudo passwd',
                'B': 'Set current user password [suggested: "pw"]\n'
                     'passwd',
                'u': 'Update Linux -- run after ChromeOS update (at least)\n'
                     'sudo pacman -Syu',
                'r': 'Refresh Icons -- fix cases of icons becoming lost\n'
                     'fix passwords somehow',
                'x': 'exit\n'
                     '# return from menu loop',
            }
            menu = Menu(opts, picked, title='MultiLine Menu')
            picked = menu.get_prompt_obj()
            print(f'\n===> To run: {picked.extra}\n')
            if 'exit' in picked.prompt:
                return 0

    def mixed_menu():
        picked = None
        while True:
            opts = {
                'A': 'Set root password [suggested: "pw"]\n'
                     'sudo passwd',
                'B': 'Set current user password [suggested: "pw"]\n'
                     'passwd',
                'u': ('Update Linux -- run after ChromeOS update (at least)\n'
                     'sudo pacman -Syu', {'key1': 'val1', 'key2': 'val2'}),
                'r': ('Refresh Icons -- fix cases of icons becoming lost',
                     {'Key1': 'Val1', 'Key2': 'Val2'}),
                'q': 'Quit\n',
            }
            menu = Menu(opts, picked, title='Mixed Menu')
            picked = menu.get_prompt_obj()
            print(f'\n===> returned: {vars(picked)}')
            if 'Quit' in picked.prompt:
                return 0
    
    simple_menu()
    multiline_menu()
    mixed_menu()

if __name__ == '__main__':
    runner(sys.argv)