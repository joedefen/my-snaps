#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shared utilities fo the my-snap tools
"""
# pylint: disable=broad-exception-caught,invalid-name

import re
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
