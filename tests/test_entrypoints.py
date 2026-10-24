"""Regression tests for argument parsing before the root re-exec.

`--help`/`--version`, invalid usage and `my-restore -n` must never invoke
sudo.  Escalation happens only after parsing, and only for actions that
actually change the system.
"""
from __future__ import annotations

from typing import ClassVar

import pytest

import my_snaps.bt_smart_balance as bsb
import my_snaps.main as main_mod
import my_snaps.my_restore as restore_mod
import my_snaps.MyUtils as my_utils


class ReexecSpy:
    """Record re-exec calls and optionally abort like a real execvp()."""

    def __init__(self, abort=False):
        self.calls = []
        self.abort = abort

    def __call__(self, module_name, argv=None):
        self.calls.append((module_name, None if argv is None else list(argv)))
        if self.abort:
            raise SystemExit(0)
        return False


# --- the shared helper -----------------------------------------------------

def test_reexec_is_a_noop_when_already_root(monkeypatch):
    monkeypatch.setattr(my_utils.os, "geteuid", lambda: 0)
    monkeypatch.setattr(my_utils.os, "execvp",
                        lambda *args: pytest.fail("execvp must not be called"))
    assert my_utils.reexec_as_root("my_snaps.main", ["-p"]) is None


def test_reexec_prepends_sudo_and_preserves_arguments(monkeypatch):
    monkeypatch.setattr(my_utils.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(my_utils.os, "chdir", lambda _path: None)
    monkeypatch.setattr(my_utils.sys, "executable", "/usr/bin/python3")
    calls = []
    monkeypatch.setattr(my_utils.os, "execvp",
                        lambda prog, argv: calls.append((prog, argv)))

    my_utils.reexec_as_root("my_snaps.main", ["-p", "--DB"])

    assert calls == [
        ("sudo", ["sudo", "/usr/bin/python3", "-m", "my_snaps.main",
                  "-p", "--DB"]),
    ]


# --- my-snaps --------------------------------------------------------------

def test_my_snaps_help_never_asks_for_root(monkeypatch):
    spy = ReexecSpy()
    monkeypatch.setattr(main_mod, "reexec_as_root", spy)

    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--help"])

    assert exc.value.code == 0
    assert spy.calls == []


def test_my_snaps_version_never_asks_for_root(monkeypatch):
    spy = ReexecSpy()
    monkeypatch.setattr(main_mod, "reexec_as_root", spy)

    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--version"])

    assert exc.value.code == 0
    assert spy.calls == []


def test_my_snaps_bad_usage_never_asks_for_root(monkeypatch):
    spy = ReexecSpy()
    monkeypatch.setattr(main_mod, "reexec_as_root", spy)

    with pytest.raises(SystemExit) as exc:
        main_mod.main(["--not-an-option"])

    assert exc.value.code == 2
    assert spy.calls == []


def test_my_snaps_reexecs_after_a_real_action_is_parsed(monkeypatch):
    spy = ReexecSpy(abort=True)
    monkeypatch.setattr(main_mod, "reexec_as_root", spy)

    with pytest.raises(SystemExit) as exc:
        main_mod.main(["-p"])

    assert exc.value.code == 0
    assert spy.calls == [("my_snaps.main", ["-p"])]


# --- bt-smart-balance ------------------------------------------------------

class FakeTool:
    """Stand-in for BtSmartBalance that never runs btrfs."""

    def __init__(self, options):
        self.options = options
        self.installed = False
        self.looped = False

    def install_cron_job(self):
        self.installed = True

    def main_loop(self):
        self.looped = True
        return 0


def test_bt_smart_balance_help_never_asks_for_root(monkeypatch):
    spy = ReexecSpy()
    monkeypatch.setattr(bsb, "reexec_as_root", spy)

    with pytest.raises(SystemExit) as exc:
        bsb.run(["--help"])

    assert exc.value.code == 0
    assert spy.calls == []


def test_bt_smart_balance_check_does_not_reexec(monkeypatch):
    """A normal run lets _run() sudo only the btrfs commands it needs."""
    spy = ReexecSpy()
    monkeypatch.setattr(bsb, "reexec_as_root", spy)
    monkeypatch.setattr(bsb, "BtSmartBalance", FakeTool)

    with pytest.raises(SystemExit) as exc:
        bsb.run([])

    assert exc.value.code == 0
    assert spy.calls == []


def test_bt_smart_balance_install_reexecs(monkeypatch):
    spy = ReexecSpy(abort=True)
    monkeypatch.setattr(bsb, "reexec_as_root", spy)
    monkeypatch.setattr(bsb, "BtSmartBalance", FakeTool)

    with pytest.raises(SystemExit) as exc:
        bsb.run(["-i"])

    assert exc.value.code == 0
    assert spy.calls == [("my_snaps.bt_smart_balance", ["-i"])]


# --- my-restore ------------------------------------------------------------

class FakeRestore:
    """Stand-in for BtrfsRestore that records how it was built."""

    instances: ClassVar[list] = []

    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.ran = False
        FakeRestore.instances.append(self)

    def main(self):
        self.ran = True


def test_my_restore_help_never_asks_for_root(monkeypatch):
    spy = ReexecSpy()
    monkeypatch.setattr(restore_mod, "reexec_as_root", spy)

    with pytest.raises(SystemExit) as exc:
        restore_mod.run(["--help"])

    assert exc.value.code == 0
    assert spy.calls == []


def test_my_restore_dry_run_never_asks_for_root(monkeypatch):
    spy = ReexecSpy(abort=True)
    monkeypatch.setattr(restore_mod, "reexec_as_root", spy)
    monkeypatch.setattr(restore_mod, "BtrfsRestore", FakeRestore)
    FakeRestore.instances.clear()

    restore_mod.run(["-n"])

    assert spy.calls == []
    assert len(FakeRestore.instances) == 1
    assert FakeRestore.instances[0].dry_run is True
    assert FakeRestore.instances[0].ran is True


def test_my_restore_real_run_reexecs(monkeypatch):
    spy = ReexecSpy(abort=True)
    monkeypatch.setattr(restore_mod, "reexec_as_root", spy)
    monkeypatch.setattr(restore_mod, "BtrfsRestore", FakeRestore)
    FakeRestore.instances.clear()

    with pytest.raises(SystemExit) as exc:
        restore_mod.run([])

    assert exc.value.code == 0
    assert spy.calls == [("my_snaps.my_restore", [])]
    assert FakeRestore.instances == []
