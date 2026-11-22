#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""A lookup's text before and after the query: the indexed form for each
project style, the similarity of two texts, and mojibake repair.

The input is a byte choosing the style, then lines: the source text and a
stored text to compare it with.
"""


import atheris

with atheris.instrument_imports():
    from translate.filters.checks import projectcheckers
    from translate.search.lshtein import LevenshteinComparer

    from amagama import mojibake, normalise, tmdb

STYLES = [None, *sorted(projectcheckers)]
COMPARER = LevenshteinComparer(2000)


def TestOneInput(data):
    if not data:
        return
    style = STYLES[data[0] % len(STYLES)]
    lines = data[1:].decode("utf-8", "replace").split("\n") + [""]
    source, stored = lines[0], lines[1]
    checker = tmdb.project_checker(style, "en")
    normalise.indexing_version(source, checker)
    quality = COMPARER.similarity(source, stored, 70)
    assert 0 <= quality <= 100, (source, stored, quality)
    tmdb.min_levenshtein_length(len(source), 70)
    tmdb.max_levenshtein_length(len(source), 70, 2000)
    repaired = mojibake.unmangle(source)
    assert repaired is None or repaired != source
