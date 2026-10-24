"""Tests for the fast (non-probing) block-device scan.

Startup used to run `blkid`, which opens every block device and runs the full
signature detection chain; on the development machine an idle optical drive
alone cost ~17 of the ~18 seconds.  Devices are now described from
`lsblk -P` output (sysfs + udev, no media I/O), with `blkid` only as a
fallback.
"""
from __future__ import annotations

from types import SimpleNamespace

from my_snaps.main import BTRFS

###############################################################################
# Output parsing
###############################################################################

def test_parse_lsblk_line():
    rec = BTRFS.parse_lsblk_line(
        'NAME="nvme0n1p2" PATH="/dev/nvme0n1p2" TYPE="part"'
        ' FSTYPE="btrfs" LABEL="btrfs-common" UUID="8f60"')

    assert rec['NAME'] == 'nvme0n1p2'
    assert rec['PATH'] == '/dev/nvme0n1p2'
    assert rec['TYPE'] == 'part'
    assert rec['FSTYPE'] == 'btrfs'
    assert rec['LABEL'] == 'btrfs-common'
    assert rec['UUID'] == '8f60'


def test_parse_lsblk_line_keeps_empty_values():
    rec = BTRFS.parse_lsblk_line('NAME="sdd" FSTYPE="" LABEL="" UUID=""')

    assert rec['FSTYPE'] == ''
    assert rec['LABEL'] == ''


def test_parse_blkid_line_maps_type_to_fstype():
    rec = BTRFS.parse_blkid_line(
        '/dev/nvme0n1p2: LABEL="btrfs-common" UUID="8f60"'
        ' BLOCK_SIZE="4096" TYPE="btrfs" PARTUUID="02b5"')

    assert rec['NAME'] == 'nvme0n1p2'
    assert rec['PATH'] == '/dev/nvme0n1p2'
    assert rec['FSTYPE'] == 'btrfs'
    assert 'TYPE' not in rec


def test_parse_mount_line_btrfs():
    parsed = BTRFS.parse_mount_line(
        '/dev/nvme0n1p2 /.snapshots btrfs'
        ' rw,noatime,subvolid=2818,subvol=/deb@snapshots 0 0')

    assert parsed == ('/dev/nvme0n1p2', '/.snapshots', 'btrfs',
                      '2818', '/deb@snapshots')


def test_parse_mount_line_non_btrfs_and_garbage():
    assert BTRFS.parse_mount_line('/dev/sda1 /my-win ext4 rw 0 0') == (
        '/dev/sda1', '/my-win', 'ext4', None, None)
    assert BTRFS.parse_mount_line('garbage') is None


def test_parse_subvol_list():
    lines = ['ID 2814 gen 123456 top level 5 path deb@root',
             'ID 2818 gen 999 top level 2814 path deb@root/deb@snapshots',
             'ID 3000 gen 999 top level 2814']

    assert BTRFS.parse_subvol_list(lines) == [
        ('2814', '5', '/deb@root'),
        ('2818', '2814', '/deb@root/deb@snapshots'),
    ]


###############################################################################
# Device scan
###############################################################################

def test_scan_block_devices_falls_back_to_blkid():
    self = BTRFS.__new__(BTRFS)
    self.dev_records = []
    calls = []

    def slurp(cmd):
        calls.append(cmd)
        if cmd.startswith('lsblk'):
            return [], [], 1  # e.g. lsblk not installed
        return ['/dev/sda1: LABEL="x" TYPE="btrfs"'], [], 0

    self._slurp_command = slurp

    recs = self._scan_block_devices()

    assert calls[0].startswith('lsblk')
    assert calls[1] == 'blkid'
    assert recs == [{'LABEL': 'x', 'FSTYPE': 'btrfs',
                     'PATH': '/dev/sda1', 'NAME': 'sda1'}]


def test_scan_block_devices_scans_only_once():
    self = BTRFS.__new__(BTRFS)
    self.dev_records = []
    calls = []

    def slurp(cmd):
        calls.append(cmd)
        return ['NAME="sda1" PATH="/dev/sda1" FSTYPE="btrfs"'], [], 0

    self._slurp_command = slurp

    self._scan_block_devices()
    self._scan_block_devices()

    assert len(calls) == 1


def test_load_devs_only_keeps_btrfs_devices():
    self = BTRFS.__new__(BTRFS)
    self.DB = False
    self.dev_records = [
        {'NAME': 'sda1', 'PATH': '/dev/sda1', 'TYPE': 'part',
         'FSTYPE': 'ext4', 'LABEL': '', 'UUID': 'e'},
        # an optical drive must never be scanned (it can block for seconds)
        {'NAME': 'sr0', 'PATH': '/dev/sr0', 'TYPE': 'rom',
         'FSTYPE': 'udf', 'LABEL': 'D3', 'UUID': 'u'},
        {'NAME': 'nvme0n1p2', 'PATH': '/dev/nvme0n1p2', 'TYPE': 'part',
         'FSTYPE': 'btrfs', 'LABEL': 'btrfs-common', 'UUID': 'b'},
    ]
    self._slurp_command = lambda cmd: (['Filesystem  Size', 'df-line'], [], 0)

    self._load_devs()

    assert set(self.devs) == {'nvme0n1p2'}
    dev_ns = self.devs['nvme0n1p2']
    assert dev_ns.device_path == '/dev/nvme0n1p2'
    assert dev_ns.label == 'btrfs-common'
    assert dev_ns.uuid == 'b'
    assert dev_ns.diskfree == 'df-line'


###############################################################################
# Subvolume tree
###############################################################################

def test_init_dev_subvols_builds_tree():
    self = BTRFS.__new__(BTRFS)
    self.tmp_dir = '/tmp/.btrfs'
    dev_ns = SimpleNamespace(dev='nvme0n1p2')

    self._init_dev_subvols(dev_ns, [
        ('2814', '5', '/deb@root'),
        ('2818', '5', '/deb@snapshots'),
        ('2900', '2818', '/deb@snapshots/snap.2024-01-10-174732'),
    ])

    assert [ns.path for ns in dev_ns.subvols] == ['/deb@root', '/deb@snapshots']
    snap = dev_ns.idents['2900']
    assert snap.depth == 1
    assert snap.parent == '2818'
    assert dev_ns.paths['/deb@snapshots'].children == [snap]
    assert dev_ns.tmp_path == '/tmp/.btrfs/nvme0n1p2'
