"""Tests for the dependency-free helpers in my_snaps.MyUtils."""
from __future__ import annotations

import re
from importlib.metadata import PackageNotFoundError

import my_snaps.MyUtils as my_utils
import pytest
from my_snaps.MyUtils import ago_str, ago_whence, human, reexec_as_root, timestamp_str, version_string


def test_timestamp_str_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}-\d{6}", timestamp_str())


def test_human():
    assert human(0) == "0.0K"
    assert human(1024) == "1.0K"
    assert human(1024**2) == "1.0M"
    assert human(1024**3) == "1.0G"


def test_human_keeps_a_value_in_one_unit_until_it_no_longer_fits():
    # The old 99.95 cutoff printed 102400 bytes as 0.1M; it must stay 100.0K.
    assert human(99.9 * 1024) == "99.9K"
    assert human(100 * 1024) == "100.0K"
    assert human(1024**2 - 1) == "1.0M"


def test_ago_str():
    assert ago_str(0) == "0s"
    assert ago_str(59) == "59s"
    assert ago_str(60) == "1m0s"
    assert ago_str(3600) == "1h0m"
    assert ago_str(-90, signed=True) == "-1m30s"


def test_ago_whence():
    assert ago_whence("eos@root.2024-01-10-174732=Update")
    assert ago_whence("no-timestamp") == ""


def test_human_uses_terabytes_for_large_values():
    assert human(1024**4) == "1.0T"
    assert human(1024**5) == "1024.0T"


def test_ago_whence_ignores_an_invalid_timestamp(monkeypatch):
    # The date matches the regex but not the calendar.
    assert ago_whence("eos@root.2024-99-99-999999=Update") == ""


def test_version_string_falls_back_when_the_package_is_not_installed(
        monkeypatch):
    def missing(_name):
        raise PackageNotFoundError("my-snaps")

    monkeypatch.setattr(my_utils, "version", missing)
    assert version_string() == "unknown"


def test_reexec_reports_a_missing_sudo(monkeypatch, capsys):
    monkeypatch.setattr(my_utils.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(my_utils.os, "chdir", lambda _path: None)

    def no_sudo(*_args):
        raise OSError("sudo: not found")

    monkeypatch.setattr(my_utils.os, "execvp", no_sudo)

    with pytest.raises(SystemExit) as caught:
        reexec_as_root("my_snaps.main", ["-p"])

    assert caught.value.code == 1
    err = capsys.readouterr().err
    assert "cannot re-run as root" in err
    assert "prefix it with sudo" in err


def test_reexec_does_not_chdir_outside_a_source_checkout(monkeypatch):
    monkeypatch.setattr(my_utils.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(my_utils.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(
        my_utils.os, "chdir",
        lambda _path: pytest.fail("chdir must not run outside a checkout"))
    calls = []
    monkeypatch.setattr(
        my_utils.os, "execvp",
        lambda prog, argv: calls.append((prog, argv)))

    reexec_as_root("my_snaps.main", [])

    assert calls == [("sudo", ["sudo", my_utils.sys.executable,
                               "-m", "my_snaps.main"])]
