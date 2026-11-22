#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Fuzz the updater's readers of text formats: PO and MO, CSV, properties,
Joomla INI, PHP and Laravel, .strings and DTD.

The input is a reader's name, a newline, then the file (see readers.py).
"""

import atheris
import fuzzing

with atheris.instrument_imports():
    import readers

NAMES = readers.FAMILIES["text"]


def TestOneInput(data):
    readers.fuzz_one(NAMES, data)


def main():
    readers.quiet()
    fuzzing.run(TestOneInput)


if __name__ == "__main__":
    main()
