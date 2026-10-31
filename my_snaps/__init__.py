"""Simple BTRFS snapshot tools: the TUI, the restorer and the balancer.

The version lives here and nowhere else.  ``[project]`` in ``pyproject.toml``
declares ``dynamic = ["version"]``, ``bless`` reads the release number from this
line, and the built wheel is checked against it by ``tests/inspect_dist.py``.
"""

__version__ = "2.0.2"
