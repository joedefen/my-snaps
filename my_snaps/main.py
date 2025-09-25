#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTRFS snapshot tool implementing the simplest snapshot strategy; i.e.,
  * replace eldest snapshots with new ones before updates, and
  * generally keep those snapshots until the next update.
  
NOTE: for basic debugging, pass --DB and it will dump major objects w/o
starting the window.
"""
# pylint: disable=invalid-name,consider-using-with,too-few-public-methods
# pylint: disable=redefined-outer-name,consider-using-generator
# pylint: disable=too-many-locals,broad-exception-caught,multiple-statements
# pylint: disable=too-many-instance-attributes,import-outside-toplevel
# pylint: disable=global-statement,broad-exception-raised,too-many-branches
# pylint: disable=too-many-statements,too-many-arguments

import sys
import os
import re
import atexit
import traceback
import subprocess
import curses as cs
from types import SimpleNamespace
from my_snaps.PowerWindow import Window, OptionSpinner
from my_snaps.MyUtils import human, ago_whence, timestamp_str

##############################################################################

class BTRFS:
    """ TBD """
    def __init__(self, opts):
        self.tmp_dir = '/tmp/.btrfs/'
        self.temps = {} # keyed by device (e.g., /dev/nvme0n1p2)
        self.devs = {}
        self.snap_targets = [] # the potential subvols to snapshot
        self.label_set = set() # all labels of current snapshots
        self.snap_subvol = None
        self.DB = opts.DB
        self.add_limit = opts.add_snap_max
        self.label = None   # one label of current interest
        self.help_mode = False
        self.win = None
        self.rows = []
        self.dirty = True # TBD: need to refresh knowledge
        self.show_size = False # until we calc disk usage

        self.blkid_lines = [] # to avoid rerunning "blkid" on refresh
        self.mounts_lines = [] # to avoid rereading "/proc/mounts" on refresh

        atexit.register(self.umount_tmps)

        if opts.cron:
            self._install_cron_job(opts)
            sys.exit(0)

        self._refresh_if_dirty()

        if self.DB:
            self._get_disk_usage()
            sys.exit(0)


        if self.add_limit > 0:
            if opts.label:
                self.label = '=' + opts.label.replace('=', '')
            success = self._replace_eldest_snaps()
            print("OK" if success else "FAIL", f'add_snap_limit={self.add_limit}')
            if success and opts.print:
                self._refresh_if_dirty()
                self._print()
            sys.exit(0 if success else 1)

        if opts.print:
            self._print()
            sys.exit(0)

        self._start_window()

    def _print(self):
        for dev in self.devs.values():
            print(f'df: {dev.diskfree}')
        path_width = self.calc_path_width()
        mounts_width = self.calc_mounts_width()
        devs_width = self.calc_devs_width()
        for row in self.rows:
            wds = row.path.split('/.snapshots/', maxsplit=1)
            shown_path = f'  | {wds[1]}' if len(wds) > 1 else row.path
            mount_str = row.mount if row.mount else '' if row.subvol_ns.snap_of else '~'
            print(
                  f'{mount_str:>{mounts_width}}'
                  f' {row.dev:>{devs_width}}'
                  f' {shown_path:<{path_width}}')

    def _install_cron_job(self, opts):
        """ Add/replace an anacron job for scheduled snapshots """
        dirname = f'/etc/cron.{opts.cron}'
        if not os.path.isdir(dirname):
            print(f'ERROR: {dirname!r} does not exist')
            sys.exit(-1)
        filename = os.path.join(dirname, f'{opts.cron}-snaps')
        if opts.add_snap_max > 0:
            text = '#!/bin/sh\n'
            text += f'{sys.executable} {os.path.abspath(__file__)} -p -s{opts.add_snap_max}'
            text += f' -L{opts.label} >/tmp/.my-snaps-{opts.cron}.txt 2>&1\n'
            with open(filename, mode='w', encoding='utf-8') as f:
                f.write(text)
            os.chmod(filename, 0o755)
            print(f'OK: to {filename!r}, wrote:\n{text}')
        elif os.path.isfile(filename):
            os.remove(filename)

    def _refresh_if_dirty(self):
        if not self.dirty:
            return
        # save sizes for xfer to refresh (cuz expensive)
        sizes = {}
        for ns in self.subvol_iter():
            sizes[f'{ns.dev}{ns.path}'] = ns.size
        self._load_devs()  # learn all the BTRFS partitions
        self._mount_tmps()  # mount BTRFS partitions in /tmp/.btrfs
        self._determine_mount_points() # associated current mounts with subvols
        self.gather_snapshots()  # determine subvols that are snapshots
        # restore sizes after refresh
        for ns in self.subvol_iter():
            key = f'{ns.dev}{ns.path}'
            if key in sizes:
                ns.size = sizes[key]
        self.make_rows()
        self.dirty = False

    def subvol_iter(self, subvol_ns=None, top_down=True):
        """  subvolume iterator recursively top-down (or bottom-up)
         - by default, all the subvols, not just top-level ones
         - optionally, just one tree given a subvol
        """
        def children_iter(subvol_ns, top_down):
            if top_down:
                yield subvol_ns
            for ns in subvol_ns.children:
                yield from children_iter(ns, top_down)
            if not top_down:
                yield subvol_ns

        if subvol_ns:
            yield from children_iter(subvol_ns, top_down=top_down)
            return

        for dev_ns in self.devs.values():
            # pylint: disable=redefined-argument-from-local
            for subvol_ns in dev_ns.subvols:
                if subvol_ns.depth == 0:
                    yield from children_iter(subvol_ns, top_down=top_down)

    def _cur_snap_suffix(self):
        return '.' + timestamp_str() + ('' if self.label is None else self.label)

    def stop_curses(self):
        """Terminate curses if running."""
        if self.win:
            self.win.stop_curses()

    def _start_window(self):
        def do_key(key):
            nonlocal spin, win, self
            value = spin.do_key(key, win)
            if key in (ord('u'), ) and not self.help_mode:
                win.clear()
                win.set_pick_mode(False)
                self.refresh_info(body=' ... BE REALLY PATIENT (running "du") ...')
                win.render()
                self._get_disk_usage()
                win.set_pick_mode(True)
                self.refresh_info()

            elif key in (ord('r'), ) and not self.help_mode:
                self._replace_eldest_snaps()

            elif key in (ord('a'), ) and not self.help_mode:
                self._replace_eldest_snaps(just_add=True)

            elif key in (ord('s'), ) and not self.help_mode:
                self._create_snap()

            elif key in (ord('d'), ) and not self.help_mode:
                self._del_subvolume()

            elif key in (ord('x'), ) and not self.help_mode:
                self.stop_curses()
                print('\n   OK, QUITTING NOW\n')
                sys.exit(0)

            elif key in (cs.KEY_ENTER, 10) and self.help_mode:
                self.help_mode = False
                win.set_pick_mode(False)

            elif key == ord('n'):
                win.alert(title='Info', message=f'got: {value}')

            return value

        spin = OptionSpinner()
        spin.add_key('help_mode', '? - toggle help screen', vals=[False, True], obj=self)

        base_keys_we_handle=[cs.KEY_ENTER,
                10, ord('s'), ord('d'), ord('u'), ord('r'), ord('a'), ord('x')]

        win = self.win = Window(keys=set(list(spin.keys) + list(base_keys_we_handle)))

        for _ in range(100000000000):
            if self.help_mode:
                win.set_pick_mode(False)
                spin.show_help_nav_keys(win)
                spin.show_help_body(win)
                self.win.add_body('Action keys:', attr=cs.A_UNDERLINE)
                self.win.add_body(' d - delete highlighted item')
                self.win.add_body(' s - create snapshot for highlighted item')
                self.win.add_body(' u - compute "du" for all subvols (very slow)')
                self.win.add_body(' r - replace eldest snapshot of each subvol')
                self.win.add_body(' a - add snapshot o each subvol with snapshots')
                self.win.add_body(' x - exit')
            else:
                win.set_pick_mode(True)
                self.refresh_info()
            win.render()
            try:
                _ = do_key(win.prompt(seconds=300))
            except Exception as exce:
                win.stop_curses()
                print("exception:", str(exce))
                print(traceback.format_exc())
                sys.exit(15)
            win.clear()

    def _replace_eldest_snaps_of_subvol(self, subvol_ns, like_snaps,
                                        suffix=None, discard=1):
        """ For ONE subvolume having snapshots, remove those and create a new snapshot."""

        if not subvol_ns.snaps:
            return True

        while discard > 0 and like_snaps:
            if not self._del_subvolume(like_snaps[0], ans="y"):
                return False  # all must succeed
            subvol_ns.snaps.remove(like_snaps[0])
            like_snaps.pop(0)
            discard -= 1
        return self._create_snap(subvol_ns, suffix=suffix)


    def _replace_eldest_snaps(self, discard=1, just_add=False):
        """ TBD """
        suffix, success = None, None

        if self.add_limit > 0:
            suffix = self._cur_snap_suffix()
        else:
            while True:
                suffix = self.win.answer(
                    f'Set snap suffix OR clear; labels: {",".join(list(self.label_set))}',
                        seed=suffix if suffix else self._cur_snap_suffix())
                if suffix and (len(suffix) < 5 or suffix[:1] != '.'
                            or any(x in suffix[1:] for x in './')):
                    self.win.alert(
                        'answer must be "." PLUS 4 or more characters w/o any "." or "/"')
                else:
                    self.label = self._get_label(suffix)
                    break
        if not suffix:
            return success

        counts = []
        for subvol_ns in self.snap_targets:
            counts.append(len(subvol_ns.label_groups.get(self.label, [])))

        for subvol_ns in self.snap_targets:
            like_snaps = subvol_ns.label_groups.get(self.label, [])
            this_cnt = len(like_snaps)
            if just_add:
                discard = max(0, this_cnt-8+1)
            else:
                max_cnt = self.add_limit if self.add_limit else max(counts)
                discard = max(0, this_cnt-max_cnt+1)

            rv = self._replace_eldest_snaps_of_subvol(subvol_ns,
                             like_snaps, suffix=suffix, discard=discard)
            success = rv if success is None else rv if success else False
        self.label = None
        return success


    def _create_snap(self, subvol_ns=None, suffix=None):
        if not subvol_ns:
            subvol_ns = self.rows[self.win.pick_pos].subvol_ns
        dev_ns = self.devs[subvol_ns.dev]

        if subvol_ns.snap_of:
            self.win.alert('Sorry, cannot create snapshot of snapshot')
            return False

        if not subvol_ns.mount:
            self.win.alert('Sorry, cannot create snapshot of unmounted subvolume')
            return False

        if subvol_ns.mount == '/.snapshots':
            self.win.alert('Sorry, cannot create snapshot of snapshot subvolume')
            return False

        if not suffix:
            seed=self._cur_snap_suffix()
            suffix = self.win.answer(
                f'Set suffix for snap "{subvol_ns.path}" OR clear', seed=seed)
        if not suffix:
            return False

        snap_dir = f'{dev_ns.tmp_path}{self.snap_subvol.path}'
        snap_path = f'{snap_dir}{subvol_ns.path}{suffix}'

        cmd = f'btrfs sub snap -r {subvol_ns.mount} {snap_path}'
        out, err, code = self._slurp_command(cmd)
        if code:
            self.win.alert(f'FAILED({code}): {cmd}', message='\n'.join(out + err),
                           height=len(out)+len(err))
            return False
        self.dirty = True
        return True

    def _del_subvolume(self, subvol_ns=None, ans=""):
        if not subvol_ns:
            subvol_ns = self.rows[self.win.pick_pos].subvol_ns

        for ns in self.subvol_iter(subvol_ns, top_down=False):
            if ns.mount:
                self.win.alert(f'Sorry, cannot delete mounted subvol {ns.mount}')
                return False

        if not ans:
            ans = self.win.answer(f'Type "y" to Delete "{subvol_ns.path}"')
        if not ans.strip().lower().startswith('y'):
            return False

        dev_ns = self.devs[subvol_ns.dev]
        for ns in self.subvol_iter(subvol_ns, top_down=False):
            snap_path = f'{dev_ns.tmp_path}{ns.path}'
            cmd = f'btrfs sub del {snap_path}'
            out, err, code = self._slurp_command(cmd)
            if code:
                self.win.alert('FAILED: {cmd}', message='\n'.join(out + err),
                               height=len(out)+len(err))
                return False
            self.dirty = True
        return True

    def _slurp_command(self, command):
        if self.DB: print('DB: +', command)
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, shell=True)
        status = process.wait()
        output, err = process.communicate()
        output, err = output.decode('utf-8'), err.decode('utf-8')
        output, err = output.splitlines(keepends=False), err.splitlines(keepends=False)
        return (output, err, os.WEXITSTATUS(status))

    @staticmethod
    def _slurp_file(pathname):
        with open(pathname, "r", encoding='utf-8') as fh:
            return [line.strip() for line in fh]

    @staticmethod
    def dev_path(dev):
        """ Return the full path to a dev (aka device basename) """
        return f'/dev/{dev}'

    def _load_devs(self):
        """ Discover all the BTRS Devices """

        if not self.blkid_lines:
            self.blkid_lines, _, _ = self._slurp_command('blkid')
        self.devs = {}
        for line in self.blkid_lines:
            # /dev/nvme0n1p2: LABEL="btrfs-common"
            #   UUID="8f60fc2f-872d-4327-aff9-34c4c4cefde7"
            #   UUID_SUB="d7b0987a-1133-4844-a19b-c6c22350379a"
            #   BLOCK_SIZE="4096" TYPE="btrfs"
            #   PARTUUID="02b5122d-5229-c347-a351-142008b89149"

            matches = re.findall(r'(\w+)="([^"]+)"', line)
            ns = SimpleNamespace() # Create a dictionary to store the fields and values
            for match in matches:
                field, value = match[0].lower(), match[1]
                if field in ('type',):
                    setattr(ns, field, value)
            if not hasattr(ns, 'type'):
                continue
            ns.dev = os.path.basename(line.split(': ', maxsplit=1)[0])
            if ns.type in ('btrfs', ) and ns.dev:
                delattr(ns, 'type')
                rows, _, _ = self._slurp_command(f'df -h {self.dev_path(ns.dev)}')
                ns.diskfree = rows[1] if len(rows) >= 2 else ''
                self.devs[ns.dev] = ns
        if self.DB:
            print('DB: --->>> after load_devs()')
            for dev, ns in self.devs.items():
                print(f'DB: {dev}: {vars(ns)}')

    @staticmethod
    def init_subvol_ns(dev='', path='', ident=None, parent=None):
        """ Create a subvolume namespace"""
        return SimpleNamespace(dev=dev, path=path, size=None,
                   depth=0, mount='', snaps=[], label_groups={},
                    children=[], snap_label='', snap_of=None,
                    ident=ident, parent=parent, ago_str='')

    def _mount_tmps(self):
        """ mount each btrfs as needed """
        os.makedirs(self.tmp_dir, exist_ok=True)
        for dev, dev_ns in self.devs.items():
            tmp_mount_dir = os.path.join(self.tmp_dir, dev)
            if not os.path.ismount(tmp_mount_dir):
                os.makedirs(tmp_mount_dir, exist_ok=True)
                code = os.WEXITSTATUS(os.system(
                        f'set -x; mount {self.dev_path(dev_ns.dev)} {tmp_mount_dir}'))
                if code:
                    raise Exception(f'cannot mount {dev_ns.dev} {code=}')
            dev_ns.tmp_path = tmp_mount_dir
            lines, _, _ = self._slurp_command(f'btrfs sub list {tmp_mount_dir}')

            dev_ns.paths = {} # subvols keyed by relative path
            dev_ns.idents = {} # subvols keyed by ident
            dev_ns.subvols = []
            for line in lines:
                wds = line.split(maxsplit=8)
                if len(wds) < 9:
                    continue
                ident, parent, path = wds[1], wds[6], f'/{wds[8]}'
                child = BTRFS.init_subvol_ns(dev=dev,
                             path=path, ident=ident, parent=parent)
                if parent in dev_ns.idents:
                    parent_ns = dev_ns.idents[parent]
                    child.depth = parent_ns.depth+1
                    parent_ns.children.append(child)
                else: # top-levels are in the dev
                    dev_ns.subvols.append(child)
                dev_ns.idents[ident] = child
                dev_ns.paths[path] = child
                child.ago_str = ago_whence(child.path)

        if self.DB:
            print('DB: --->>> after mount_tmps()')
            for dev, dev_ns in self.devs.items():
                print(f'DB: {dev=}: keys={vars(dev_ns).keys()}')
            for subvol in self.subvol_iter():
                print(f'DB:   {"  "*subvol.depth}subvol: {vars(subvol)}')


    def umount_tmps(self):
        """ unmount each btrfs as needed """
        for ns in self.devs.values():
            os.system(f'set -x; umount {ns.tmp_path}')

    def _determine_mount_points(self):
        if not self.mounts_lines:
            self.mounts_lines = self._slurp_file('/proc/mounts')
        for line in self.mounts_lines:
            wds = re.split(r'\s+', line)
            if len(wds) < 4:
                continue

            mount, fstype, opts = wds[1], wds[2], wds[3]
            if mount == '/' and fstype != 'btrfs':
                assert False, f'root is not BTRFS ({mount}, {fstype})'
            if fstype != 'btrfs':
                continue

            match = re.search(r'subvolid=(\d+)', opts)
            if not match:
                continue
            ident = match.group(1)
            for subvol in self.subvol_iter():
                if subvol.ident == ident:
                    subvol.mount = mount
                    if mount == '/.snapshots':
                        self.snap_subvol = subvol
                    break
        self.snap_targets = []
        for dev_ns in self.devs.values():
            dev_ns.subvols = sorted(dev_ns.subvols,
                    key=lambda x: (x.mount if x.mount else '/~~~~~~~~~~~~', x.path))
            for subvol_ns in dev_ns.subvols:
                if (subvol_ns != self.snap_subvol
                        and subvol_ns.depth == 0 and subvol_ns.mount):
                    self.snap_targets.append(subvol_ns)

        if self.DB:
            print('DB: --->>> after determine_mount_points()')
            for subvol in self.subvol_iter():
                if subvol.mount:
                    print(f'DB:   {subvol.dev=} {subvol.mount=} {subvol.path=}')
        assert self.snap_subvol, "cannot find mounted /.snapshots"


    @staticmethod
    def _get_label(string):
        wds = string.rsplit('=', maxsplit=1)
        if len(wds) > 1:
            return f'={wds[1]}'
        return ''

    def gather_snapshots(self):
        """ TBD """
        def link(of_subvol, subvol):
            if of_subvol and subvol:
                of_subvol.snaps.append(subvol)
                of_subvol.snaps = sorted(of_subvol.snaps, key=lambda x: x.path)
                subvol.snap_of = of_subvol
                subvol.snap_label = label = self._get_label(remainder)
                label_group = of_subvol.label_groups.get(label, [])
                label_group.append(subvol)
                of_subvol.label_groups[label] = label_group
                self.label_set.add(label)

        self.label_set = set()
        for subvol in self.subvol_iter():
            wds = subvol.path.split('@snapshots/', maxsplit=1)
            if len(wds) < 2:
                continue
            mat = re.match(r'^(.*)\.[\-\d\:]+', wds[1])
            if not mat:
                link(self.snap_subvol, subvol)
                continue
            of_path = f'/{mat.group(1)}'
            remainder = wds[1][len(of_path):] # everything after the .
            dev_ns = self.devs[subvol.dev]
            of_subvol = dev_ns.paths.get(of_path, None)
            if of_subvol:
                link(of_subvol, subvol)
            else:
                link(self.snap_subvol, subvol)

        if self.DB:
            print('DB: --->>> after gather_snapshots()')
            for subvol in self.subvol_iter():
                if subvol.snaps:
                    print(f'DB:   {subvol.dev=} {subvol.mount=} {subvol.path=}')
                for snap in subvol.snaps:
                    print(f'DB:   snap {snap.path=}')


    def make_rows(self):
        """ Create the set of rows for display with only the subset of info
        needed for display"""
        def init_row(size, mount, dev, path, subvol_ns):
            return SimpleNamespace(size=size, mount=mount,
                               dev=dev, path=path, subvol_ns=subvol_ns)

        self.rows = []

        for dev, dev_ns in self.devs.items():
            for ns in dev_ns.subvols:
                row = init_row(ns.size, ns.mount, dev, ns.path, ns)
                self.rows.append(row)
                for snap in ns.snaps:
                    row = init_row(snap.size,
                           snap.mount, snap.dev, snap.path, snap)
                    self.rows.append(row)
        if self.DB:
            for row in self.rows:
                print(f'DB: row: {row.size=} {row.mount=}'
                      f' {row.dev=} {row.path=!r}')

    def _get_disk_usage(self):
        """This actually only works for the snaps."""
        def convert_human(val):
            if val.endswith('GiB'):
                val = float(val[:-3]) * (2**30)
            elif val.endswith('MiB'):
                val = float(val[:-3]) * (2**20)
            elif val.endswith('KiB'):
                val = float(val[:-3]) * (2**10)
            elif re.search(r'\dB$', val):
                val = float(val[:-1]) * 1
            else:
                print(f'ERR: cannot parse({val}')
                val = 0
            return int(round(val))

        if not self.snap_subvol:
            return
        dev_ns = self.devs.get(self.snap_subvol.dev, None)
        if not dev_ns:
            return

        cmd = f'cd "{dev_ns.tmp_path}" && btrfs fi du -s {self.snap_subvol.path[1:]}/*'
        lines, _, _ = self._slurp_command(cmd)
        for line in lines[1:]:
            total, exclusive, _, pathname = re.split(r'\s+', line.strip(), maxsplit=3)
            pathname = os.path.join('/', pathname)
            if not pathname in dev_ns.paths:
                continue
            snap_ns = dev_ns.paths[pathname]
            snap_ns.size = convert_human(exclusive)
            if snap_ns.snap_of:
                if not snap_ns.snap_of.size:
                    snap_ns.snap_of.size = 0
                snap_ns.snap_of.size = max(convert_human(total),
                    snap_ns.snap_of.size)

        for row in self.rows:
            row.size = row.subvol_ns.size
        self.show_size = True

        if self.DB:
            print('DB: --->>> after _get_disk_usage()')
            for ns in self.subvol_iter():
                print(f'DB: {ns.size=} {ns.path=!r}')

    def calc_path_width(self):
        """ TBD """
        rv = max([len(x.path) for x in self.rows])
        return max(len('Subvolume'), rv)

    def calc_mounts_width(self):
        """ TBD """
        rv = max([len(x.mount) for x in self.rows])
        return max(len('Mount'), rv)

    def calc_devs_width(self):
        """ TBD """
        rv = max([len(x.dev) for x in self.rows])
        return max(len('Device'), rv)

    def refresh_info(self, body=None):
        """ TBD """

        win = self.win
        self._refresh_if_dirty()
        path_width = self.calc_path_width()
        mounts_width = self.calc_mounts_width()
        devs_width = self.calc_devs_width()

        win.add_header('MY-SNAPS', attr=cs.A_REVERSE)
        win.add_header(
            '  s:+snap d:-subvol u:disk-usage r:replace-all a:add-all x:exit ?:help',
            resume=True)
        for dev_ns in self.devs.values():
            win.add_header(f'df: {dev_ns.diskfree}')
        size_hdr = f' {"~Size":>7}'
        win.add_header(
              f'{"Mount":>{mounts_width}}'
              f'{size_hdr if self.show_size else ""}'
              f' {"Device":>{devs_width}}'
              f' {"Subvolume":<{path_width}}',
              attr=cs.A_BOLD,
              )

        if body:
            body = body if isinstance(body, (list, tuple)) else [body]
            for line in body:
                win.add_body(line, attr=cs.A_REVERSE)
            return

        for row in self.rows:
            wds = row.path.split('@snapshots/', maxsplit=1)
            if len(wds) < 2:
                shown_path = row.path
            else:
                shown_path = f'--> {wds[1]}'
                if row.subvol_ns.ago_str:
                    shown_path += f' {row.subvol_ns.ago_str}'
            if row.subvol_ns.snap_of == self.snap_subvol:
                shown_path = '!!!' + shown_path[3:]
            mount_str = row.mount if row.mount else '' if row.subvol_ns.snap_of else '~'
            size_str = f' {"-" if row.size is None else human(row.size):>7}'
            win.add_body(
                  f'{mount_str:>{mounts_width}}'
                  f'{size_str if self.show_size else ""}'
                  f' {row.dev:>{devs_width}}'
                  f' {shown_path:<{path_width}}')

btrfs = None

def rerun_module_as_root(module_name):
    """ rerun using the module name """
    if os.geteuid() != 0: # Re-run the script with sudo
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        vp = ['sudo', sys.executable, '-m', module_name] + sys.argv[1:]
        os.execvp('sudo', vp)

def main():
    """ TBD """
    global btrfs
    import argparse
    rerun_module_as_root('my_snaps.main')
    if os.geteuid() != 0: # Re-run the script with sudo
        module_name = 'my_snaps.main'
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        vp = ['sudo', sys.executable, '-m', module_name] + sys.argv[1:]
        os.execvp('sudo', vp)

    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--add-snap-max', type=int, default=None,
            help='add snapshots limited to value per subvol [1<=val<=8]')
    parser.add_argument('-L', '--label', type=str,
            help='add given label to -s snapshots')
    parser.add_argument('-p', '--print', action="store_true",
            help='print the subvolumes/snaps and exit')
    parser.add_argument('--cron', type=str,
            choices=('hourly', 'daily', 'weekly', 'monthly'),
            help='install a periodic snapshot anacron job')
    parser.add_argument('--DB', action="store_true",
            help='add some debugging output')
    opts = parser.parse_args()
    if opts.cron:
        if not opts.label:
            opts.label = '=' + opts.cron.capitalize()
        if opts.add_snap_max is None:
            defaults={'hourly': 2, 'daily': 2, 'weekly': 2, 'monthly':1}
            opts.add_snap_max = defaults[opts.cron]
        opts.print = False
    if opts.add_snap_max is None:
        opts.add_snap_max = 0
    if opts.add_snap_max > 0:
        opts.add_snap_max = min(opts.add_snap_max, 8)

    btrfs = BTRFS(opts)
    btrfs.umount_tmps()

def run():
    """ Entry point"""
    try:
        main()
    except KeyboardInterrupt:
        if btrfs and btrfs.win:
            btrfs.stop_curses()
        print('\n   OK, QUITTING NOW\n')
        sys.exit(0)
    except Exception as exce:
        if btrfs and btrfs.win:
            btrfs.stop_curses()
        print("exception:", str(exce))
        print(traceback.format_exc())
        sys.exit(15)

if __name__ == "__main__":
    run()
