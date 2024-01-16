#!/usr/bin/env python3
""" Program to run """
# pylint: disable=invalid-name,broad-exception-caught
# pylint: disable=too-many-locals,too-many-arguments

import sys
import os
import subprocess
import re
import traceback
from types import SimpleNamespace
try:
    from InlineMenu import Menu
    from MyUtils import timestamp_str, ago_whence
except:
    from my_snaps.InlineMenu import Menu
    from my_snaps.MyUtils import timestamp_str, ago_whence

class BtrfsRestore:
    """ TBD """
    def __init__(self):
        if os.geteuid() != 0: # Re-run the script with sudo
            print('NOTE: restarting with "sudo"')
            os.execvp('sudo', ['sudo', sys.executable] + sys.argv)
        self.dry_run = False
        self.filesystems = []
        self.slash_mnt = self.get_slash_mnt()

    def do_command(self, prompts, todo=None, precmd='', once=False, force=False):
        """ TBD"""
        prompts['x'] = 'EXIT'
        while True:
            prompts['y'] = ('toggle dry-run='
                            + ('ON' if self.dry_run else 'OFF'))
            menu = Menu(prompts, str(todo) if todo else None,
                        title='my-restore menu'
                          + (' DRY-RUN' if self.dry_run else ''))
            choice = menu.get_prompt_obj()
            if choice.key == 'x':
                sys.exit(0)
            if choice.key == 'y':
                self.dry_run = not self.dry_run
                continue
            cmd = menu.get_command(choice)
            if not cmd.startswith('#'):
                if not force and self.dry_run:
                    os.system(f'echo WOULD + {precmd!r} {cmd!r}')
                else:
                    os.system(f'clear;set -x; {precmd} {cmd}')
            todo = choice.next
            if once:
                return todo

    def get_slash_mnt(self):
        """ TBD """
        def slurp_file(pathname):
            with open(pathname, "r", encoding='utf-8') as fh:
                return [line.strip() for line in fh]

        rv = SimpleNamespace(device='', fstype='')
        lines = slurp_file('/proc/mounts')
        for line in lines:
            wds = re.split(r'\s+', line)
            if len(wds) >= 4:
                device, mount, fstype, = wds[0], wds[1], wds[2]
                if mount == '/mnt':
                    rv = SimpleNamespace(device=device, fstype=fstype)
        return rv

    def select_mount(self):
        """ TBD """
        command_output = subprocess.check_output(['btrfs', 'filesystem', 'show']).decode('utf-8')
        lines = command_output.split('\n')
        self.filesystems = []
        for line in lines:
            mat = re.match(r"^Label:\s+('[^']*')", line)
            if mat:
                label = mat.group(1)
            mat = re.search(r"\bpath\s+(/dev/\S+)", line)
            if mat:
                path = mat.group(1)
                self.filesystems.append(SimpleNamespace(label=label, path=path))
        cmds, todo, mnt = {}, '-', self.slash_mnt
        for idx, fs in enumerate(self.filesystems):
            if fs.path == mnt.device:
                cmd = f'# KEEP {fs.path!r} mounted on /mnt'
            else:
                cmd = 'umount /dev && ' if mnt.device else ''
                cmd += f'mount {fs.path} /mnt # {fs.label}'
            cmds[str(idx)] = cmd
            todo = '0'
        todo = self.do_command(cmds, todo, once=True, force=True)

    def get_state(self):
        """ Create a dict of subvolumes that have a snapshots and/or a reverted tip """
        command_output = subprocess.check_output([
            'btrfs', 'sub', 'list', '.']).decode('utf-8')
        lines = command_output.split('\n')
        # ID 667 gen 216849 top level 5 path eos@my-opt
        # ID 699 gen 217076 top level 5 path eos@snapshots
        # ID 782 ... eos@snapshots/eos@root.2024-01-10-174732=Update
        # ID 791 ... eos@snapshots/eos@root.2024-01-13-084102=Daily
        subnames = set()
        subs = {}
        reverts = {}
        for line in lines:
            mat = re.search(r"\bpath\s+(\S+)", line)
            if not mat:
                continue
            subpath = mat.group(1)
            basename = os.path.basename(subpath)
            parent = os.path.dirname(subpath)
            if not parent and not basename.endswith('@snapshot'):
                if '.' in basename and basename.endswith('=Reverted'):
                    reverts[basename.split('.', 1)[0]] = subpath
                elif not '.' in basename:
                    subnames.add(basename)
                continue
            if parent.endswith('@snapshots'):
                wds = basename.split('.', 1)
                if len(wds) > 1:
                    subvol = wds[0]
                    sub_snaps = subs.get(subvol, [])
                    sub_snaps.append(subpath)
                    subs[subvol] = sub_snaps
        rv = {}
        for name in sorted(subnames):
            sub_snaps = subs.get(name, [])
            revert = reverts.get(name, None)
            if sub_snaps or revert:
                rv[name] = SimpleNamespace(
                    revert=revert, snaps=sorted(sub_snaps))

        # for name, ns in rv.items(): print(f'{name}: {ns}')
        return rv

    def select_restores(self, todo='a'):
        """ TBD """
        def advance(key, lead):
            return Menu.get_next_key(key), ' '*len(lead)

        # pylint: disable=too-many-branches
        os.chdir('/mnt')
        subs = self.get_state()
        key, cmds = 'a', {}

        for subvol, ns in subs.items():
            lead = f'{subvol}'
            if ns.revert:
                cmds[key] = [
                    f'{lead}: revert {ns.revert}',
                    f'btrfs sub del "{subvol}" && mv "{ns.revert}" "{subvol}"',
                ]
                key, lead = advance(key, lead)
                cmds[key] = [
                    f'{lead}: del {ns.revert}',
                    f'btrfs sub del "{ns.revert}"',
                ]
                key, lead = advance(key, lead)
                prep = f'btrfs sub del "{subvol}" &&'
            else:
                prep = f'mv "{subvol}" "{subvol}.{timestamp_str()}=Reverted" &&'

            for snap in ns.snaps:
                snap_base = os.path.basename(snap)
                cmds[key] = [
                    f'{lead}: restore {snap_base} {ago_whence(snap_base)}',
                    f'{prep} btrfs sub snap "{snap}" "{subvol}"',
                ]
                key, lead = advance(key, lead)
        cmds[key] = 'reboot now'
        return self.do_command(cmds, once=True, todo=todo)

    def main(self):
        """ The top-level function. """
        # pylint: disable=import-outside-toplevel
        import argparse
        parser = argparse.ArgumentParser()
        parser.add_argument('-n', '--dry-run', action="store_true",
                help='do NOT do anything')
        opts = parser.parse_args()
        self.dry_run = opts.dry_run
        self.select_mount()
        os.system('clear')
        todo = 'a'
        while True:
            todo = self.select_restores(todo=todo)


def run():
    """Wrap main in try/except."""
    try:
        BtrfsRestore().main()
    except KeyboardInterrupt:
        pass
    except Exception as exce:
        print("exception:", str(exce))
        print(traceback.format_exc())


if __name__ == '__main__':
    run()
