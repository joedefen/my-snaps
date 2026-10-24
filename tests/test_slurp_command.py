"""Regression tests for BTRFS._slurp_command().

These tests do not touch btrfs, mounts, or require root. They lock in the
contract that a command's real exit code is returned and that stdout/stderr
are captured without deadlocking.
"""
from __future__ import annotations

import sys

import pytest

import my_snaps.main as main_module
from my_snaps.main import BTRFS


@pytest.fixture
def btrfs():
    """A BTRFS instance with only the state _slurp_command() needs."""
    obj = object.__new__(BTRFS)
    obj.DB = False
    return obj


def python_command(code: str) -> list[str]:
    """Build an argv list that runs Python code under the test interpreter."""
    return [sys.executable, '-c', code]


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


def test_large_stderr_does_not_deadlock(btrfs):
    """An unread stderr pipe can deadlock just as easily as stdout."""
    command = python_command("import sys; sys.stderr.write('e' * 1_000_000)")
    out, err, code = btrfs._slurp_command(command)
    assert code == 0
    assert out == []
    assert len(err) == 1
    assert len(err[0]) == 1_000_000


def test_wait_is_never_called_before_communicate(btrfs, monkeypatch):
    """Regression guard: wait() must not block before the pipes are drained.

    A child writing more than the pipe buffer blocks until the parent reads;
    calling wait() first therefore deadlocks.  communicate() drains stdout
    and stderr concurrently, so it must be the only blocking call.
    """
    instances = []

    class FakePopen:
        def __init__(self, command, **kwargs):
            self.command = command
            self.kwargs = kwargs
            self.returncode = 0
            self.calls = []
            instances.append(self)

        def wait(self):
            self.calls.append('wait')
            raise AssertionError('wait() called before communicate()')

        def communicate(self):
            self.calls.append('communicate')
            return b'out\n', b'err\n'

    monkeypatch.setattr(main_module.subprocess, 'Popen', FakePopen)

    out, err, code = btrfs._slurp_command(
        ['btrfs', 'sub', 'list', '/tmp/.btrfs/sda1'])

    assert instances[0].calls == ['communicate']
    assert instances[0].command == ['btrfs', 'sub', 'list', '/tmp/.btrfs/sda1']
    # No shell: the argv list is executed directly.
    assert 'shell' not in instances[0].kwargs
    assert out == ['out']
    assert err == ['err']
    assert code == 0


def test_string_commands_are_rejected(btrfs):
    """A command string would need a shell; interpolated names would be unsafe."""
    with pytest.raises(TypeError):
        btrfs._slurp_command('btrfs sub list /tmp/.btrfs/sda1')


def test_arguments_with_spaces_and_metacharacters_reach_the_child_intact(btrfs):
    """A subvolume name is data, never shell syntax.

    The old shell-based implementation would word-split this argument and
    even run the `$(...)`; an argv list must pass it through unchanged.
    """
    name = 'my snap; touch /tmp/pwned $(id) "quoted"'
    out, err, code = btrfs._slurp_command(
        [sys.executable, '-c', 'import sys; print(sys.argv[1])', name])

    assert code == 0
    assert err == []
    assert out == [name]


def test_cwd_is_forwarded(btrfs, monkeypatch, tmp_path):
    """Commands may need a working directory (e.g. `btrfs fi du`)."""
    seen = {}

    class FakePopen:
        def __init__(self, command, **kwargs):
            seen['command'] = command
            seen['cwd'] = kwargs.get('cwd')
            self.returncode = 0

        def communicate(self):
            return b'', b''

    monkeypatch.setattr(main_module.subprocess, 'Popen', FakePopen)

    btrfs._slurp_command(['btrfs', 'fi', 'du', '-s', 'name'],
                         cwd=str(tmp_path))

    assert seen['command'] == ['btrfs', 'fi', 'du', '-s', 'name']
    assert seen['cwd'] == str(tmp_path)


def test_non_utf8_output_does_not_raise(btrfs):
    command = python_command('import sys; sys.stdout.buffer.write(b"\\xff\\xfe")')
    out, err, code = btrfs._slurp_command(command)
    assert code == 0
    assert err == []
    assert len(out) == 1
    assert "\ufffd" in out[0]
