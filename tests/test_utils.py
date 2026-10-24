"""Tests for the dependency-free helpers in my_snaps.MyUtils."""
from __future__ import annotations

import re

from my_snaps.MyUtils import ago_str, ago_whence, human, timestamp_str


def test_timestamp_str_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}", timestamp_str())


def test_human():
    assert human(0) == "0.0K"
    assert human(1024) == "1.0K"
    assert human(1024**2) == "1.0M"
    assert human(1024**3) == "1.0G"


def test_ago_str():
    assert ago_str(0) == "0s"
    assert ago_str(59) == "59s"
    assert ago_str(60) == "1m0s"
    assert ago_str(3600) == "1h0m"
    assert ago_str(-90, signed=True) == "-1m30s"


def test_ago_whence():
    assert ago_whence("eos@root.2024-01-10-174732=Update")
    assert ago_whence("no-timestamp") == ""
