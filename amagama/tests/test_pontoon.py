#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import http.server
import json
import threading

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_pairing import lookup
from amagama.tests.test_updater import QuietHandler, block, fetched
from amagama.updater.fetchers import FetchError, Pontoon


def tmx(lang, *units):
    """units: (tuid, source, target)"""
    body = "".join('<tu tuid="%s"><tuv xml:lang="en-US"><seg>%s</seg></tuv>'
                   '<tuv xml:lang="%s"><seg>%s</seg></tuv></tu>' % (tuid, source, lang, target)
                   for tuid, source, target in units)
    return ('<?xml version="1.0" encoding="UTF-8"?><tmx version="1.4"><header srclang="en-US" '
            'datatype="plaintext" segtype="sentence" o-tmf="plain text" adminlang="en-US" '
            'creationtool="pontoon" creationtoolversion="0.1"/><body>%s</body></tmx>' % body)


@pytest.fixture
def pontoon(tmp_path):
    """A local stand-in for pontoon.mozilla.org, with the locales on two pages."""
    root = tmp_path / "pontoon"
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    url = "http://127.0.0.1:%d" % server.server_address[1]
    pages = [[("af", 10), ("pa-PK", 3)], [("ach", 0)]]
    for i, locales in enumerate(pages):
        page = root / "api/v2/locales" / ("page%d" % i if i else "")
        page.mkdir(parents=True)
        (page / "index.html").write_text(json.dumps({
            "next": "%s/api/v2/locales/page%d/" % (url, i + 1) if i + 1 < len(pages) else None,
            "results": [{"code": code, "approved_strings": count} for code, count in locales]}))
    (root / "translation-memory").mkdir()
    for code, lang in (("af", "af"), ("pa-PK", "pa-PK")):
        (root / "translation-memory" / ("%s.all-projects.tmx" % code)).write_text(tmx(
            lang, ("firefox:browser.ftl:save", "Save file", "Stoor lêer")), encoding="utf-8")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, url
    server.shutdown()
    server.server_close()


@pytest.fixture
def run(amagama_config, tmp_path):
    cache = tmp_path / "cache"
    sources = tmp_path / "sources.toml"

    def run(sources_text, *args, command="fetch_sources"):
        sources.write_text(sources_text)
        amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(cache),
                       UPDATER_WORKERS=1)
        return CliRunner().invoke(manage, (command,) + args)
    run.cache = cache
    return run


def test_fetch(run, pontoon, pauses):
    root, url = pontoon
    result = run(block("mozilla", "pontoon", base_url=url, pause=5, max_age_days=0))
    assert result.exit_code == 0, result.output
    # ach has nothing approved.
    assert fetched(run.cache, "mozilla") == [
        ("af/af.all-projects.tmx", "af"), ("pa-PK/pa-PK.all-projects.tmx", "pa_PK")]
    assert pauses == [5, 5]


def test_min_approved(pontoon):
    root, url = pontoon
    assert Pontoon({"base_url": url, "min_approved": 5}).languages() == ["af"]


def test_no_fingerprint(run, pontoon):
    root, url = pontoon
    text = block("mozilla", "pontoon", base_url=url, pause=0)
    run(text)
    assert run(text, "--check").output == ""
    assert Pontoon({"base_url": url}).fingerprint() is None


def test_bad_locales(pontoon):
    root, url = pontoon
    (root / "api/v2/locales/index.html").write_text('{"results": [{"code": "af"}]}')
    with pytest.raises(FetchError, match="approved_strings"):
        Pontoon({"base_url": url}).languages()


def test_update_exclude_ids(run, pontoon):
    root, url = pontoon
    (root / "translation-memory/af.all-projects.tmx").write_text(tmx(
        "af", ("firefox:browser.ftl:save", "Save file", "Stoor lêer"),
        ("foundation-website-content:blog:intro", "Read our blog", "Lees die blog")),
        encoding="utf-8")
    result = run(block("mozilla", "pontoon", base_url=url, pause=0, max_age_days=0,
                       exclude_ids=["foundation-website-content:*"]),
                 "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 2 files: 2 units (0 skipped)." in result.output
    assert lookup("af", "Save file") == ["Stoor lêer"]
    assert lookup("af", "Read our blog") == []


def test_fetch_some_languages(pontoon, tmp_path):
    root, url = pontoon
    fetcher = Pontoon({"base_url": url, "pause": 0})
    lang = fetcher.languages()[0]
    files = fetcher.fetch(str(tmp_path / "out"), [lang])
    assert files and {code for path, code in files} == {lang}
