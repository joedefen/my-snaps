#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BTRFS snapshot tool implementing the simplest snapshot strategy; i.e.,
  * replace eldest snapshots with new ones before updates, and
  * generally keep those snapshots until the next update.

NOTE: for basic debugging, pass --DB and it will dump major objects w/o
starting the window.
"""
# This module is the TUI plus its device/mount bookkeeping.  It is long because
# those concerns share one live state machine; keep new behaviour in small
# helpers instead of growing `_start_window()` or `refresh_info()`.
# pylint: disable=too-many-lines

import argparse
import sys
import os
import re
import shlex
import atexit
import traceback
import subprocess
import curses as cs
from types import SimpleNamespace
from console_window import (ConsoleWindow, ConsoleWindowOpts,
                            InlineConfirmation, OptionSpinner)
from my_snaps.MyUtils import (human, ago_whence, timestamp_str,
                              reexec_as_root, version_string)

# `a` (add-all) keeps at most this many snapshots of one label per subvolume
# in the TUI.  It is a convenience default, not a hard limit: `-k/--keep`
# changes it, while `-s/--add-snap-max` states an exact retention for
# non-interactive runs.
DEFAULT_KEEP_SNAPS = 8

# A typo guard for `-s`/`-k`.  Values above it are refused outright, never
# clamped: `-s` deletes as well as creates, so clamping it would silently
# remove snapshots the operator asked to keep.
SANITY_MAX_SNAPS = 1000

##############################################################################

class BTRFSError(RuntimeError):
    """ An operational refusal that `run()` reports without a traceback.

    Raised for conditions the user can act on (a non-BTRFS root, a missing
    `/.snapshots` mount, a device that will not mount).  Unlike an `assert`,
    it survives `python -O`, and unlike a bare exception it lets `run()`
    print just the actionable message.
    """


class BTRFS:  # pylint: disable=too-many-instance-attributes
    """ Drive the my-snaps TUI and the non-interactive snapshot actions.

    One instance owns the discovered devices and subvolumes (`self.devs`),
    the rows shown on screen, the temporary mounts under `/tmp`, and the
    curses window. `main()` creates it after the root re-exec; constructing
    it only records options.
    """

    # The live instance, used by `run()` to restore the terminal from an
    # exception handler.  A class attribute keeps the reference explicit
    # instead of a module-wide `global` that is easy to miss.
    current = None

    # Device, mount, row and inline-prompt state deliberately live on one
    # object: every action needs most of it, so splitting the class would
    # only push a dozen collaborators through each call.

    # How long the TUI waits for a keystroke before it checks for a
    # hot-plugged device or a new mount (see _poll_device_changes).
    IDLE_POLL_SECONDS = 60

    # Options for the temporary bookkeeping mounts under `tmp_dir`.
    #
    # `subvolid=5` pins the filesystem's top level: without it a configured
    # default subvolume would make every path from `btrfs sub list` point at
    # the wrong place.  Discovery reads through a read-only mount; creating
    # or deleting a subvolume needs a writable one, so it gets its own
    # short-lived mount instead of making the bookkeeping mount writable.
    MOUNT_OPTS = {'ro': 'ro,subvolid=5', 'rw': 'subvolid=5'}

    # Set only on systems without lsblk: `blkid` probes every medium and can
    # stall for seconds, so its result is cached for the process.  The normal
    # `lsblk` path is rescanned on every refresh (see _scan_block_devices).
    blkid_records = None

    def __init__(self, opts):
        self.tmp_dir = '/tmp/.btrfs/'
        self.temps = {} # keyed by device (e.g., /dev/nvme0n1p2)
        # Keyed by filesystem UUID (or by device name when no UUID exists),
        # so a multi-device filesystem appears once, not once per member.
        self.devs = {}
        # Every member name and display alias -> key in `self.devs`.
        self.dev_aliases = {}
        self.snap_targets = [] # the potential subvols to snapshot
        self.label_set = set() # all labels of current snapshots
        self.snap_subvol = None
        self.db = opts.db
        self.add_limit = opts.add_snap_max
        self.keep = opts.keep  # cap for `a` when no `-s` limit applies
        self.label = None   # one label of current interest
        self.help_mode = False
        self.win = None
        self.rows = []
        self.dirty = True # re-scan devices and mounts before the next draw
        self.show_size = False # until we calc disk usage

        # Inline (non-modal) confirmation/input.  The prompt is rendered as an
        # ordinary line under the picked row instead of an overlay dialog, so
        # the rest of the listing stays visible while we ask.  The widget owns
        # the buffer, the caret and the editing keys; this app only supplies
        # the prompt wording, the row it belongs under and the callback.
        self.confirm = InlineConfirmation()
        self._confirm_prompt = ''
        self._confirm_labels = ''
        self._confirm_callback = None
        self._confirm_prev_passthrough = False

        # Startup progress goes to stderr only when interactive (and before
        # curses owns the screen) so logs/cron output stay clean.
        self.show_progress = bool(getattr(sys.stderr, 'isatty', lambda: False)())

        # Fingerprints of the last full refresh, used by the idle poll to tell
        # whether a hot-plugged device or a new BTRFS mount appeared.  They are
        # deliberately not caches: every dirty refresh re-reads lsblk and
        # /proc/mounts, so an action never works from a stale device list.
        self.dev_fingerprint = None
        self.mount_fingerprint = None

        atexit.register(self.umount_tmps)

        if opts.cron:
            self._install_cron_job(opts)
            sys.exit(0)

        self._refresh_if_dirty()

        if self.db:
            self._get_disk_usage()
            sys.exit(0)


        if self.add_limit > 0:
            # `-s` without `-L` manages the unlabeled snapshots.  Their
            # `label_groups` entries are keyed by '', so storing None here
            # would select no group and the old unlabeled snaps would never
            # be removed.
            self.label = self._normalize_label(opts.label)
            if self.add_limit > DEFAULT_KEEP_SNAPS:
                print(f'NOTE: keeping {self.add_limit} snapshots per '
                      'subvolume/label', file=sys.stderr)
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

    def _progress(self, message, final=False):
        """ Show transient startup progress on stderr (interactive, no window).
        Percent-complete is deliberately not shown: the amount of work is not
        knowable until "btrfs sub list" (and possibly mount) has returned, so
        phase counts and per-device indexes are the only honest measures.
        """
        if not self.show_progress or self.win is not None:
            return
        if final:
            sys.stderr.write('\r' + ' ' * 79 + '\r')
        else:
            sys.stderr.write('\r' + message[:79].ljust(79))
        sys.stderr.flush()

    def _refresh_if_dirty(self):
        if not self.dirty:
            return
        step, steps = 1, 5

        def phase(label):
            self._progress(
                f'Reading BTRFS state. Be patient ... [{step}/{steps}] {label}')

        phase('scanning known subvolumes')
        # save sizes for xfer to refresh (cuz expensive)
        sizes = {}
        for ns in self.subvol_iter():
            sizes[f'{ns.dev}{ns.path}'] = ns.size

        step += 1
        phase('identifying BTRFS devices')
        self._load_devs()  # learn all the BTRFS partitions

        step += 1
        phase('mounting BTRFS devices')
        self._mount_tmps()  # mount BTRFS partitions in /tmp/.btrfs

        step += 1
        phase('matching mount points')
        self._determine_mount_points() # associated current mounts with subvols

        step += 1
        phase('gathering snapshots')
        self.gather_snapshots()  # determine subvols that are snapshots
        # restore sizes after refresh
        for ns in self.subvol_iter():
            key = f'{ns.dev}{ns.path}'
            if key in sizes:
                ns.size = sizes[key]
        self.make_rows()
        self.dirty = False
        self._progress('', final=True)

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
            for top_ns in dev_ns.subvols:
                if top_ns.depth == 0:
                    yield from children_iter(top_ns, top_down=top_down)

    def _cur_snap_suffix(self):
        return '.' + timestamp_str() + ('' if self.label is None else self.label)

    def stop_curses(self):
        """Terminate curses if running."""
        if self.win:
            self.win.stop_curses()

    def _handle_key(self, key, spin, win):
        """ Route one keypress and return the spinner's current value.

        `key` is None when the prompt woke on its idle timeout (or a resize):
        poll for hot-plugged devices instead of acting.  While an inline
        confirmation is live every key belongs to it, so the action keys must
        not fire underneath the prompt.
        """
        if self.confirm.active:
            if key is not None:
                self.handle_confirm_key(key)
            return None
        if key is None:
            self._poll_device_changes()
            return None
        value = spin.do_key(key, win)
        if key in (ord('u'), ) and not self.help_mode:
            self._confirm_disk_usage()

        elif key in (ord('f'), ) and not self.help_mode:
            # "freshen": rebuild devices, mounts and subvolume trees on the
            # next loop iteration (refresh_info() runs right after the key).
            self.dirty = True

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

    def _start_window(self):
        spin = OptionSpinner()
        spin.add_key('help_mode', '? - toggle help screen', vals=[False, True], obj=self)

        base_keys_we_handle=[cs.KEY_ENTER,
                10, ord('s'), ord('d'), ord('u'), ord('f'), ord('r'), ord('a'),
                ord('x')]

        win = self.win = ConsoleWindow(opts=ConsoleWindowOpts(
            keys=set(list(spin.keys) + list(base_keys_we_handle))))

        for _ in range(100000000000):
            if self.help_mode:
                win.set_pick_mode(False)
                spin.show_help_nav_keys(win)
                spin.show_help_body(win)
                self.win.add_body('Action keys:', attr=cs.A_UNDERLINE)
                self.win.add_body(' d - delete highlighted item')
                self.win.add_body(' s - create snapshot for highlighted item')
                self.win.add_body(' r - replace eldest snapshot of each subvol')
                self.win.add_body('     (on a labeled snapshot: that label only)')
                self.win.add_body(' a - add snapshot to each subvol with snapshots')
                self.win.add_body('     (on a labeled snapshot: that label only)')
                self.win.add_body(' u - gather disk usage (slow; asks first)')
                self.win.add_body(' f - freshen device and mount discovery')
                self.win.add_body(' x - exit')
            else:
                win.set_pick_mode(True)
                self.refresh_info()
            win.render()
            try:
                # The idle timeout is our device poll: after this many seconds
                # without a keystroke, look for a hot-plugged disk or a new
                # mount instead of waiting for a restart.
                _ = self._handle_key(
                    win.prompt(seconds=self.IDLE_POLL_SECONDS), spin, win)
            # Any failure must still restore the terminal, so this catch is
            # deliberately broad.
            except Exception as exce:  # pylint: disable=broad-exception-caught
                win.stop_curses()
                print("exception:", str(exce))
                print(traceback.format_exc())
                sys.exit(15)
            win.clear()

    ####################################################################
    # Inline (non-modal) confirmation / input
    #
    # console_window's InlineConfirmation widget asks for confirmation
    # without an overlay dialog: the prompt is drawn as an ordinary body
    # line and the keys are routed to the widget.  Mode 'y' is a single
    # keystroke; mode 'text' is a (seeded) line editor.
    ####################################################################

    def _begin_confirm(self, prompt, *, mode='yes', identity=None, seed='',
                       labels=None, action_type=None, on_confirm=None):
        """ Start an inline prompt; returns False when there is no window. """
        # Keyword-only UI options: bundling them in a dataclass would add an
        # indirection to every call site without making the calls clearer.
        # pylint: disable=too-many-arguments
        if not self.win:
            return False
        self.confirm.start(action_type=action_type, identity=identity,
                           mode=mode, initial=seed)
        self._confirm_prompt = prompt
        self._confirm_labels = labels or ''
        self._confirm_callback = on_confirm
        # Passthrough mode already forwards printable keys, the editing keys
        # (Backspace, Delete, arrows, Home/End, Ctrl-A/E) and ESC, so no
        # handled_keys juggling is needed to drive the widget.
        self._confirm_prev_passthrough = self.win.passthrough_mode
        self.win.passthrough_mode = True
        return True

    def _end_confirm(self):
        """ Cancel the inline prompt and restore the window's key routing. """
        self.confirm.cancel()
        self._confirm_prompt = ''
        self._confirm_labels = ''
        self._confirm_callback = None
        if self.win:
            self.win.passthrough_mode = self._confirm_prev_passthrough

    def handle_confirm_key(self, key):
        """ Route one key to the active inline prompt.

        Returns True when the key was consumed, False when no prompt is
        active.  A confirmed answer is delivered to the callback captured by
        _begin_confirm() AFTER the prompt has been torn down, so the callback
        is free to start another prompt (e.g., to re-ask after bad input).
        """
        if not self.confirm.active:
            return False
        if key is None:  # resize/timeout wakes the prompt without a keystroke
            return True
        result = self.confirm.handle_key(key)
        if result == 'confirmed':
            answer = self.confirm.input_buffer
            callback = self._confirm_callback
            self._end_confirm()
            if callback:
                callback(answer)
        elif result == 'cancelled':
            self._end_confirm()
        return True

    def _confirm_line(self):
        """ The body line that displays the live inline prompt.

        Kept terse on purpose: the warning glyph marks the line, and a short
        bracketed hint reminds of the two keys that matter.  In text mode the
        keys are named up-front (before the text being edited) and the widget
        composes the buffer with its caret glyph at the editing position.
        """
        mode = self.confirm.mode
        if mode == 'text':
            return ('  ⚠️ '
                    + self.confirm.get_display_string(
                        f'{self._confirm_prompt}: '))
        if mode in ('Y', 'y'):
            hint = f'Press "{mode}" or ESC'
        else:
            hint = self.confirm.get_hint()
        return f'  ⚠️ {self._confirm_prompt}   [{hint}]'

    def _confirm_lines(self):
        """ All body lines for the live prompt, as (text, attr) pairs.

        The label list of the "_all" operations lives on its own dim line so
        the prompt itself stays short.
        """
        lines = [(self._confirm_line(), cs.A_BOLD)]
        if self._confirm_labels:
            lines.append((f'     labels: {self._confirm_labels}', cs.A_DIM))
        return lines

    def _flash_error(self, message, seconds=4.0):
        """ Report a refusal in a flash (auto-dismissing) box, not an alert.

        Denials only need a few words read, so there is no point in making the
        user press a key to get rid of a full alert dialog.  Without a window
        (-s / --cron / -p) stderr is the only channel, so the refusal is
        printed instead of being swallowed.
        """
        if self.win:
            self.win.flash(message, duration=seconds)
        else:
            print(f'ERROR: {message}', file=sys.stderr)
        return False

    def _report_error(self, message):
        """ Report a failed command: alert in the TUI, stderr otherwise.

        `-s`/`--cron` run with `self.win is None`, so an alert would raise
        AttributeError and hide the real btrfs error behind a traceback.
        """
        if self.win:
            self.win.alert(message)
        else:
            print(f'ERROR: {message}', file=sys.stderr)
        return False

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


    def _picked_subvol_ns(self):
        """ The subvolume of the row under the cursor (None when there is none). """
        pick_pos = getattr(self.win, 'pick_pos', None)
        if not isinstance(pick_pos, int) or not 0 <= pick_pos < len(self.rows):
            return None
        return self.rows[int(pick_pos)].subvol_ns

    def _picked_snap_label(self):
        """ The label of the snapshot under the cursor ('' when it is not one).

        Sitting on e.g. "...--> deb@root.2026-10-23-073714=Daily" means the
        intent is about the =Daily family of snapshots rather than the whole
        listing, so r/a only need a yes/no instead of the suffix prompt.
        """
        subvol_ns = self._picked_subvol_ns()
        if subvol_ns is None or not subvol_ns.snap_of:
            return ''
        return subvol_ns.snap_label or ''

    def _labeled_targets(self, label):
        """ The contemporary subvolumes that own a snapshot with `label`.

        `snap_targets` is already the mounted, top-level set the cron job
        maintains, so stale subvolumes (e.g., an old install's /kub@home) are
        not resurrected just because they still hold a =Daily snapshot.
        """
        return [subvol_ns for subvol_ns in self.snap_targets
                if subvol_ns.label_groups.get(label)]

    def _replace_eldest_snaps(self, just_add=False):
        """ Ask (inline) for the snap suffix, then retain/add per subvolume.

        When the cursor sits on a labeled snapshot, r/a act on that label only
        and need just a yes/no: there is nothing to type.
        """
        if self.add_limit > 0:  # cron / -s: fully non-interactive
            return self._do_replace_eldest_snaps(
                self._cur_snap_suffix(), just_add=just_add)

        label = self._picked_snap_label()
        if label:
            return self._confirm_labeled_snaps(label, just_add)

        self._begin_confirm(
            'Set snap suffix, then ENTER or ESC',
            mode='text', seed=self._cur_snap_suffix(),
            labels=' '.join(sorted(x for x in self.label_set if x)),
            action_type='replace',
            on_confirm=lambda suffix: self._suffix_entered(suffix, just_add))
        return None

    def _confirm_labeled_snaps(self, label, just_add):
        """ Inline y/ESC confirmation for the labeled snaps under the cursor.

        Only mounted top-level subvolumes are candidates.  A snapshot whose
        owning subvolume is not one of them (unmounted, e.g. /kub@home) is
        refused rather than silently acting on the other subvolumes.
        """
        verb = 'Add' if just_add else 'Replace'
        picked = self._picked_subvol_ns()
        owner = picked.snap_of if picked else None
        if owner is None or owner not in self.snap_targets:
            where = owner.path if owner else 'this subvolume'
            return self._flash_error(
                f'Cannot {verb.lower()} snaps of {where} '
                '(not a mounted top-level subvolume)')
        targets = self._labeled_targets(label)
        if not targets:
            return self._flash_error(f'No subvolumes have {label} snapshots')
        plural = 's' if len(targets) != 1 else ''
        self._begin_confirm(
            f'{verb} a {label} snap for {len(targets)} subvol{plural}?',
            mode='y', identity=picked.path if picked else None,
            action_type='add' if just_add else 'replace',
            on_confirm=lambda answer: self._labeled_snaps_confirmed(
                label, just_add, targets, answer))
        return None

    def _labeled_snaps_confirmed(self, label, just_add, targets, answer):
        """ Run the labeled replace/add only for a 'y' answer. """
        if not answer.strip().lower().startswith('y'):
            return
        self.label = label
        self._do_replace_eldest_snaps(
            self._cur_snap_suffix(), just_add=just_add, targets=targets)

    def _suffix_entered(self, suffix, just_add):
        """ Validate an inline suffix; bad input re-prompts inline. """
        if suffix and (len(suffix) < 5 or suffix[:1] != '.'
                    or any(x in suffix[1:] for x in './')):
            self._begin_confirm(
                'Suffix must be "." + 4 chars, no "." or "/"',
                mode='text', seed=suffix, action_type='replace',
                on_confirm=lambda value: self._suffix_entered(value, just_add))
            return
        if not suffix:
            return
        self.label = self._get_label(suffix)
        self._do_replace_eldest_snaps(suffix, just_add=just_add)

    def _do_replace_eldest_snaps(self, suffix, just_add=False, targets=None):
        """ The actual replace/add work, given an already accepted suffix.

        `add` (`just_add`) keeps at most `self.keep` snaps of the label per
        subvolume, or `self.add_limit` when `-s` stated an exact retention.
        `replace` keeps as many as the largest family already present, so it
        never trims.
        """
        if targets is None:
            targets = self.snap_targets
        label = self.label or ''
        keep = self.add_limit or self.keep
        success = None
        counts = [len(subvol_ns.label_groups.get(label, []))
                  for subvol_ns in targets]
        max_cnt = self.add_limit if self.add_limit else max(counts, default=0)

        for subvol_ns in targets:
            like_snaps = subvol_ns.label_groups.get(label, [])
            this_cnt = len(like_snaps)
            if just_add:
                discard = max(0, this_cnt-keep+1)
            else:
                discard = max(0, this_cnt-max_cnt+1)

            rv = self._replace_eldest_snaps_of_subvol(subvol_ns,
                             like_snaps, suffix=suffix, discard=discard)
            success = rv if success is None else rv if success else False
        self.label = None
        return success


    def _snap_blocker(self, subvol_ns):
        """ Return why `subvol_ns` cannot be snapshotted, or None. """
        if subvol_ns.snap_of:
            return 'Cannot snap a snapshot'
        if not subvol_ns.mount:
            return 'Cannot snap an unmounted subvolume'
        if subvol_ns.mount == '/.snapshots':
            return 'Cannot snap the snapshot subvolume'
        return None

    def _create_snap(self, subvol_ns=None, suffix=None):
        if not subvol_ns:
            subvol_ns = self.rows[self.win.pick_pos].subvol_ns

        blocker = self._snap_blocker(subvol_ns)
        if blocker:
            return self._flash_error(blocker)

        if not suffix:
            # Inline text prompt: the row itself names the subvolume, so the
            # prompt only asks for the suffix; ESC cancels.
            self._begin_confirm(
                'Set snap suffix, then ENTER or ESC',
                mode='text', identity=subvol_ns.path,
                seed=self._cur_snap_suffix(), action_type='snap',
                on_confirm=lambda value: self._create_snap(subvol_ns, value))
            return None

        # The read-only bookkeeping mount cannot receive a snapshot, so ask
        # for the writable twin only when a snapshot is actually created.
        rw_dir = self._ensure_rw_mount(subvol_ns.dev)
        snap_dir = f'{rw_dir}{self.snap_subvol.path}'
        snap_path = f'{snap_dir}{subvol_ns.path}{suffix}'

        cmd = ['btrfs', 'sub', 'snap', '-r', subvol_ns.mount, snap_path]
        out, err, code = self._slurp_command(cmd)
        if code:
            return self._report_error(
                f'FAILED({code}): {shlex.join(cmd)}\n' + '\n'.join(out + err))
        self.dirty = True
        return True

    def _del_subvolume(self, subvol_ns=None, ans=""):
        if not subvol_ns:
            subvol_ns = self.rows[self.win.pick_pos].subvol_ns

        for ns in self.subvol_iter(subvol_ns, top_down=False):
            if ns.mount:
                return self._flash_error(f'Cannot delete mounted {ns.mount}')

        if not ans:
            # Inline single-key confirmation: 'y' proceeds, ESC cancels.
            if not self._begin_confirm(
                    f'Delete "{subvol_ns.path}"', mode='y',
                    identity=subvol_ns.path, action_type='delete',
                    on_confirm=lambda _value: self._del_subvolume(
                        subvol_ns, ans='y')):
                return False
            return None
        if not ans.strip().lower().startswith('y'):
            return False

        rw_dir = self._ensure_rw_mount(subvol_ns.dev)
        for ns in self.subvol_iter(subvol_ns, top_down=False):
            snap_path = f'{rw_dir}{ns.path}'
            cmd = ['btrfs', 'sub', 'del', snap_path]
            out, err, code = self._slurp_command(cmd)
            if code:
                return self._report_error(
                    f'FAILED: {shlex.join(cmd)}\n' + '\n'.join(out + err))
            self.dirty = True
        return True

    def _confirm_disk_usage(self):
        """ Ask before the slow `btrfs fi du` pass (it can take minutes). """
        return self._begin_confirm(
            'Gather disk usage (can take quite a while)?',
            mode='y', action_type='usage',
            on_confirm=self._gather_disk_usage)

    def _gather_disk_usage(self, answer='y'):
        """ Run an accepted `du` pass, then redraw the listing with sizes. """
        if not answer.strip().lower().startswith('y'):
            return False
        win = self.win
        if win:
            win.clear()
            win.set_pick_mode(False)
            self.refresh_info(
                body=' ... BE REALLY PATIENT (running "du") ...')
            win.render()
        self._get_disk_usage()
        if win:
            win.set_pick_mode(True)
            self.refresh_info()
        return True

    def _slurp_command(self, command, cwd=None):
        """ Run an argv list; return (stdout, stderr, exit code).

        `command` must be a sequence of arguments, never a string: this tool
        runs as root, and subvolume/label names come from the filesystem, so
        a name containing spaces, quotes or `$(...)` must reach the kernel as
        one argument instead of being re-parsed by a shell.

        `communicate()` must be the first blocking call on the child: it
        drains stdout and stderr concurrently.  Calling `wait()` first
        deadlocks as soon as either stream fills its pipe buffer, which is
        plausible for `btrfs sub list` on a large tree or for `df`.
        """
        if isinstance(command, str):
            raise TypeError('command must be an argv sequence, not a string')
        command = [str(part) for part in command]
        if self.db:
            print('DB: +', shlex.join(command))
        # Not a `with subprocess.Popen(...)`: Popen.__exit__ waits for the
        # child, and this is the one place where communicate() must be the
        # only blocking call (see above).  The process is short-lived and
        # communicate() reaps it.
        # pylint: disable=consider-using-with
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, cwd=cwd)
        output, err = process.communicate()
        output = output.decode('utf-8', errors='replace')
        err = err.decode('utf-8', errors='replace')
        output = output.splitlines(keepends=False)
        err = err.splitlines(keepends=False)
        return (output, err, process.returncode)

    @staticmethod
    def _slurp_file(pathname):
        with open(pathname, "r", encoding='utf-8') as fh:
            return [line.strip() for line in fh]

    @staticmethod
    def dev_path(dev):
        """ Return the full path to a dev (aka device basename) """
        return f'/dev/{dev}'

    def _dev_ns(self, dev):
        """ The device namespace for a filesystem key, alias or member name. """
        aliases = getattr(self, 'dev_aliases', {})
        return self.devs.get(aliases.get(dev, dev))

    LSBLK_FIELDS = 'NAME,PATH,TYPE,FSTYPE,LABEL,UUID'

    @staticmethod
    def parse_lsblk_line(line):
        """ Parse one `lsblk -P` line into a {FIELD: value} dict. """
        return dict(re.findall(r'([A-Z_]+)="([^"]*)"', line))

    @staticmethod
    def parse_blkid_line(line):
        """ Parse one `blkid` line into the `lsblk -P` field spelling. """
        rec = {field.upper(): value
               for field, value in re.findall(r'(\w+)="([^"]*)"', line)}
        if 'TYPE' in rec:  # blkid spells the filesystem type "TYPE"
            rec['FSTYPE'] = rec.pop('TYPE')
        rec['PATH'] = line.split(':', maxsplit=1)[0]
        rec['NAME'] = os.path.basename(rec['PATH'])
        return rec

    def _scan_block_devices(self):
        """ Describe the block devices *without probing* their media.

        `blkid` with no arguments opens every block device and runs the whole
        signature detection chain.  A single idle optical drive (or a
        spun-down disk) can block that for 15+ seconds, and all this tool
        needs from it is the set of BTRFS devices.  `lsblk -P` answers from
        sysfs plus the udev database in a few milliseconds, so it is the
        primary source; `blkid` stays as a fallback for the (unlikely) case
        of a system without lsblk.

        The scan is *not* cached: it runs once per refresh (a few
        milliseconds), so a device hot-plugged after startup is visible
        without restarting the tool.  The exception is the `blkid` fallback,
        whose result is kept (see `blkid_records`) because probing media is
        what makes it slow in the first place.
        """
        out, _, code = self._slurp_command(
            ['lsblk', '-P', '-o', self.LSBLK_FIELDS])
        if code == 0 and out:
            self.blkid_records = None  # lsblk is back; drop the slow cache
            return [self.parse_lsblk_line(line) for line in out]
        if self.blkid_records is None:
            out, _, _ = self._slurp_command(['blkid'])
            self.blkid_records = [self.parse_blkid_line(line) for line in out]
        return self.blkid_records

    @staticmethod
    def _fingerprint_records(records):
        """ Stable identity of a block-device scan (no media probing). """
        return tuple(sorted(
            (rec.get('NAME') or os.path.basename(rec.get('PATH', '')),
             rec.get('FSTYPE', ''), rec.get('UUID', ''), rec.get('LABEL', ''))
            for rec in records))

    @staticmethod
    def _mount_fingerprint(mounts):
        """ The (device, mountpoint) pairs of the BTRFS mounts in `mounts`. """
        return tuple((dev, mount) for dev, mount, fstype, _ident, _subvol
                     in mounts if fstype == 'btrfs')

    def _poll_device_changes(self):
        """ Dirty the model when a device or BTRFS mount appeared/disappeared.

        Called on prompt timeouts only (see `_start_window`), never in a loop:
        a few milliseconds of `lsblk` and `/proc/mounts` is enough to notice a
        hot-plugged disk, and the expensive rebuild (`btrfs sub list`, `df`,
        mounting) runs on the next refresh only when something changed.
        """
        records = self._scan_block_devices()
        if (self._fingerprint_records(records) != self.dev_fingerprint
                or self._mount_fingerprint(self._all_mounts())
                != self.mount_fingerprint):
            self.dirty = True
            return True
        return False

    @staticmethod
    def _record_name(rec):
        """ The block-device basename of one device-scan record. """
        return rec.get('NAME') or os.path.basename(rec.get('PATH', ''))

    def _load_devs(self):
        """ Discover BTRFS filesystems (no probing, see _scan_block_devices).

        Every member of a multi-device BTRFS filesystem reports the same
        filesystem UUID.  Grouping the scan records by that UUID gives one
        `devs` entry per filesystem, so it is mounted, counted and listed
        once instead of once per member.  Devices without a UUID cannot be
        grouped and keep the old per-device behaviour.

        `dev_ns.dev` stays the short name used in the device column and in
        the temporary mount directory: the first member's name for a
        single-device filesystem, or `<first>+<others>` when the filesystem
        has more members.  `dev_ns.device_path` is a real member device (any
        member mounts the whole filesystem) and `dev_ns.members` keeps every
        member name.
        """
        records = self._scan_block_devices()
        self.dev_fingerprint = self._fingerprint_records(records)
        self.devs = {}
        self.dev_aliases = {}
        by_fs = {}
        for rec in records:
            if rec.get('FSTYPE') != 'btrfs' or rec.get('TYPE') == 'rom':
                continue
            name = self._record_name(rec)
            if not name:
                continue
            key = rec.get('UUID') or name
            by_fs.setdefault(key, []).append((name, rec))

        for key, members in by_fs.items():
            members.sort(key=lambda item: item[0])
            primary_name, primary = members[0]
            member_names = [name for name, _rec in members]
            others = len(member_names) - 1
            dev = primary_name if not others else f'{primary_name}+{others}'
            dev_ns = SimpleNamespace(
                    dev=dev,
                    device_path=primary.get('PATH')
                    or self.dev_path(primary_name),
                    label=next((rec.get('LABEL', '') for _name, rec in members
                                if rec.get('LABEL')), ''),
                    uuid=primary.get('UUID', ''),
                    members=member_names)
            rows, _, _ = self._slurp_command(['df', '-h', dev_ns.device_path])
            dev_ns.diskfree = rows[1] if len(rows) >= 2 else ''
            self.devs[key] = dev_ns
            self.dev_aliases[key] = key
            self.dev_aliases[dev] = key
            for name in member_names:
                self.dev_aliases[name] = key
        if self.db:
            print('DB: --->>> after load_devs()')
            for key, ns in self.devs.items():
                print(f'DB: {key=}: {vars(ns)}')

    @staticmethod
    def init_subvol_ns(dev='', path='', ident=None, parent=None):
        """ Create a subvolume namespace"""
        return SimpleNamespace(dev=dev, path=path, size=None,
                   depth=0, mount='', snaps=[], label_groups={},
                    children=[], snap_label='', snap_of=None,
                    ident=ident, parent=parent, ago_str='')

    @staticmethod
    def parse_subvol_list(lines):
        """ Parse `btrfs sub list` output into (ident, parent, path) triples. """
        rv = []
        for line in lines:
            wds = line.split(maxsplit=8)
            if len(wds) < 9:
                continue
            rv.append((wds[1], wds[6], f'/{wds[8]}'))
        return rv

    def _init_dev_subvols(self, dev_ns, triples):
        """ (Re)build the subvolume tree of one device from the triples. """
        dev_ns.tmp_path = (getattr(dev_ns, 'ro_path', None)
                           or self._mount_dir(dev_ns.dev, 'ro'))
        dev_ns.paths = {} # subvols keyed by relative path
        dev_ns.idents = {} # subvols keyed by ident
        dev_ns.subvols = []
        for ident, parent, path in triples:
            child = BTRFS.init_subvol_ns(dev=dev_ns.dev,
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

    def _mount_dir(self, dev, mode):
        """ The directory of the `mode` ('ro' or 'rw') tmp mount for `dev`. """
        return os.path.join(self.tmp_dir, dev, mode)

    @staticmethod
    def _remember_tmp_mount(dev_ns, mode, path):
        """ Record a successful tmp mount so `umount_tmps()` can undo it.

        `tmp_path` stays the read-only discovery mount because every path
        taken from `btrfs sub list` is resolved against it.
        """
        setattr(dev_ns, f'{mode}_path', path)
        if mode == 'ro':
            dev_ns.tmp_path = path

    def _ensure_tmp_mount(self, dev, mode='ro'):
        """ Mount `dev` under the tmp dir unless a suitable mount exists.

        The mount is pinned with `subvolid=5`, so the paths returned by
        `btrfs sub list` always line up with the filesystem top level, even
        when a default subvolume is configured.  An existing mount is reused
        only when it already has the requested options: a directory left
        mounted by an older version, or by a previous run that did not exit
        cleanly, is unmounted and replaced instead of trusted.

        Returns the mount dir, or None when the mount fails.
        """
        dev_ns = self._dev_ns(dev)
        if dev_ns is None:
            return None
        # Older versions mounted the device directly at `<tmp_dir>/<dev>`.
        # Leaving that behind would nest the new mounts inside it, so drop it
        # before choosing a path under the device's own directory.
        legacy_dir = os.path.join(self.tmp_dir, dev)
        if os.path.ismount(legacy_dir):
            subprocess.run(['umount', legacy_dir], check=False)
        tmp_mount_dir = self._mount_dir(dev, mode)
        opts = self._mount_options(tmp_mount_dir)
        if opts is not None:
            tokens = set(opts.split(','))
            if 'subvolid=5' in tokens and ('ro' in tokens) == (mode == 'ro'):
                self._remember_tmp_mount(dev_ns, mode, tmp_mount_dir)
                return tmp_mount_dir
            subprocess.run(['umount', tmp_mount_dir], check=False)
        os.makedirs(tmp_mount_dir, exist_ok=True)
        dev_node = getattr(dev_ns, 'device_path', None) or self.dev_path(dev)
        if subprocess.run(['mount', '-o', self.MOUNT_OPTS[mode],
                           dev_node, tmp_mount_dir], check=False).returncode:
            return None
        self._remember_tmp_mount(dev_ns, mode, tmp_mount_dir)
        return tmp_mount_dir

    def _ensure_rw_mount(self, dev):
        """ The writable top-level mount used to create or delete subvolumes.

        Unlike discovery, this is only requested from the mutating actions,
        so a pure listing (`-p`, `--DB`, the first screen) never gets a
        writable mount of the filesystem.
        """
        rw_dir = self._ensure_tmp_mount(dev, 'rw')
        if rw_dir is None:
            raise BTRFSError(
                f'cannot mount {dev} read-write under {self.tmp_dir}; '
                'check dmesg, or mount it manually first')
        return rw_dir


    def _mount_tmps(self):
        """ mount each btrfs as needed """
        os.makedirs(self.tmp_dir, exist_ok=True)
        dev_count = len(self.devs)
        for dev_ndx, dev_ns in enumerate(self.devs.values(), start=1):
            self._progress(
                'Reading BTRFS state. Be patient ... [3/5] '
                f'mounting {dev_ns.dev} ({dev_ndx}/{dev_count})')
            tmp_mount_dir = self._ensure_tmp_mount(dev_ns.dev, 'ro')
            if tmp_mount_dir is None:
                raise BTRFSError(
                    f'cannot mount {dev_ns.dev} under {self.tmp_dir} to read '
                    'its subvolumes; check dmesg, or mount it manually first')
            lines, _, _ = self._slurp_command(
                ['btrfs', 'sub', 'list', tmp_mount_dir])
            self._init_dev_subvols(dev_ns, self.parse_subvol_list(lines))

        if self.db:
            print('DB: --->>> after mount_tmps()')
            for key, dev_ns in self.devs.items():
                print(f'DB: {key=}: {dev_ns.dev=} '
                      f'{dev_ns.members=} keys={vars(dev_ns).keys()}')
            for subvol in self.subvol_iter():
                print(f'DB:   {"  "*subvol.depth}subvol: {vars(subvol)}')


    def umount_tmps(self):
        """ Unmount every temporary mount, writable twins before their ro. """
        for ns in self.devs.values():
            seen = []
            for name in ('rw_path', 'ro_path', 'tmp_path'):
                tmp_path = getattr(ns, name, None)
                if tmp_path and tmp_path not in seen:
                    seen.append(tmp_path)
            for tmp_path in seen:
                if os.path.ismount(tmp_path):
                    subprocess.run(['umount', tmp_path], check=False)

    def _load_mounts(self):
        """ Read "/proc/mounts" afresh on every call.

        Mounts appear while the tool runs (e.g. `/.snapshots` after a manual
        mount, or a USB disk); a copy cached at startup made those later
        mounts invisible until the next restart.
        """
        return self._slurp_file('/proc/mounts')

    @staticmethod
    def parse_mount_line(line):
        """ Parse a "/proc/mounts" line into
        (dev, mount, fstype, subvolid, subvol); the last two are None unless
        the entry is a BTRFS mount.
        """
        wds = re.split(r'\s+', line)
        if len(wds) < 4:
            return None
        dev, mount, fstype, opts = wds[0], wds[1], wds[2], wds[3]
        subvolid = subvol = None
        if fstype == 'btrfs':
            mat = re.search(r'subvolid=(\d+)', opts)
            subvolid = mat.group(1) if mat else None
            mat = re.search(r'subvol=(\S+)', opts)
            subvol = mat.group(1) if mat else None
        return dev, mount, fstype, subvolid, subvol

    def _mount_options(self, pathname):
        """ The option string of the mount at `pathname`, or None.

        `/proc/mounts` escapes characters in the mount point (a space becomes
        `\\040`), so compare the decoded field instead of the raw text.
        """
        wanted = os.path.normpath(pathname)
        for line in self._load_mounts():
            wds = re.split(r'\s+', line)
            if len(wds) < 4:
                continue
            mount = wds[1].replace('\\040', ' ')
            if os.path.normpath(mount) == wanted:
                return wds[3]
        return None

    def _all_mounts(self):
        """ Every "/proc/mounts" entry, as parse_mount_line() tuples. """
        rv = []
        for line in self._load_mounts():
            parsed = self.parse_mount_line(line)
            if parsed:
                rv.append(parsed)
        return rv

    def _attach_mounts(self, mounts):
        """ Copy each mount point onto the subvolume named by its subvolid. """
        self.mount_fingerprint = self._mount_fingerprint(mounts)
        for dev, mount, fstype, ident, _subvol in mounts:
            if mount == '/' and fstype != 'btrfs':
                raise BTRFSError(
                    f'root is not BTRFS: {mount} is {fstype} on {dev}; '
                    'my-snaps only works with a BTRFS root')
            if fstype != 'btrfs' or ident is None:
                continue
            for subvol in self.subvol_iter():
                if subvol.ident == ident:
                    subvol.mount = mount
                    if mount == '/.snapshots':
                        self.snap_subvol = subvol
                    break

    def _collect_snap_targets(self):
        """ Sort each device's top-level subvolumes and note the snap targets. """
        self.snap_targets = []
        for dev_ns in self.devs.values():
            dev_ns.subvols = sorted(dev_ns.subvols,
                    key=lambda x: (x.mount if x.mount else '/~~~~~~~~~~~~', x.path))
            for subvol_ns in dev_ns.subvols:
                if (subvol_ns != self.snap_subvol
                        and subvol_ns.depth == 0 and subvol_ns.mount):
                    self.snap_targets.append(subvol_ns)

    def _determine_mount_points(self):
        """ Map mounts onto subvolumes and pick the snapshot targets. """
        self._attach_mounts(self._all_mounts())
        self._collect_snap_targets()
        if self.db:
            print('DB: --->>> after determine_mount_points()')
            for subvol in self.subvol_iter():
                if subvol.mount:
                    print(f'DB:   {subvol.dev=} {subvol.mount=} {subvol.path=}')
        if not self.snap_subvol:
            raise BTRFSError(
                'cannot find a BTRFS mount at /.snapshots: '
                'mount the snapshot subvolume there before running my-snaps')

    @staticmethod
    def _normalize_label(label):
        """ Canonical label: '' when unlabeled, otherwise '=Name'.

        `label_groups` uses '' for snapshots without a label, so an absent
        `-L` must become '' rather than None to find (and prune) them.
        """
        if not label:
            return ''
        return '=' + label.strip('=')

    @staticmethod
    def _get_label(string):
        if not string:
            return ''
        wds = string.rsplit('=', maxsplit=1)
        if len(wds) > 1:
            return f'={wds[1]}'
        return ''

    def gather_snapshots(self):
        """ Link each snapshot subvolume to the subvolume it was made of.

        A snapshot lives at `<subvol>.YYYY-MM-DD-HHMMSS[=Label]` under the
        `@snapshots` subvolume. The part before the timestamp names its
        origin; snapshots that do not parse that way, or whose origin is not
        mounted, are attached to the snapshot subvolume itself. Each label
        is also collected into `label_groups` so retention can count per
        label.
        """
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
            dev_ns = self._dev_ns(subvol.dev)
            of_subvol = dev_ns.paths.get(of_path, None) if dev_ns else None
            if of_subvol:
                link(of_subvol, subvol)
            else:
                link(self.snap_subvol, subvol)

        if self.db:
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

        for dev_ns in self.devs.values():
            for ns in dev_ns.subvols:
                row = init_row(ns.size, ns.mount, dev_ns.dev, ns.path, ns)
                self.rows.append(row)
                for snap in ns.snaps:
                    row = init_row(snap.size,
                           snap.mount, dev_ns.dev, snap.path, snap)
                    self.rows.append(row)
        if self.db:
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
        dev_ns = self._dev_ns(self.snap_subvol.dev)
        if not dev_ns:
            return

        # Expand the snapshot names here instead of letting a shell do it, so
        # a name with a space or a glob character cannot change the command.
        snap_root = self.snap_subvol.path[1:]
        try:
            entries = [os.path.join(snap_root, name)
                       for name in sorted(os.listdir(
                           os.path.join(dev_ns.tmp_path, snap_root)))
                       if not name.startswith('.')]
        except OSError:
            entries = []
        lines = []
        if entries:
            lines, _, _ = self._slurp_command(
                ['btrfs', 'fi', 'du', '-s', *entries], cwd=dev_ns.tmp_path)
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

        if self.db:
            print('DB: --->>> after _get_disk_usage()')
            for ns in self.subvol_iter():
                print(f'DB: {ns.size=} {ns.path=!r}')

    def calc_path_width(self):
        """ Return the display width of the path column."""
        rv = max((len(x.path) for x in self.rows), default=0)
        return max(len('Subvolume'), rv)

    def calc_mounts_width(self):
        """ Return the display width of the mount column."""
        rv = max((len(x.mount) for x in self.rows), default=0)
        return max(len('Mount'), rv)

    def calc_devs_width(self):
        """ Return the display width of the device column."""
        rv = max((len(x.dev) for x in self.rows), default=0)
        return max(len('Device'), rv)

    def _add_confirm_lines(self, win):
        """ Draw the active inline prompt as ordinary body lines. """
        for text, attr in self._confirm_lines():
            win.add_body(text, attr=attr)

    def _add_row(self, win, row, widths, confirming):
        """ Add one rendered listing row to the window. """
        mounts_width, devs_width, path_width = widths
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
        if confirming and row.path == self.confirm.identity:
            self._add_confirm_lines(win)

    def refresh_info(self, body=None):
        """ Redraw the listing, optionally replacing the body with messages.

        `body` is used for transient output (errors, confirmations) that
        should occupy the body area instead of the subvolume rows.
        """

        win = self.win
        self._refresh_if_dirty()
        path_width = self.calc_path_width()
        mounts_width = self.calc_mounts_width()
        devs_width = self.calc_devs_width()
        widths = (mounts_width, devs_width, path_width)

        win.add_fancy_header('MY-SNAPS  [s]:+snap [d]:-subvol [r]eplace-all'
             + ' [a]dd-all [u]sage [f]reshen [x]:exit [?]:help',
            mode='Underline')
        for dev_ns in self.devs.values():
            win.add_header(f'df: {dev_ns.diskfree}', attr=cs.A_DIM)
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

        # An active inline prompt is drawn as a normal body line, so the
        # listing stays visible instead of being covered by a dialog.
        confirming = self.confirm.active
        if confirming and not self.confirm.identity:
            self._add_confirm_lines(win)
        for row in self.rows:
            self._add_row(win, row, widths, confirming)


def parse_args(argv=None):
    """ Parse the command line before anything asks for root.

    `--help`/`--version` and usage errors therefore never trigger sudo, which
    keeps `pip --user` installations usable and avoids a password prompt for
    a command that does no work.
    """
    parser = argparse.ArgumentParser(prog='my-snaps')
    parser.add_argument('--version', action='version',
            version=f'%(prog)s {version_string()}')
    parser.add_argument('-s', '--add-snap-max', type=int, default=None,
            help='add snapshots kept per subvol/label, exactly N '
                 f'[1<=N<={SANITY_MAX_SNAPS}]')
    parser.add_argument('-k', '--keep', type=int, default=DEFAULT_KEEP_SNAPS,
            help='cap for a (add-all) in the TUI '
                 f'[1<=N<={SANITY_MAX_SNAPS}] (default: %(default)s)')
    parser.add_argument('-L', '--label', type=str,
            help='add given label to -s snapshots')
    parser.add_argument('-p', '--print', action="store_true",
            help='print the subvolumes/snaps and exit')
    parser.add_argument('--cron', type=str,
            choices=('hourly', 'daily', 'weekly', 'monthly'),
            help='install a periodic snapshot anacron job')
    parser.add_argument('--DB', action="store_true", dest='db',
            help='add some debugging output')
    opts = parser.parse_args(argv)
    if opts.cron:
        if not opts.label:
            opts.label = '=' + opts.cron.capitalize()
        if opts.add_snap_max is None:
            defaults={'hourly': 2, 'daily': 2, 'weekly': 2, 'monthly':1}
            opts.add_snap_max = defaults[opts.cron]
        opts.print = False
    if opts.add_snap_max is None:
        opts.add_snap_max = 0
    elif not 1 <= opts.add_snap_max <= SANITY_MAX_SNAPS:
        # A clamp used to turn `-s 20` into 8, which deletes snapshots the
        # operator asked to keep.  Refuse the typo instead, before the root
        # re-exec and before anything is touched.
        parser.error('argument -s/--add-snap-max: must be between 1 and '
                     f'{SANITY_MAX_SNAPS}, got {opts.add_snap_max}')
    if not 1 <= opts.keep <= SANITY_MAX_SNAPS:
        parser.error('argument -k/--keep: must be between 1 and '
                     f'{SANITY_MAX_SNAPS}, got {opts.keep}')
    return opts

def main(argv=None):
    """ Parse arguments, re-exec as root, then build and run the tool."""
    opts = parse_args(argv)
    # Every action that survives parsing (window, -p, -s, --cron) mounts
    # BTRFS devices under /tmp or writes /etc, so this is the first point at
    # which root is genuinely required.
    reexec_as_root('my_snaps.main', argv)

    BTRFS.current = BTRFS(opts)
    BTRFS.current.umount_tmps()

def run():
    """ Entry point: leave the terminal usable whatever `main()` raises. """
    try:
        main()
    except KeyboardInterrupt:
        if BTRFS.current and BTRFS.current.win:
            BTRFS.current.stop_curses()
        print('\n   OK, QUITTING NOW\n')
        sys.exit(0)
    except BTRFSError as exce:
        if BTRFS.current and BTRFS.current.win:
            BTRFS.current.stop_curses()
        print(f'ERROR: {exce}', file=sys.stderr)
        sys.exit(1)
    # The final catch-all is the point of this handler: the TUI must restore
    # the terminal and report what happened instead of dying inside curses.
    except Exception as exce:  # pylint: disable=broad-exception-caught
        if BTRFS.current and BTRFS.current.win:
            BTRFS.current.stop_curses()
        print("exception:", str(exce))
        print(traceback.format_exc())
        sys.exit(15)

if __name__ == "__main__":
    run()
