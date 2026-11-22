#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Generated requests to the read API and the web pages.

None may fail with a server error, and a lookup of a stored text finds it.
The tests run with the same examples every time; with AMAGAMA_FUZZ set they
run many more, new ones each time.
"""

import os
from urllib.parse import quote

import pytest
from flask_caching import Cache
from hypothesis import HealthCheck, given, settings, strategies as st

from amagama.application import amagama_server_factory
from amagama.tests.conftest import TempTMDB

examples = settings(max_examples=200, derandomize=True, deadline=None,
                    suppress_health_check=[HealthCheck.function_scoped_fixture,
                                           HealthCheck.too_slow])
if os.environ.get("AMAGAMA_FUZZ"):
    examples = settings(examples, max_examples=20000, derandomize=False)

STORED = [("File", "Lêer"), ("Open file", "Maak lêer oop"), ("&Save", "&Stoor"),
          ("Close the window...", "Sluit die venster..."), ("Ünïcødé ✓ 😀", "Ünïcødé ✓ 😀")]


def known_bug(exc, path, query):
    """The open issue an exception from a request is, or None."""
    if isinstance(exc, ValueError) and "NUL (0x00)" in str(exc) and \
            ("%00" in path or "\x00" in query.get('source', '')):
        return 3670
    return None


@pytest.fixture
def app(amagama_config, pg_connection):
    amagama_config(ENABLE_WEB_UI=True)
    app = amagama_server_factory()
    app.testing = True
    app.tmdb = TempTMDB(connection=pg_connection, app=app)
    app.tmdb.init_db(['en'])
    app.cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache'})
    with app.app_context():
        for source, target in STORED:
            app.tmdb.add_test_unit(source, target)
    return app


@pytest.fixture
def get(app, pg_connection):
    """GET path, giving the response, or None for a known bug."""
    def get(path, query=None, headers=None):
        try:
            response = app.test_client().get(path, query_string=query, headers=headers)
        except Exception as exc:
            if known_bug(exc, path, query or {}) is None:
                raise
            # As the pool does after a 500.
            pg_connection.rollback()
            return None
        assert response.status_code < 500, (path, query, headers)
        return response
    return get


def integer(text):
    """text as the API reads an integer option."""
    try:
        return int(text)
    except ValueError:
        return None


text = st.text(st.characters(codec='utf-8'), max_size=60) | st.sampled_from(
    [source for source, _ in STORED] + ["\x00", "%", "/", "..", "' OR 1=1 --", "&&x", "…",
                                        "﻿", "‮", "x" * 3000])
header = st.text(st.characters(codec='utf-8', exclude_categories=['Cc']), max_size=40) | \
    st.sampled_from(["*", "af;q=0.5, en", "q=abc", ";;;", "af;q=nan"])
code = st.sampled_from(["en", "af", "en_US", "en-GB", "af_ZA", "sr@latin", "zz", "x!", "",
                        "a" * 300, "@", "_", "../en", "EN", "und", "mul"]) | text
number = st.sampled_from(["", "0", "-1", "70", "100", "101", "9999999999", "1e9", "nan",
                          "10" * 20, "1_000", " 5 ", "٥"]) | st.integers().map(str)
globs = st.lists(text | st.sampled_from(["*", "!*", "[", "[!]", "**" * 40 + "x", "GPL-2.0+",
                                         "*:3", "x:11", "x:nan", "x:inf", ":5"]),
                 max_size=4).map(",".join)
options = st.fixed_dictionaries({}, optional={
    'min_similarity': number, 'max_candidates': number,
    'style': st.sampled_from(["mozilla", "gnome", "kde", "openoffice", "nope"]) | text,
    'exclude': globs, 'only': globs, 'weight': globs, 'licence': globs,
    'exclude_checks': globs, 'jsoncallback': text, 'source': text})


@examples
@given(slang=code, tlang=code, source=text, query=options, accept=st.none() | header,
       prefix=st.sampled_from(['/api/v1', '/tmserver']), in_path=st.booleans())
def test_lookup(get, slang, tlang, source, query, accept, prefix, in_path):
    path = "%s/%s/%s/unit/" % (prefix, quote(slang, safe=''), quote(tlang, safe=''))
    if in_path:
        path += quote(source, safe='/')
    response = get(path, query, {'Accept-Language': accept} if accept is not None else None)
    if response is not None and response.status_code == 200 and not query.get('jsoncallback'):
        assert isinstance(response.get_json(force=True), list)


@examples
@given(stored=st.sampled_from(STORED), min_similarity=st.none() | number,
       max_candidates=st.none() | number)
def test_stored_text_found(get, stored, min_similarity, max_candidates):
    source, target = stored
    query = {name: value for name, value in (('min_similarity', min_similarity),
                                             ('max_candidates', max_candidates))
             if value is not None}
    response = get("/api/v1/en/af/unit/%s" % quote(source, safe=''), query)
    if response is None:
        return
    candidates = integer(max_candidates or "")
    if candidates is not None and candidates < 0:
        return  # #3672: a negative max_candidates drops matches.
    found = [(match['source'], match['target']) for match in response.get_json()]
    if max(integer(min_similarity or "") or 70, 70) > 100:
        assert found == []
    else:
        assert (source, target) in found


@examples
@given(page=st.sampled_from(['/', '/stats/', '/api/v1/sources/', '/api/v1/languages/',
                             '/api/v1/stats/', '/tmserver/sources/']),
       lang=st.none() | code, accept=st.none() | header, callback=st.none() | text)
def test_pages(get, page, lang, accept, callback):
    query = {name: value for name, value in (('lang', lang), ('jsoncallback', callback))
             if value is not None}
    get(page, query, {'Accept-Language': accept} if accept is not None else None)
