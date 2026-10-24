#!/usr/bin/env python3
""" Tool to start btrfs filesystem balance if needed """
# pylint: disable=invalid-name,too-many-instance-attributes
import os
import re
import shlex
import subprocess
import sys

from my_snaps.MyUtils import reexec_as_root, version_string

#: Smallest ``-dusage`` value ever handed to btrfs. ``btrfs balance
#: -dusage=0`` means "balance every data chunk", i.e. an unbounded full
#: balance, so zero (and negatives) are clamped up to this value.
MIN_DUSAGE = 1

class BtSmartBalance:
    """ Methods to do BTRFS balancing when needed """
    def __init__(self, options):
        self.opts = options # gets: mount_point, allocated_pct_min,
                            # wasted_pct_min, dusage
        self.allocated_pct, self.wasted_pct = 0, 0 # computed actual
        self.device_size, self.allocated, self.used = 0, 0, 0
        self.do_balance = False

    def _run(self, args):
        """ Run ``btrfs <args>`` and return the CompletedProcess.

        An argv list (never a shell string) keeps the mount point intact and
        makes the command reproducible; ``sudo`` is only prepended when we
        were not already started as root.
        """
        argv = ['btrfs'] + list(args)
        if os.geteuid() != 0:
            argv.insert(0, 'sudo')
        return subprocess.run(argv, capture_output=True, text=True,
                              check=False)

    def get_bt_usage(self):
        """Retrieve Btrfs filesystem usage details (None on failure)."""
        try:
            result = self._run(['filesystem', 'usage', self.opts.mount_point])
        except OSError as e: # e.g. btrfs or sudo is not installed
            print(f'Error running btrfs: {e}', file=sys.stderr)
            return None
        if result.returncode:
            print('Error retrieving usage data:', result.stderr.strip(),
                  file=sys.stderr)
            return None
        return result.stdout

    def parse_usage_data(self, usage_data):
        """Parse the relevant data from the Btrfs usage output."""
        def parse_value(pattern, data):
            # ignore anything less and GiB and assume not greater than TiB
            pattern += r':\s+([\d.]+)([GT])'
            match = re.search(pattern, data)
            if match:
                rv = float(match.group(1))
                if match.group(2) == 'T':
                    rv *= 1024
                return round(rv, 2)
            return 0.0

        self.device_size = parse_value(r'Device size', usage_data)
        self.allocated = parse_value(r'Device allocated', usage_data)
        self.used = parse_value(r'Used', usage_data)

    def should_balance(self):
        """Determine if a balance is necessary based on thresholds."""
        if self.device_size <= 0:
            print('Cannot evaluate usage: device size is unknown (0 GiB).',
                  file=sys.stderr)
            self.allocated_pct = self.wasted_pct = 0
            self.do_balance = False
            return False

        self.allocated_pct = int(round(100.0 * self.allocated / self.device_size, 0))
        self.wasted_pct = int(round(100.0 * (self.allocated - self.used) / self.device_size, 0))

        self.do_balance = bool(self.allocated_pct >= self.opts.allocated_pct_min
                               or self.wasted_pct >= self.opts.wasted_pct_min)
        return self.do_balance

    def balance_args(self):
        """ Build the argv btrfs needs for the balance implied by the options. """
        dusage = max(MIN_DUSAGE, min(99, int(self.opts.dusage)))
        return ['balance', 'start', '--bg', f'-dusage={dusage}',
                self.opts.mount_point]

    def is_balance_running(self):
        """ True when btrfs reports a balance already in progress here. """
        try:
            result = self._run(['balance', 'status', self.opts.mount_point])
        except OSError:
            return False
        if result.returncode:
            return False
        return 'No balance found' not in (result.stdout + result.stderr)

    def balance_filesystem(self):
        """Start a bounded, background Btrfs balance; False on failure."""
        if self.is_balance_running():
            print(f'NOTE: a balance is already running on '
                  f'{self.opts.mount_point!r}; not starting another.')
            return True

        args = self.balance_args()
        try:
            result = self._run(args)
        except OSError as e:
            print(f'Error running btrfs: {e}', file=sys.stderr)
            return False
        if result.returncode:
            print(f'Error starting balance({result.returncode}):',
                  result.stderr.strip(), file=sys.stderr)
            return False
        print(f'LAUNCHED: btrfs {" ".join(args)}')
        return True

    def main_loop(self):
        """ Logic when run as a program; returns a process exit code. """
        usage_data = self.get_bt_usage()
        if not usage_data:
            print('Failed to retrieve filesystem usage data.', file=sys.stderr)
            return 1

        self.parse_usage_data(usage_data)
        needed = self.should_balance()
        print(f'BTRFS stats (GiB): used={self.used} allocated={self.allocated}',
              f'device_size={self.device_size}')
        print(f'    tests:  wasted={self.wasted_pct}% [min={self.opts.wasted_pct_min}%]',
              f'OR allocated={self.allocated_pct}% [min={self.opts.allocated_pct_min}%]')
        if not needed:
            print('No balance needed based on current usage data.')
            return 0
        return 0 if self.balance_filesystem() else 1

    def cron_script(self):
        """ Return the shell script used by the weekly cron job. """
        text = '#!/bin/bash\n'
        text += f'( date; {shlex.quote(sys.executable)}'
        text += f' {shlex.quote(os.path.abspath(__file__))}'
        text += f' -a {self.opts.allocated_pct_min}'
        text += f' -w {self.opts.wasted_pct_min}'
        text += f' -d {max(MIN_DUSAGE, self.opts.dusage)}'
        text += f' -m {shlex.quote(self.opts.mount_point)}'
        text += ') >/tmp/bt-smart-balance-job.txt 2>&1\n'
        return text

    def install_cron_job(self):
        """ Add/replace an anacron job for scheduled snapshots """
        dirname = '/etc/cron.weekly'
        if not os.path.isdir(dirname):
            print(f'ERROR: {dirname!r} does not exist')
            sys.exit(-1)
        filename = os.path.join(dirname, 'bt-smart-balance-job')
        text = self.cron_script()
        with open(filename, mode='w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(filename, 0o755)
        print(f'OK: to {filename!r}, wrote:\n{text}')

def parse_args(argv=None):
    """ Parse the command line before anything asks for root. """
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--version', action='version',
            version=f'%(prog)s {version_string()}')
    parser.add_argument('-a', '--allocated-pct-min', type=int, default=70,
            help='min allocated percent to balance [0<=val<=99, dflt=70]')
    parser.add_argument('-d', '--dusage', type=int, default=20,
            help='btrfs -dusage filter (less is more aggressive)'
                 f' [{MIN_DUSAGE}<=val<=99, dflt=20]')
    parser.add_argument('-w', '--wasted-pct-min', type=int, default=7,
            help='min wasted percent to balance [0<=val<=99,dflt=7]')
    parser.add_argument('-m', '--mount-point', type=str, default='/',
            help='BTRFS mount point [dflt=/]')
    parser.add_argument('-i', '--install-anacron-job', action="store_true",
            help='creates a script in /etc/cron.weekly with current args')

    opts = parser.parse_args(argv)
    opts.allocated_pct_min = max(0, min(99, opts.allocated_pct_min))
    opts.wasted_pct_min = max(0, min(99, opts.wasted_pct_min))
    if opts.dusage < MIN_DUSAGE:
        parser.error(f'-d/--dusage must be >= {MIN_DUSAGE}; '
                     '0 would balance every data chunk (full balance)')
    opts.dusage = min(99, opts.dusage)
    if not os.path.isdir(opts.mount_point):
        parser.error(f'mount point is not a directory: {opts.mount_point!r}')
    return opts

def run(argv=None):
    opts = parse_args(argv)
    # `_run()` prepends sudo to each btrfs command, so a normal check or
    # balance needs no whole-process re-exec.  Only installing the job writes
    # to /etc, and that is the sole action that must itself be root.
    if opts.install_anacron_job:
        reexec_as_root('my_snaps.bt_smart_balance', argv)
    tool = BtSmartBalance(options=opts)
    if opts.install_anacron_job:
        tool.install_cron_job()
    else:
        sys.exit(tool.main_loop())

if __name__ == "__main__":
    run()
