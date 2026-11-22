#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The paths a fetcher builds from a listing's or an API's language codes and
file names: each one inside the fetch directory, and none of a plain relative
name refused.

The input is lines: the parts of one path.
"""

import os
import posixpath

import atheris
import fuzzing

with atheris.instrument_imports():
    from amagama.updater import fetchers

# inside() is mostly os.path, imported before the instrumentation started.
for function in (posixpath.join, posixpath.normpath, posixpath.commonpath):
    atheris.instrument_func(function)

DEST = "/srv/amagama/cache/block"


def TestOneInput(data):
    parts = data.decode("utf-8", "replace").split("\n")[:3]
    try:
        path = fetchers.inside(DEST, *parts)
    except fetchers.FetchError:
        path = None
    relative = os.path.normpath(os.path.join(*parts))
    if path is not None:
        # A doubled leading slash is kept: //srv is /srv.
        assert ("/" + path.lstrip("/")).startswith(DEST + os.sep), (parts, path)
        assert os.path.normpath(path) == path, (parts, path)
    elif not any(os.path.isabs(part) for part in parts):
        assert relative in (".", "..") or relative.startswith(".." + os.sep), (parts, relative)


def main():
    fuzzing.run(TestOneInput)


if __name__ == "__main__":
    main()
