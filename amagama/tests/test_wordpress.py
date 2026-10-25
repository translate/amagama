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
import urllib.parse
import zipfile

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_pairing import lookup
from amagama.tests.test_update import po
from amagama.tests.test_updater import QuietHandler, block, fetched
from amagama.updater.fetchers import FetchError, WordPress
from amagama.updater.sources import load_sources


class SlugHandler(QuietHandler):
    """Serves ?slug=x and ?version=x from the directory x, as api.wordpress.org answers them."""

    def translate_path(self, path):
        url = urllib.parse.urlsplit(path)
        query = urllib.parse.parse_qs(url.query)
        name = (query.get("slug") or query.get("version") or [""])[0]
        return super().translate_path(url.path + (name + "/" if name else ""))


@pytest.fixture
def wordpress(tmp_path):
    """A local stand-in for api.wordpress.org and downloads.wordpress.org."""
    root = tmp_path / "wordpress"
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(SlugHandler, directory=str(root)))
    url = "http://127.0.0.1:%d" % server.server_address[1]

    def publish(kind, slug, lang, *pairs, updated="2026-10-01 00:00:00"):
        name = "%s-%s" % (slug, lang) if slug != "core" else lang
        package = root / "packs" / kind / slug / ("%s.zip" % lang)
        package.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("%s.po" % name, po(lang, *pairs))
            archive.writestr("%s.mo" % name, b"\x00")
        api = root / kind / "1.0" / ("" if slug == "core" else slug)
        api.mkdir(parents=True, exist_ok=True)
        listing = api / "index.html"
        packs = json.loads(listing.read_text())["translations"] if listing.exists() else []
        packs = [p for p in packs if p["language"] != lang] + [{
            "language": lang, "version": "1.0", "updated": updated,
            "package": "%s/packs/%s/%s/%s.zip" % (url, kind, slug, lang)}]
        listing.write_text(json.dumps({"translations": packs}))

    publish("core", "core", "af", ("Save file", "Stoor lêer"))
    publish("core", "core", "de_DE_formal", ("Save file", "Datei speichern"))
    publish("plugins", "woocommerce", "af", ("Add to cart", "Voeg by mandjie"))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, url, publish
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


def test_fetch(run, wordpress, pauses):
    root, url, publish = wordpress
    result = run(block("wp", "wordpress", kind="core", base_url=url, files=["**/*.po"], pause=3,
                       max_age_days=0)
                 + block("plugins", "wordpress", kind="plugins", slugs=["woocommerce"],
                         base_url=url, files=["**/*.po"], pause=3, max_age_days=0))
    assert result.exit_code == 0, result.output
    # The formal German is German's; the .mo copies aren't imported.
    assert fetched(run.cache, "wp") == [("core/af/af.po", "af"),
                                        ("core/de_DE_formal/de_DE_formal.po", "de_DE")]
    assert fetched(run.cache, "plugins") == [("woocommerce/af/woocommerce-af.po", "af")]
    assert pauses == [3, 3, 3]


def test_fingerprint_change(run, wordpress):
    root, url, publish = wordpress
    text = block("wp", "wordpress", kind="core", base_url=url, files=["**/*.po"], pause=0,
                 max_age_days=0)
    run(text)
    assert run(text, "--check").output == ""
    publish("core", "core", "af", ("Save file", "Stoor"), updated="2026-10-20 00:00:00")
    assert run(text, "--check").output == "wp\n"


def test_bad_zip(wordpress):
    root, url, publish = wordpress
    (root / "packs/core/core/af.zip").write_text("not a zip")
    with pytest.raises(FetchError, match="File is not a zip file"):
        WordPress({"kind": "core", "base_url": url, "pause": 0}).fetch(str(root / "out"))


def test_bad_api(wordpress):
    root, url, publish = wordpress
    (root / "core/1.0/index.html").write_text('{"translations": [{"language": "af"}]}')
    with pytest.raises(FetchError, match="version"):
        WordPress({"kind": "core", "base_url": url}).packs()


@pytest.mark.parametrize("settings, error", [
    ({"kind": "apps"}, "kind must be one of core, plugins, themes"),
    ({"kind": "core", "slugs": ["akismet"]}, "slugs must list"),
    ({"kind": "plugins"}, "slugs must list"),
    ({"kind": "plugins", "slugs": ["../x"]}, "slugs must list"),
    ({"kind": "plugins", "slugs": ["akismet"], "release": "7.1"}, "release must be"),
    ({"kind": "core", "release": "latest"}, "release must be"),
])
def test_invalid(tmp_path, settings, error):
    path = tmp_path / "sources.toml"
    path.write_text(block("wp", "wordpress", files=["**/*.po"], **settings))
    with pytest.raises(ValueError, match=error):
        load_sources(str(path))


def test_update(run, wordpress):
    root, url, publish = wordpress
    result = run(block("wp", "wordpress", kind="core", base_url=url, files=["**/*.po"], pause=0,
                       max_age_days=0),
                 "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 2 files: 2 units (0 skipped)." in result.output
    assert lookup("af", "Save file") == ["Stoor lêer"]
    assert lookup("de_DE", "Save file") == ["Datei speichern"]


def test_release(wordpress):
    root, url, publish = wordpress
    # WordPress 7.1 installs Afrikaans's 5.8 pack: the API lists it for 7.1.
    release = root / "core/1.0/7.1"
    release.mkdir()
    (release / "index.html").write_text(
        (root / "core/1.0/index.html").read_text().replace('"1.0"', '"5.8"'))
    packs = WordPress({"kind": "core", "release": "7.1", "base_url": url}).packs()
    assert [(slug, lang, version) for slug, lang, version, updated, package in packs] == [
        ("core", "af", "5.8"), ("core", "de_DE_formal", "5.8")]


def test_fetch_some_languages(wordpress):
    root, url, publish = wordpress
    fetcher = WordPress({"kind": "core", "base_url": url, "pause": 0})
    lang = fetcher.languages()[0]
    files = fetcher.fetch(str(root / "out"), [lang])
    assert files and {code for path, code in files} == {lang}
