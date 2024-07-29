#!/usr/bin/env python3
""" Program to run """
# pylint: disable=invalid-name,broad-exception-caught
# pylint: disable=too-many-locals,too-many-arguments

import sys
import os
import glob
import subprocess
import re
import traceback
from types import SimpleNamespace
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
        self.mounted_ids = set()
        self.root_subvol = None
        self.mounted_subpaths = set()
        self.slash_mnt = self.get_slash_mnt()
        self.is_bootable = True # until proved otherwise

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
                self.check_bootable()
                sys.exit(0)
            if choice.key == 'y':
                self.dry_run = not self.dry_run
                continue
            cmd = menu.get_command(choice)
            if not cmd.startswith('#'):
                if not force and self.dry_run:
                    os.system(f'echo WOULD + {precmd!r} {cmd!r}')
                elif 'reboot' not in cmd or self.check_bootable():
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
        # /dev/mmcblk1p2 / btrfs rw,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvolid=318,subvol=/eos@root 0 0
        for line in lines:
            wds = re.split(r'\s+', line)
            if len(wds) < 4:
                continue
            device, mount, fstype, opts_str = wds[0], wds[1], wds[2], wds[3]
            if mount == '/mnt':
                rv = SimpleNamespace(device=device, fstype=fstype)
            if fstype != 'btrfs':
                continue
            opts, subvolid, subvol = opts_str.split(','), '', ''
            for opt in opts:
                wds = opt.split('=', maxsplit=1)
                try:
                    if wds[0] == 'subvolid':
                        subvolid = int(wds[1])
                    if wds[0] == 'subvol':
                        subvol = wds[1]
                except Exception:
                    pass
            if subvolid:
                self.mounted_ids.add(subvolid)
                if mount == '/':
                    self.root_subvol = subvol[1:] # strip leading /

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
    
    def check_bootable(self):
        """ Check whether /usr/lib/modules, /efi/, and /.efi-back/ agree"""
        def get_list(folder):
            try:
                rv = glob.glob(f'{folder}/*')
                rv = [os.path.basename(item) for item in rv]
                return rv
            except Exception:
                return []
        def pr(my_list):
            return ' '.join(my_list)
        efis = get_list('/efi/????????????????????????????????')
        if not efis or not self.root_subvol:
            return True
        root = os.path.join('/mnt', self.root_subvol)
        mods = get_list(f'{root}/usr/lib/modules')
        backs = get_list(f'{root}/.efi-back/????????????????????????????????')
        overlap = set(mods) & set(efis) & set(backs)
        if not overlap and self.is_bootable:
            print('NOT bootable (no overlap)')
            print(f'  modules: {pr(mods)}\n  /efi: {pr(efis)}\n'
                  + f'  /efi-back: {pr(backs)}\n  root: {root}\n  overlap: {pr(overlap)}')
        self.is_bootable = bool(overlap)
        return self.is_bootable


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
        self.mounted_subpaths = set()
        for line in lines:
            mat = re.search(r"\bID\s+(\d+)\b.*\bpath\s+(\S+)", line)
            if not mat:
                continue
            subid = int(mat.group(1))
            subpath = mat.group(2)
            if subid in self.mounted_ids:
                self.mounted_subpaths.add(subpath)
            basename = os.path.basename(subpath)
            parent = os.path.dirname(subpath)
            if not parent and not basename.endswith('@snapshot'):
                if '.' in basename and basename.endswith('=Reverted'):
                    reverts[basename.split('.', 1)[0]] = subpath
                elif not '.' in basename:
                    subnames.add(basename)
                elif basename.endswith('ToDel'):
                    if subpath not in self.mounted_subpaths:
                        cmd = f'btrfs sub del "{subpath}"'
                        os.system(f'set -x; {cmd}')
                    continue
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

        def sync_cmd(subvol, whence):
            sync, efi_back = '', os.path.join(whence, '.efi-back')
            if os.path.isdir(efi_back) and subvol == self.root_subvol:
                sync = f'rsync -a -del -H "{efi_back}/" "/efi/" && '
            return sync

        # pylint: disable=too-many-branches
        os.chdir('/mnt')
        subs = self.get_state()
        key, cmds = 'a', {}

        for subvol, ns in subs.items():
            lead = f'{subvol}'
            if ns.revert:
                run = sync_cmd(subvol, ns.revert)
                if subvol in self.mounted_subpaths:
                    run += f' mv "{subvol}" "{subvol}.ToDel" && '
                else:
                    run += f' btrfs sub del "{subvol}" && '
                run += f'mv "{ns.revert}" "{subvol}"'
                cmds[key] = [f'{lead}: revert {ns.revert}', run]
                key, lead = advance(key, lead)
                if ns.revert not in self.mounted_subpaths:
                    cmds[key] = [
                        f'{lead}: del {ns.revert}',
                        f'btrfs sub del "{ns.revert}"',
                    ]
                    key, lead = advance(key, lead)
                prep = f'btrfs sub del "{subvol}" && '
            else:
                prep = f'mv "{subvol}" "{subvol}.{timestamp_str()}=Reverted" && '

            for snap in ns.snaps:
                snap_base = os.path.basename(snap)
                sync = sync_cmd(subvol, snap)

                cmds[key] = [
                    f'{lead}: restore {snap_base} {ago_whence(snap_base)}', # prompt
                    f'{prep}{sync}btrfs sub snap "{snap}" "{subvol}"', # actual cmd
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

def rerun_module_as_root(module_name):
    """ rerun using the module name """
    if os.geteuid() != 0: # Re-run the script with sudo
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        vp = ['sudo', sys.executable, '-m', module_name] + sys.argv[1:]
        os.execvp('sudo', vp)

def run():
    """Wrap main in try/except."""
    try:
        rerun_module_as_root('my_snaps.my_restore')
        BtrfsRestore().main()
    except KeyboardInterrupt:
        pass
    except Exception as exce:
        print("exception:", str(exce))
        print(traceback.format_exc())


if __name__ == '__main__':
    run()
