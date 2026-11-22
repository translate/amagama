#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Fuzz the code that reads a request's text and options, and the fetch path
check: request_*.py, one fuzz target for all, since each soon finds all it
can.

The input's first byte picks which; the rest is that one's input.
"""

import atheris
import fuzzing

with atheris.instrument_imports():
    import request_fetch_paths
    import request_languages
    import request_lookup_text
    import request_negotiate
    import request_source_options

TARGETS = [request_fetch_paths, request_languages, request_lookup_text, request_negotiate,
           request_source_options]


def TestOneInput(data):
    if data:
        TARGETS[data[0] % len(TARGETS)].TestOneInput(data[1:])


def main():
    fuzzing.run(TestOneInput)


if __name__ == "__main__":
    main()
