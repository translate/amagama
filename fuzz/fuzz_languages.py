#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Language codes from requests and repositories: canonical forms and the
names their data is stored under.

The input is a code.
"""

import re
import sys

import atheris

with atheris.instrument_imports():
    from amagama import tmdb
    from amagama.updater import languages

TABLE_NAME = re.compile(r"[a-z0-9_]+")


def TestOneInput(data):
    code = data.decode("utf-8", "replace")
    canonical = languages.normalise_language(code)
    table = tmdb.lang_to_table(code)
    languages.not_language(code)
    languages.is_android_locale(code)
    languages.map_language(code, {"x": ""})
    if not code.isascii():
        return  # #3674: some non-ASCII letters pass as a modifier.
    if canonical is not None:
        assert languages.normalise_language(canonical) == canonical, (code, canonical)
    if table is not None:
        assert TABLE_NAME.fullmatch(table), (code, table)


def main():
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
