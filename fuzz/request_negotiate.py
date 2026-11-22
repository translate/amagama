#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The language of the web pages and of the block titles, from ?lang= and
Accept-Language, and the titles in it.

The input is lines: ?lang=, Accept-Language, a block's title and version.
"""


import atheris

with atheris.instrument_imports():
    from werkzeug.datastructures import LanguageAccept
    from werkzeug.http import parse_accept_header

    from amagama import titles, webtext


def TestOneInput(data):
    lines = data.decode("utf-8", "replace").split("\n") + [""] * 4
    accept = parse_accept_header(lines[1], LanguageAccept)
    lang = titles.negotiate(lines[0], accept)
    assert lang in [*titles.catalogs(), "en"], lang
    page = webtext.negotiate(lines[0], accept)
    assert page in [*webtext.catalogs(), "en"], page
    webtext.Translator(page)
    direction, localised = titles.localise([{"title": lines[2], "version": lines[3]}], lang)
    assert direction in ("ltr", "rtl")
    assert len(localised) == 1
