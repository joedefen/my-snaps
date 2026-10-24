"""Tests for bt_smart_balance command construction and safety guards.

The real ``btrfs`` binary is never executed: ``BtSmartBalance._run()`` is
replaced with a fake that records the argv lists it receives.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import mock_open

import my_snaps.bt_smart_balance as bsb
import pytest
from my_snaps.bt_smart_balance import MIN_DUSAGE, BtSmartBalance, parse_args


def make_tool(**overrides):
    """Build a tool with the same defaults as the CLI."""
    opts = SimpleNamespace(
        mount_point="/",
        allocated_pct_min=70,
        wasted_pct_min=7,
        dusage=20,
    )
    for key, value in overrides.items():
        setattr(opts, key, value)
    return BtSmartBalance(options=opts)


class FakeBtrfs:
    """Fake ``_run`` that answers ``balance status`` and records everything."""

    def __init__(self, status="No balance found on '/'\n", status_rc=0,
                 start_rc=0, start_stderr=""):
        self.status = status
        self.status_rc = status_rc
        self.start_rc = start_rc
        self.start_stderr = start_stderr
        self.calls = []

    def __call__(self, args):
        args = list(args)
        self.calls.append(args)
        if args[:2] == ["balance", "status"]:
            return SimpleNamespace(returncode=self.status_rc,
                                   stdout=self.status, stderr="")
        return SimpleNamespace(returncode=self.start_rc,
                               stdout="", stderr=self.start_stderr)

    @property
    def start_calls(self):
        return [call for call in self.calls if call[1:2] == ["start"]]


def test_balance_args_is_a_complete_btrfs_command():
    tool = make_tool(mount_point="/mnt/data")
    assert tool.balance_args() == [
        "balance", "start", "--bg", "-dusage=20", "/mnt/data"]


@pytest.mark.parametrize("dusage", [0, -5])
def test_balance_args_never_allows_an_unbounded_balance(dusage):
    tool = make_tool(dusage=dusage)
    assert tool.balance_args()[-2] == f"-dusage={MIN_DUSAGE}"


def test_balance_args_clamps_above_99():
    tool = make_tool(dusage=500)
    assert tool.balance_args()[-2] == "-dusage=99"


def test_balance_filesystem_starts_the_expected_command(monkeypatch, capsys):
    tool = make_tool()
    fake = FakeBtrfs()
    monkeypatch.setattr(tool, "_run", fake)

    assert tool.balance_filesystem() is True
    assert fake.calls[0] == ["balance", "status", "/"]
    assert fake.start_calls == [tool.balance_args()]
    assert "LAUNCHED: btrfs balance start --bg -dusage=20 /" in capsys.readouterr().out


def test_balance_filesystem_does_not_start_a_second_balance(monkeypatch, capsys):
    tool = make_tool()
    fake = FakeBtrfs(status="Balance on '/' is running, 12% left\n")
    monkeypatch.setattr(tool, "_run", fake)

    assert tool.balance_filesystem() is True
    assert fake.start_calls == []
    assert "already running" in capsys.readouterr().out


def test_balance_filesystem_reports_start_failure(monkeypatch, capsys):
    tool = make_tool()
    fake = FakeBtrfs(start_rc=1, start_stderr="ERROR: not a btrfs filesystem")
    monkeypatch.setattr(tool, "_run", fake)

    assert tool.balance_filesystem() is False
    captured = capsys.readouterr()
    assert "Error starting balance(1)" in captured.err
    assert "not a btrfs filesystem" in captured.err


def test_balance_filesystem_reports_missing_binary(monkeypatch, capsys):
    tool = make_tool()

    def boom(_args):
        raise FileNotFoundError("btrfs")

    monkeypatch.setattr(tool, "_run", boom)
    assert tool.balance_filesystem() is False
    assert "btrfs" in capsys.readouterr().err


def test_should_balance_does_not_divide_by_zero(capsys):
    tool = make_tool()
    assert tool.should_balance() is False
    assert "device size is unknown" in capsys.readouterr().err


@pytest.mark.parametrize("allocated,used,expected", [
    (80.0, 79.0, True),   # allocated 80% >= 70%
    (50.0, 30.0, True),   # wasted 20% >= 7%
    (50.0, 49.0, False),  # neither threshold met
])
def test_should_balance_thresholds(allocated, used, expected):
    tool = make_tool()
    tool.device_size, tool.allocated, tool.used = 100.0, allocated, used
    assert tool.should_balance() is expected


def test_main_loop_balances_when_needed(monkeypatch):
    tool = make_tool()
    fake = FakeBtrfs()
    monkeypatch.setattr(tool, "_run", fake)
    monkeypatch.setattr(tool, "get_bt_usage", lambda: "usage output")

    def fake_parse(_data):
        tool.device_size, tool.allocated, tool.used = 100.0, 80.0, 70.0

    monkeypatch.setattr(tool, "parse_usage_data", fake_parse)

    assert tool.main_loop() == 0
    assert fake.start_calls == [["balance", "start", "--bg", "-dusage=20", "/"]]


def test_main_loop_skips_balance_when_not_needed(monkeypatch):
    tool = make_tool()
    fake = FakeBtrfs()
    monkeypatch.setattr(tool, "_run", fake)
    monkeypatch.setattr(tool, "get_bt_usage", lambda: "usage output")

    def fake_parse(_data):
        tool.device_size, tool.allocated, tool.used = 100.0, 50.0, 49.0

    monkeypatch.setattr(tool, "parse_usage_data", fake_parse)

    assert tool.main_loop() == 0
    assert fake.calls == []


def test_main_loop_returns_nonzero_when_usage_fails(monkeypatch, capsys):
    tool = make_tool()
    monkeypatch.setattr(tool, "get_bt_usage", lambda: None)

    assert tool.main_loop() == 1
    assert "Failed to retrieve" in capsys.readouterr().err


def test_cron_script_forwards_dusage_and_quotes_mount_point():
    tool = make_tool(mount_point="/mnt/my data", dusage=5)
    script = tool.cron_script()
    assert "-a 70" in script
    assert "-w 7" in script
    assert "-d 5" in script
    assert "'/mnt/my data'" in script


def test_cron_script_clamps_dusage():
    tool = make_tool(dusage=0)
    assert f"-d {MIN_DUSAGE}" in tool.cron_script()


# --- command execution -----------------------------------------------------

def test_run_does_not_prepend_sudo_when_already_root(monkeypatch):
    tool = make_tool()
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(bsb.subprocess, "run", fake_run)
    monkeypatch.setattr(bsb.os, "geteuid", lambda: 0)

    result = tool._run(["filesystem", "usage", "/"])

    assert result.returncode == 0
    assert calls == [(["btrfs", "filesystem", "usage", "/"],
                      {"capture_output": True, "text": True, "check": False})]


def test_run_prepends_sudo_for_a_normal_user(monkeypatch):
    tool = make_tool()
    calls = []
    monkeypatch.setattr(
        bsb.subprocess, "run",
        lambda argv, **kwargs: calls.append(list(argv))
        or SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(bsb.os, "geteuid", lambda: 1000)

    tool._run(["balance", "status", "/"])

    assert calls == [["sudo", "btrfs", "balance", "status", "/"]]


def test_get_bt_usage_returns_stdout(monkeypatch):
    tool = make_tool()
    monkeypatch.setattr(
        tool, "_run",
        lambda _args: SimpleNamespace(returncode=0, stdout="usage",
                                      stderr=""))

    assert tool.get_bt_usage() == "usage"


def test_get_bt_usage_reports_a_failing_command(monkeypatch, capsys):
    tool = make_tool()
    monkeypatch.setattr(
        tool, "_run",
        lambda _args: SimpleNamespace(returncode=2, stdout="",
                                      stderr="not btrfs"))

    assert tool.get_bt_usage() is None
    assert "not btrfs" in capsys.readouterr().err


def test_get_bt_usage_reports_a_missing_binary(monkeypatch, capsys):
    tool = make_tool()

    def boom(_args):
        raise OSError("btrfs: not found")

    monkeypatch.setattr(tool, "_run", boom)

    assert tool.get_bt_usage() is None
    assert "btrfs: not found" in capsys.readouterr().err


def test_parse_usage_data_converts_terabytes_and_ignores_small_units():
    tool = make_tool()

    tool.parse_usage_data(
        "Device size: 10.00GiB\n"
        "Device allocated: 1.50TiB\n"
        "Used: 500.00MiB\n")

    assert tool.device_size == 10.0
    assert tool.allocated == 1536.0
    assert tool.used == 0.0


def test_parse_usage_data_defaults_missing_fields_to_zero():
    tool = make_tool()
    tool.parse_usage_data("Device size: 10.00GiB\n")
    assert tool.allocated == 0.0
    assert tool.used == 0.0


@pytest.mark.parametrize("status,returncode,expected", [
    ("No balance found on '/'\n", 0, False),
    ("Balance on '/' is running\n", 0, True),
    ("", 1, False),
])
def test_is_balance_running(status, returncode, expected, monkeypatch):
    tool = make_tool()
    monkeypatch.setattr(
        tool, "_run",
        lambda _args: SimpleNamespace(returncode=returncode, stdout=status,
                                      stderr=""))

    assert tool.is_balance_running() is expected


def test_is_balance_running_is_false_when_btrfs_is_missing(monkeypatch):
    tool = make_tool()

    def boom(_args):
        raise OSError("btrfs")

    monkeypatch.setattr(tool, "_run", boom)
    assert tool.is_balance_running() is False


def test_install_cron_job_writes_an_executable_script(monkeypatch, capsys):
    tool = make_tool(mount_point="/mnt/my data", dusage=5)
    opened = mock_open()
    chmod_calls = []
    monkeypatch.setattr(bsb.os.path, "isdir", lambda _path: True)
    monkeypatch.setattr("builtins.open", opened)
    monkeypatch.setattr(
        bsb.os, "chmod",
        lambda path, mode: chmod_calls.append((path, mode)))

    tool.install_cron_job()

    opened.assert_called_once_with(
        "/etc/cron.weekly/bt-smart-balance-job", mode="w", encoding="utf-8")
    opened().write.assert_called_once_with(tool.cron_script())
    assert chmod_calls == [
        ("/etc/cron.weekly/bt-smart-balance-job", 0o755)]
    assert "OK: to" in capsys.readouterr().out


def test_install_cron_job_refuses_a_missing_directory(monkeypatch, capsys):
    tool = make_tool()
    monkeypatch.setattr(bsb.os.path, "isdir", lambda _path: False)

    with pytest.raises(SystemExit) as caught:
        tool.install_cron_job()

    assert caught.value.code == -1
    assert "does not exist" in capsys.readouterr().out


# --- argument parsing ------------------------------------------------------

def test_parse_args_clamps_percentages_and_dusage():
    opts = parse_args(["-a", "-5", "-w", "500", "-d", "500"])

    assert opts.allocated_pct_min == 0
    assert opts.wasted_pct_min == 99
    assert opts.dusage == 99


def test_parse_args_refuses_an_unbounded_dusage(capsys):
    with pytest.raises(SystemExit) as caught:
        parse_args(["-d", "0"])

    assert caught.value.code == 2
    assert "full balance" in capsys.readouterr().err


def test_parse_args_refuses_a_missing_mount_point(capsys):
    with pytest.raises(SystemExit) as caught:
        parse_args(["-m", "/definitely/not/a/directory"])

    assert caught.value.code == 2
    assert "not a directory" in capsys.readouterr().err
