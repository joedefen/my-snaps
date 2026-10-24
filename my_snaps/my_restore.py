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
from my_snaps.MyUtils import (timestamp_str, ago_whence,
                              reexec_as_root, version_string)

#: `btrfs filesystem show` prints either `Label: 'name'` or `Label: none`;
#: tolerate both (and an unquoted name) instead of assuming a quoted label.
LABEL_RE = re.compile(r"^\s*Label:\s+(?:'([^']*)'|(\S+))")
#: `path /dev/sda1` appears in the per-device lines of each filesystem block.
PATH_RE = re.compile(r"\bpath\s+(/dev/\S+)")

#: The single mount point this tool operates on. Every subvolume it renames
#: or deletes is relative to this directory, so it must be a real BTRFS mount.
MNT = '/mnt'


def iter_mounts():
    """ Yield (device, mountpoint, fstype, options) from /proc/mounts. """
    try:
        with open('/proc/mounts', encoding='utf-8') as fh:
            lines = fh.readlines()
    except OSError as exce:
        print(f'ERROR: cannot read /proc/mounts: {exce}', file=sys.stderr)
        sys.exit(1)
    for line in lines:
        wds = re.split(r'\s+', line.strip())
        if len(wds) >= 4:
            yield wds[0], wds[1], wds[2], wds[3]


def same_device(left, right):
    """ Compare two device names, resolving /dev/... symlinks when possible. """
    if left == right:
        return True
    if left.startswith('/dev/') and right.startswith('/dev/'):
        return os.path.realpath(left) == os.path.realpath(right)
    return False


def parse_filesystems(show_output):
    """ Parse `btrfs filesystem show` into label/path entries.

    Unlabelled filesystems print `Label: none`; their label becomes '' so
    every path stays paired with its own label instead of inheriting the
    previous filesystem's label.
    """
    rv, label = [], ''
    for line in show_output.splitlines():
        mat = LABEL_RE.match(line)
        if mat:
            quoted, bare = mat.group(1), mat.group(2)
            label = quoted if quoted is not None else bare
            if label == 'none':
                label = ''
            continue
        mat = PATH_RE.search(line)
        if mat:
            rv.append(SimpleNamespace(label=label, path=mat.group(1)))
    return rv

