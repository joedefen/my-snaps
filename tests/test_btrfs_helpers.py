"""Tests for small, pure BTRFS helpers that do not touch the filesystem."""
from __future__ import annotations

from types import SimpleNamespace

from my_snaps.main import BTRFS


def test_get_label():
    assert BTRFS._get_label("eos@root.2024-01-10-174732=Update") == "=Update"
    assert BTRFS._get_label("eos@root.2024-01-10-174732") == ""
    assert BTRFS._get_label("=Daily") == "=Daily"


def test_width_helpers_tolerate_an_empty_row_list():
    # `--print` can run before any row exists; max() used to raise there.
    inst = object.__new__(BTRFS)
    inst.rows = []
    assert inst.calc_path_width() == len("Subvolume")
    assert inst.calc_mounts_width() == len("Mount")
    assert inst.calc_devs_width() == len("Device")


def test_width_helpers_use_the_longest_value():
    inst = object.__new__(BTRFS)
    inst.rows = [SimpleNamespace(path="/a/very/long/path", mount="/mnt",
                                 dev="/dev/nvme0n1p2")]
    assert inst.calc_path_width() == len("/a/very/long/path")
    assert inst.calc_mounts_width() == len("Mount")
    assert inst.calc_devs_width() == len("/dev/nvme0n1p2")


def test_init_subvol_ns_defaults():
    ns = BTRFS.init_subvol_ns(dev="sda1", path="/root", ident="256")

    assert ns.dev == "sda1"
    assert ns.path == "/root"
    assert ns.ident == "256"
    assert ns.parent is None
    assert ns.size is None
    assert ns.depth == 0
    assert ns.mount == ""
    assert ns.snaps == []
    assert ns.label_groups == {}
    assert ns.children == []
    assert ns.snap_label == ""
    assert ns.snap_of is None
    assert ns.ago_str == ""
