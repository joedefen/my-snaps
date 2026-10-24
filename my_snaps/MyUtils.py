#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shared utilities fo the my-snap tools
"""
# pylint: disable=broad-exception-caught,invalid-name

import os
import re
import sys
import time
from datetime import datetime

##############################################################################
def timestamp_str():
    """ Get a data string for a time of so many days ago. """
    return datetime.now().strftime("%Y-%m-%d-%H%M%S")

##############################################################################
def human(number):
    """ Return a concise number description."""
    suffixes = ['K', 'M', 'G', 'T']
    number = float(number)
    while suffixes:
        suffix = suffixes.pop(0)
        number /= 1024
        if number < 99.95 or not suffixes:
            return f'{number:.1f}{suffix}'
    return None

##############################################################################
def ago_str(delta_secs, signed=False):
    """ Turn time differences in seconds to a compact representation;
        e.g., '18h·39m'
    """
    ago = int(max(0, round(delta_secs if delta_secs >= 0 else -delta_secs)))
    divs = (60, 60, 24, 7, 52, 9999999)
    units = ('s', 'm', 'h', 'd', 'w', 'y')
    vals = (ago%60, int(ago/60)) # seed with secs, mins (step til 2nd fits)
    uidx = 1 # best units
    for div in divs[1:]:
        # print('vals', vals, 'div', div)
        if vals[1] < div:
            break
        vals = (vals[1]%div, int(vals[1]/div))
        uidx += 1
    rv = '-' if signed and delta_secs < 0 else ''
    rv += f'{vals[1]}{units[uidx]}' if vals[1] else ''
    rv += f'{vals[0]:d}{units[uidx-1]}'
    return rv

##############################################################################
def version_string():
    """ Return the installed my-snaps version, or 'unknown' from a bare tree. """
    try:
        from importlib.metadata import PackageNotFoundError, version
        return version('my-snaps')
    except (ImportError, PackageNotFoundError):
        return 'unknown'

##############################################################################
def reexec_as_root(module_name, argv=None):
    """ Re-run this command under sudo, but only when root is truly needed.

    Call this *after* argument parsing.  `--help` and `--version` then exit
    before any password prompt, invalid usage is reported without sudo, and
    read-only/dry-run modes can deliberately stay unprivileged.  It returns
    when the process is already root; otherwise `execvp()` replaces it and
    this call never returns.
    """
    if os.geteuid() == 0:
        return

    # A source checkout needs the project root on sys.path for
    # `python -m my_snaps.<module>`; an installed package is importable from
    # anywhere, so do not chdir the caller's shell into site-packages.
    package_dir = os.path.dirname(os.path.abspath(__file__))
    project_dir = os.path.dirname(package_dir)
    if os.path.isfile(os.path.join(project_dir, 'pyproject.toml')):
        os.chdir(project_dir)

    args = list(sys.argv[1:] if argv is None else argv)
    command = ['sudo', sys.executable, '-m', module_name] + args
    try:
        os.execvp('sudo', command)
    except OSError as exce:
        print(f'ERROR: cannot re-run as root with sudo: {exce}',
              file=sys.stderr)
        print('HINT: run this command as root or prefix it with sudo.',
              file=sys.stderr)
        raise SystemExit(1)

##############################################################################
def ago_whence(filename):
    """ Find the standard time string in the file name and return
        the ago_str()
    """
    mat = re.search(r'\b(\d\d\d\d-\d\d-\d\d-\d\d\d\d\d\d)\b', filename)
    if mat:
        try:
            dt_object = datetime.strptime(mat.group(1), "%Y-%m-%d-%H%M%S")
            return ago_str(time.time() - dt_object.timestamp())
        except Exception:
            pass
    return ''