class BtrfsRestore:
    """ TBD """
    def __init__(self, dry_run=False):
        # Escalation lives in run(), after argument parsing; constructing the
        # object is pure Python state and must not exec sudo (tests and the
        # dry-run path rely on that).
        self.dry_run = dry_run
        self.filesystems = []
        self.selected_fs = None
        self.last_key = None
        self.mounted_ids = set()
        self.root_subvol = None
        self.mounted_subpaths = set()
        self.todel_subpaths = []
        self.slash_mnt = self.get_slash_mnt()
        self.is_bootable = True # until proved otherwise

    def do_command(self, prompts, todo=None, precmd='', once=False):
        """ TBD"""
        prompts['x'] = 'EXIT'
        while True:
            prompts['y'] = ('toggle dry-run='
                            + ('ON' if self.dry_run else 'OFF'))
            menu = Menu(prompts, str(todo) if todo else None,
                        title='my-restore menu'
                          + (' DRY-RUN' if self.dry_run else ''))
            choice = menu.get_prompt_obj()
            self.last_key = choice.key
            if choice.key == 'x':
                self.check_bootable()
                sys.exit(0)
            if choice.key == 'y':
                self.dry_run = not self.dry_run
                continue
            cmd = menu.get_command(choice)
            if not cmd.startswith('#'):
                if self.dry_run:
                    os.system(f'echo WOULD + {precmd!r} {cmd!r}')
                elif 'reboot' not in cmd or self.check_bootable():
                    status = os.system(f'clear;set -x; {precmd} {cmd}')
                    if status != 0:
                        print(f'ERROR: command failed (wait status {status}):'
                              f' {cmd}', file=sys.stderr)
            todo = choice.next
            if once:
                return todo

    def get_slash_mnt(self):
        """ Describe what is mounted on /mnt and record live subvolume ids. """
        rv = SimpleNamespace(device='', fstype='')
        # /dev/mmcblk1p2 / btrfs rw,noatime,compress=zstd:3,ssd,discard=async,space_cache=v2,subvolid=318,subvol=/eos@root 0 0
        for device, mount, fstype, opts_str in iter_mounts():
            if mount == MNT:
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

    def mount_choices(self):
        """ Map menu keys to the commands that put a filesystem on /mnt.

        The unmount target is the mount point itself, never `/dev`: as root,
        `umount /dev` can tear down devtmpfs, and if it fails the `&&`
        short-circuits and leaves the chosen filesystem unmounted while
        `select_restores()` happily inspects whatever is on /mnt.
        """
        cmds, todo = {}, '-'
        mounted = self.slash_mnt.device
        for idx, fs in enumerate(self.filesystems):
            if fs.path == mounted:
                cmd = f'# KEEP {fs.path!r} mounted on {MNT}'
            else:
                prefix = f'umount {MNT} && ' if mounted else ''
                label = fs.label if fs.label else 'no-label'
                cmd = f'{prefix}mount {fs.path} {MNT} # {label}'
            cmds[str(idx)] = cmd
            todo = '0'
        return cmds, todo

    def select_mount(self):
        """ Choose which BTRFS filesystem gets mounted on /mnt """
        try:
            command_output = subprocess.check_output(
                ['btrfs', 'filesystem', 'show']).decode('utf-8', errors='replace')
        except (subprocess.CalledProcessError, OSError) as e:
            print(f'ERROR: cannot list BTRFS filesystems: {e}', file=sys.stderr)
            sys.exit(1)
        self.filesystems = parse_filesystems(command_output)
        if not self.filesystems:
            print('ERROR: no BTRFS filesystems found', file=sys.stderr)
            sys.exit(1)
        cmds, todo = self.mount_choices()
        valid_keys = set(cmds) # `do_command()` adds 'x' and 'y' to `cmds`
        # `do_command()` honors dry-run, so the mount may deliberately not
        # have happened; `mnt_problem()` verifies the result before anything
        # else is allowed to touch /mnt.
        self.do_command(cmds, todo, once=True)
        if self.last_key not in valid_keys:
            print(f'ERROR: unexpected selection {self.last_key!r}', file=sys.stderr)
            sys.exit(1)
        self.selected_fs = self.filesystems[int(self.last_key)]

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
        root = os.path.join(MNT, self.root_subvol)
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
        """ Create a dict of subvolumes that have a snapshots and/or a reverted tip

        This is strictly read-only. Pending `*ToDel` subvolumes are collected
        in `self.todel_subpaths` for `select_restores()` to offer as explicit
        menu actions; nothing is deleted here. Deleting at inspection time
        meant that `my-restore --dry-run` could still destroy data, and that
        merely starting the tool cleaned up subvolumes the user had not yet
        finished evaluating.
        """
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
        self.todel_subpaths = []
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
                    if (subpath not in self.mounted_subpaths
                            and subpath not in self.todel_subpaths):
                        self.todel_subpaths.append(subpath)
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

    def mnt_problem(self):
        """ Return why /mnt is unsafe to operate on, or '' when it is fine.

        `select_restores()` runs `btrfs sub` commands relative to /mnt. If
        the selected filesystem is not actually mounted there -- dry-run, a
        failed `mount`, or a later manual unmount -- /mnt is just a
        directory on the root filesystem and those commands would rename or
        delete subvolumes belonging to the wrong filesystem.
        """
        for device, mount, fstype, _ in iter_mounts():
            if mount != MNT:
                continue
            if fstype != 'btrfs':
                return f'{MNT} holds {fstype} ({device}), not BTRFS'
            if self.selected_fs and not same_device(device, self.selected_fs.path):
                return f'{MNT} holds {device}, not {self.selected_fs.path}'
            return ''
        return f'nothing is mounted on {MNT}'

    def efi_sync_cmd(self, subvol, whence):
        """ Mirror the snapshot's backup of the ESP into /efi.

        Deletion is the point: /efi must end up matching the restored root.
        The additive copy left behind by the mistyped `-del` -- which rsync
        parses as `-d -e l`, setting the remote shell to the nonexistent
        command `l` -- silently kept kernels and boot entries that the
        restored root no longer had.

        Both guards exist because `--delete` makes /efi authoritative: an
        empty backup would empty the ESP, and a /efi that is not the mounted
        ESP would delete files belonging to the running (or live) system.
        """
        efi_back = os.path.join(whence, '.efi-back')
        if subvol != self.root_subvol or not os.path.isdir(efi_back):
            return ''
        try:
            entries = os.listdir(efi_back)
        except OSError as exce:
            print(f'WARNING: cannot read {efi_back!r}: {exce}; '
                  'not mirroring /efi', file=sys.stderr)
            return ''
        if not entries:
            print(f'WARNING: {efi_back!r} is empty; not mirroring /efi',
                  file=sys.stderr)
            return ''
        if not os.path.ismount('/efi'):
            print('WARNING: /efi is not a mount point; not mirroring '
                  '(would delete the wrong tree)', file=sys.stderr)
            return ''
        return f'rsync -a -H --delete "{efi_back}/" "/efi/" && '

    def select_restores(self, todo='a'):
        """ TBD """
        def next_key(key):
            """ Next menu key, skipping the ones reserved by `do_command()`. """
            key = Menu.get_next_key(key)
            while key in ('x', 'y'):
                key = Menu.get_next_key(key)
            return key

        def advance(key, lead):
            return next_key(key), ' '*len(lead)

        def freed_name_cmd(subvol):
            """ Free `subvol`'s name so a new subvolume can take it.

            A subvolume that is the root of an active mount cannot be
            deleted -- the kernel answers EBUSY -- and its name cannot be
            reused while it exists. Because a mount follows the subvolume
            id and not its name, renaming a mounted subvolume is safe: the
            running system keeps reading the old extents until reboot, and
            the replaced tip stays recoverable as a `*ToDel` entry that
            `get_state()` offers for explicit deletion later.
            """
            if subvol in self.mounted_subpaths:
                aside = f'{subvol}.{timestamp_str()}ToDel'
                return f'mv "{subvol}" "{aside}" && '
            return f'btrfs sub del "{subvol}" && '

        # pylint: disable=too-many-branches
        problem = self.mnt_problem()
        if problem:
            print(f'ERROR: {problem}', file=sys.stderr)
            print(f'ERROR: refusing to touch subvolumes outside {MNT}',
                  file=sys.stderr)
            if self.dry_run:
                print(f'HINT: dry-run does not mount; mount the chosen '
                      f'filesystem on {MNT} to inspect it', file=sys.stderr)
            sys.exit(1)
        os.chdir(MNT)
        subs = self.get_state()
        key, cmds = 'a', {}

        for subvol, ns in subs.items():
            lead = f'{subvol}'
            if ns.revert:
                # The reverted tip is still present, so the *current*
                # subvolume is the copy made by the earlier revert. Freeing
                # its name is what makes `revert` and `restore` symmetric.
                prep = freed_name_cmd(subvol)
                run = self.efi_sync_cmd(subvol, ns.revert) + prep
                run += f'mv "{ns.revert}" "{subvol}"'
                cmds[key] = [f'{lead}: revert {ns.revert}', run]
                key, lead = advance(key, lead)
                if ns.revert not in self.mounted_subpaths:
                    cmds[key] = [
                        f'{lead}: del {ns.revert}',
                        f'btrfs sub del "{ns.revert}"',
                    ]
                    key, lead = advance(key, lead)
            else:
                prep = f'mv "{subvol}" "{subvol}.{timestamp_str()}=Reverted" && '

            for snap in ns.snaps:
                snap_base = os.path.basename(snap)
                # Mirror the ESP *before* `prep` renames the current
                # subvolume: an rsync failure must not leave the system with
                # `{subvol}` moved aside and no replacement created.
                sync = self.efi_sync_cmd(subvol, snap)

                cmds[key] = [
                    f'{lead}: restore {snap_base} {ago_whence(snap_base)}', # prompt
                    f'{sync}{prep}btrfs sub snap "{snap}" "{subvol}"', # actual cmd
                ]
                key, lead = advance(key, lead)
        # `*ToDel` subvolumes are leftovers of a revert that replaced a mounted
        # subvolume. They are only offered here, after the user has had a
        # chance to boot and test the restored system; `do_command()` honors
        # dry-run, so nothing is deleted until an entry is explicitly chosen.
        for subpath in self.todel_subpaths:
            cmds[key] = [f'del {subpath}', f'btrfs sub del "{subpath}"']
            key = next_key(key)
        cmds[key] = 'reboot now'
        return self.do_command(cmds, once=True, todo=todo)

    def main(self):
        """ The top-level function. """
        self.select_mount()
        os.system('clear')
        todo = 'a'
        while True:
            todo = self.select_restores(todo=todo)

def parse_args(argv=None):
    """ Parse the command line before anything asks for root.

    `--help`/`--version` and usage errors therefore never trigger sudo.  A
    dry-run is parsed here too so `my-restore -n` can run unprivileged; it
    only prints the commands it would execute.
    """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='version',
            version=f'%(prog)s {version_string()}')
    parser.add_argument('-n', '--dry-run', action="store_true",
            help='do NOT do anything')
    return parser.parse_args(argv)

def run(argv=None):
    """Wrap main in try/except."""
    try:
        opts = parse_args(argv)
        if not opts.dry_run:
            # A real restore renames/deletes subvolumes and may mount a
            # filesystem, so it needs root.  A dry-run changes nothing, so it
            # deliberately stays unprivileged (re-run it with sudo yourself
            # when merely reading the BTRFS state needs root).
            reexec_as_root('my_snaps.my_restore', argv)
        BtrfsRestore(dry_run=opts.dry_run).main()
    except KeyboardInterrupt:
        pass
    except Exception as exce:
        print("exception:", str(exce))
        print(traceback.format_exc())


if __name__ == '__main__':
    run()
