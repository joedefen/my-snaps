"""Regression tests for BTRFS._slurp_command().

These tests do not touch btrfs, mounts, or require root. They lock in the
contract that a command's real exit code is returned and that stdout/stderr
are captured without deadlocking.
"""
from __future__ import annotations

import shlex
import sys

import pytest

from my_snaps.main import BTRFS


@pytest.fixture
def btrfs():
    """A BTRFS instance with only the state _slurp_command() needs."""
    obj = object.__new__(BTRFS)
    obj.DB = False
    return obj


def python_command(code: str) -> str:
    """Build a shell command that runs Python code under the test interpreter."""
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}"


@pytest.mark.parametrize("exit_code", [0, 1, 42])
def test_exit_code_is_propagated(btrfs, exit_code):
    command = python_command(f"raise SystemExit({exit_code})")
    _out, _err, code = btrfs._slurp_command(command)
    assert code == exit_code


def test_stdout_stderr_and_failure_code_are_captured(btrfs):
    command = python_command(
        'import sys; print("out"); print("err", file=sys.stderr); '
        "raise SystemExit(7)"
    )
    out, err, code = btrfs._slurp_command(command)
    assert out == ["out"]
    assert err == ["err"]
    assert code == 7


def test_large_output_does_not_deadlock(btrfs):
    command = python_command("print('x' * 1_000_000)")
    out, err, code = btrfs._slurp_command(command)
    assert code == 0
    assert err == []
    assert len(out) == 1
    assert len(out[0]) == 1_000_000


def test_non_utf8_output_does_not_raise(btrfs):
    command = python_command('import sys; sys.stdout.buffer.write(b"\\xff\\xfe")')
    out, err, code = btrfs._slurp_command(command)
    assert code == 0
    assert err == []
    assert len(out) == 1
    assert "\ufffd" in out[0]
