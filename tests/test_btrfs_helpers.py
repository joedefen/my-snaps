"""Tests for small, pure BTRFS helpers that do not touch the filesystem."""
from __future__ import annotations

from my_snaps.main import BTRFS


def test_get_label():
    assert BTRFS._get_label("eos@root.2024-01-10-174732=Update") == "=Update"
    assert BTRFS._get_label("eos@root.2024-01-10-174732") == ""
    assert BTRFS._get_label("=Daily") == "=Daily"


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
