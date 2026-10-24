"""Regression tests for parsing `btrfs filesystem show` output.

These tests are pure: no btrfs command, mount or root privilege is needed.
They lock in that unlabelled filesystems keep an empty label instead of
raising `UnboundLocalError` or inheriting the previous filesystem's label.
"""
from __future__ import annotations

from my_snaps.my_restore import parse_filesystems

# Realistic `btrfs filesystem show` output, two filesystems, first unlabelled.
TWO_FILESYSTEMS = """\
Label: none  uuid: 8f60fc2f-872d-4327-aff9-34c4c4cefde7
\tTotal devices 1 FS bytes used 15.00GiB
\tdevid    1 size 100.00GiB used 20.00GiB path /dev/nvme0n1p2

Label: 'btrfs-common'  uuid: 11111111-2222-3333-4444-555555555555
\tTotal devices 1 FS bytes used 1.00GiB
\tdevid    1 size 50.00GiB used 2.00GiB path /dev/sda1
"""


def test_unlabelled_filesystem_is_not_an_error():
    entries = parse_filesystems(TWO_FILESYSTEMS)
    assert [(e.label, e.path) for e in entries] == [
        ("", "/dev/nvme0n1p2"),
        ("btrfs-common", "/dev/sda1"),
    ]


def test_label_does_not_leak_into_the_next_filesystem():
    output = """\
Label: 'first'  uuid: 1111
\tdevid    1 size 10.00GiB used 1.00GiB path /dev/sda1

Label: none  uuid: 2222
\tdevid    1 size 10.00GiB used 1.00GiB path /dev/sdb1

Label: 'third'  uuid: 3333
\tdevid    1 size 10.00GiB used 1.00GiB path /dev/sdc1
"""
    assert [(e.label, e.path) for e in parse_filesystems(output)] == [
        ("first", "/dev/sda1"),
        ("", "/dev/sdb1"),
        ("third", "/dev/sdc1"),
    ]


def test_label_with_spaces_is_preserved():
    output = "Label: 'my data'  uuid: 1111\n\tdevid 1 size 1.00GiB path /dev/sda1\n"
    assert [(e.label, e.path) for e in parse_filesystems(output)] == [
        ("my data", "/dev/sda1")]


def test_unquoted_label_is_accepted():
    output = "Label: plain  uuid: 1111\n\tdevid 1 size 1.00GiB path /dev/sda1\n"
    assert parse_filesystems(output)[0].label == "plain"


def test_multiple_devices_share_their_filesystem_label():
    output = """\
Label: 'raid1'  uuid: 1111
\tTotal devices 2 FS bytes used 1.00GiB
\tdevid    1 size 10.00GiB used 2.00GiB path /dev/sda1
\tdevid    2 size 10.00GiB used 2.00GiB path /dev/sdb1
"""
    assert [(e.label, e.path) for e in parse_filesystems(output)] == [
        ("raid1", "/dev/sda1"),
        ("raid1", "/dev/sdb1"),
    ]


def test_empty_output_yields_no_entries():
    assert parse_filesystems("") == []


def test_path_before_any_label_is_reported_unlabelled():
    output = "\tdevid 1 size 1.00GiB path /dev/sda1\n"
    assert parse_filesystems(output)[0].label == ""
