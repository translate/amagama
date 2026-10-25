#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""zstd-compressed files in the fetch cache.

The cache's translation files sit unused between rebuilds, so they are
kept compressed (#3412). The standard library has zstd from Python 3.14;
before that, the zstandard package provides it.
"""

import os
import shutil

try:
    from compression import zstd

    def _open(path, mode, level=None):
        return zstd.open(path, mode, level=level)

    def _frame(data, level):
        return zstd.compress(data, level)
except ImportError:  # Python < 3.14
    import zstandard

    def _open(path, mode, level=None):
        cctx = zstandard.ZstdCompressor(level=level) if level is not None else None
        return zstandard.open(path, mode, cctx=cctx)

    def _frame(data, level):
        return zstandard.ZstdCompressor(level=level).compress(data)

SUFFIX = ".zst"
# About 100 MB/s; PO shrinks 4.6x, against 4.8x at level 19 (5 MB/s).
LEVEL = 9
_CHUNK = 1024 * 1024


def compress(path):
    """Replace path with path + SUFFIX."""
    if os.path.getsize(path) == 0:
        # Writing nothing through _open leaves a file with no zstd frame.
        with open(path + SUFFIX, "wb") as dst:
            dst.write(_frame(b"", LEVEL))
    else:
        with open(path, "rb") as src, _open(path + SUFFIX, "wb", LEVEL) as dst:
            shutil.copyfileobj(src, dst, _CHUNK)
    os.remove(path)


def decompress(path, dest):
    """Write the contents of path (a SUFFIX file) to dest."""
    if os.path.getsize(path) == 0:
        # An empty file, compressed before empty files got a frame.
        open(dest, "wb").close()
        return
    with _open(path, "rb") as src, open(dest, "wb") as dst:
        shutil.copyfileobj(src, dst, _CHUNK)
