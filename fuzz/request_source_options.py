#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""A lookup's ?exclude=, ?only=, ?weight=, ?licence= and ?exclude_checks=,
applied to suggestions from blocks with the given names and licence.

The input is lines: the five options, then the blocks' names (commas), their
licence, and a suggestion's checks (commas).
"""


import atheris

with atheris.instrument_imports():
    from amagama import source_options

OPTIONS = ["exclude", "only", "weight", "licence", "exclude_checks"]


def TestOneInput(data):
    lines = data.decode("utf-8", "replace").split("\n") + [""] * 8
    try:
        options = source_options.SourceOptions.from_args(dict(zip(OPTIONS, lines)))
    except ValueError:
        return
    for _, weight in options.weights:
        assert 0 <= weight <= source_options.MAX_WEIGHT, weight
    licence = lines[6]
    names = [name for name in lines[5].split(",") if name]
    results = [
        {"quality": 100.0, "sources": names, "checks": lines[7].split(",")},
        {"quality": 75.0, "sources": names[:1], "checks": []},
        {"quality": 90.0, "sources": [], "checks": []},
    ]
    chosen = options.choose(results, 2, {name: 2.0 for name in names},
                            {name: licence for name in names})
    assert len(chosen) <= 2
    for result in chosen:
        assert set(result["sources"]) <= set(names)
    source_options.licence_allowed(licence, options.licences)
