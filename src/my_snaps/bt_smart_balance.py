#!/usr/bin/env python3
""" Tool to start btrfs filesystem balance if needed """
# pylint: disable=invalid-name,too-many-instance-attributes
import os
import sys
import subprocess
import re

class BtSmartBalance:
    """ Methods to do BTRFS balancing when needed """
    def __init__(self, options):
        self.opts = options # gets: mount_point, allocated_pct_min, wasted_pct_min
        self.allocated_pct, self.wasted_pct = 0, 0 # computed actual
        self.device_size, self.allocated, self.used = 0, 0, 0
        self.do_balance = False

    def get_bt_usage(self):
        """Retrieve Btrfs filesystem usage details."""
        try:
            result = subprocess.run(
                f'sudo btrfs filesystem usage {self.opts.mount_point}'.split(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, check=True)
            return result.stdout
        except subprocess.CalledProcessError as e:
            print(f"Error retrieving usage data: {e}")
            return None

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
        self.allocated_pct = int(round(100.0 * self.allocated / self.device_size, 0))
        self.wasted_pct = int(round(100.0 * (self.allocated - self.used) / self.device_size, 0))

        self.do_balance = bool(self.allocated_pct >= self.opts.allocated_pct_min
                               or self.wasted_pct >= self.opts.wasted_pct_min)
        return self.do_balance

    def balance_filesystem(self):
        """Perform the Btrfs balance operation."""
        try:
            cmd = f'sudo btrfs balance start -dusage={self.opt.dusage}'
            cmd = f' --bg {self.opts.mount_point}'
            subprocess.run(cmd.split(), check=True)
            print(f'LAUNCHED: {cmd}')
        except subprocess.CalledProcessError as e:
            print(f"Error starting balance: {e}")

    def main_loop(self):
        """ Logic when run as a program """
        usage_data = self.get_bt_usage()

        if usage_data:
            self.parse_usage_data(usage_data)
            if self.should_balance():
                self.balance_filesystem()
            else:
                print("No balance needed based on current usage data.")
            print(f'BTRFS stats (GiB): used={self.used} allocated={self.allocated}',
                  f'device_size={self.device_size}')
            print(f'    tests:  wasted={self.wasted_pct}% [min={self.opts.wasted_pct_min}%]',
                  f'OR allocated={self.allocated_pct}% [min={self.opts.allocated_pct_min}%]')
        else:
            print("Failed to retrieve filesystem usage data.")

    def install_cron_job(self):
        """ Add/replace an anacron job for scheduled snapshots """
        dirname = '/etc/cron.weekly'
        if not os.path.isdir(dirname):
            print(f'ERROR: {dirname!r} does not exist')
            sys.exit(-1)
        filename = os.path.join(dirname, 'bt-smart-balance-job')
        text = '#!/bin/bash\n'
        text += f'( date; {sys.executable} {os.path.abspath(__file__)}'
        text += f' -a{self.opts.allocated_pct_min}'
        text += f' -w{self.opts.wasted_pct_min} -m{self.opts.mount_point!r}'
        text += ') >/tmp/bt-smart-balance-job.txt 2>&1\n'
        with open(filename, mode='w', encoding='utf-8') as f:
            f.write(text)
        os.chmod(filename, 0o755)
        print(f'OK: to {filename!r}, wrote:\n{text}')

def rerun_module_as_root(module_name):
    """ rerun using the module name """
    if os.geteuid() != 0: # Re-run the script with sudo
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        vp = ['sudo', sys.executable, '-m', module_name] + sys.argv[1:]
        os.execvp('sudo', vp)

def run():
    import argparse
    rerun_module_as_root('my_snaps.bt_smart_balance')

    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--allocated-pct-min', type=int, default=70,
            help='min allocated percent to balance [0<=val<=99, dflt=70]')
    parser.add_argument('-d', '--dusage', type=int, default=20,
            help='pass thru to balance (less is more aggressive) [0<=val<=99,dflt=20]')
    parser.add_argument('-w', '--wasted-pct-min', type=int, default=7,
            help='min wasted percent to balance [0<=val<=99,dflt=7]')
    parser.add_argument('-m', '--mount-point', type=str, default='/',
            help='BTRFS mount point [dflt=/]')
    parser.add_argument('-i', '--install-anacron-job', action="store_true",
            help='creates a script in /etc/cron.weekly with current args')

    opts = parser.parse_args()
    opts.allocated_pct_min = max(0, min(99, opts.allocated_pct_min))
    opts.wasted_pct_min = max(0, min(99, opts.wasted_pct_min))
    opts.dusage = max(0, min(99, opts.dusage))

    tool = BtSmartBalance(options=opts)
    if opts.install_anacron_job:
        tool.install_cron_job()
    else:
        tool.main_loop()

if __name__ == "__main__":
    run()
