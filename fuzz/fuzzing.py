#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Running a fuzz target within the time it is given.

ClusterFuzzLite passes -max_total_time and stops the target 10 seconds after
it. libFuzzer counts that time from when fuzzing starts, after the target's
imports and instrumentation, which take longer than 10 seconds for some
targets; stopped while still reducing its corpus, a target's corpus can fail
to upload. run() takes the time already spent off -max_total_time, so the
10 seconds are left for libFuzzer to finish.
"""

import math
import os
import sys

import atheris


def _started(pid):
    """When process pid started, in seconds since boot."""
    with open("/proc/%d/stat" % pid) as f:
        fields = f.read().rsplit(")", 1)[1].split()
    return int(fields[19]) / os.sysconf("SC_CLK_TCK")


def running_for():
    """Seconds since the target was launched, counting the PyInstaller
    bootloader that unpacked it; 0 where /proc can't tell."""
    try:
        with open("/proc/uptime") as f:
            uptime = float(f.read().split()[0])
        pids = [os.getpid()]
        parent = os.getppid()
        if os.path.realpath("/proc/%d/exe" % parent) == os.path.realpath("/proc/self/exe"):
            pids.append(parent)
        return max(0.0, uptime - min(_started(pid) for pid in pids))
    except (OSError, ValueError, IndexError):
        return 0.0


def within(argv, spent):
    """argv with -max_total_time less spent, but at least 1."""
    result = []
    for arg in argv:
        name, _, value = arg.partition("=")
        if name == "-max_total_time" and value.isdigit():
            arg = "-max_total_time=%d" % max(1, int(value) - math.ceil(spent))
        result.append(arg)
    return result


def run(test_one_input):
    """Fuzz test_one_input, finishing within the time the target was given."""
    atheris.Setup(within(sys.argv, running_for()), test_one_input)
    atheris.Fuzz()
