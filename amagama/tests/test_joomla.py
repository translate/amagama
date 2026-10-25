#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import hashlib
import http.server
import io
import subprocess
import threading
import zipfile

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_pairing import lookup
from amagama.tests.test_updater import QuietHandler, block
from amagama.updater.fetchers import FetchError, Joomla
from amagama.updater.sources import load_sources


def details(*versions):
    """versions: (version, url, sha256)"""
    return "<updates>%s</updates>" % "".join(
        "<update><version>%s</version><downloads><downloadurl>%s</downloadurl></downloads>"
        "<sha256>%s</sha256></update>" % version for version in versions)


@pytest.fixture
def joomla(tmp_path):
    """Local stand-ins for update.joomla.org, its pack downloads and joomla-cms."""
    repo = tmp_path / "joomla-cms"
    for path, text in (("language/en-GB/joomla.ini", 'JSAVE="Save"\n'),
                       ("administrator/language/en-GB/com_content.ini",
                        'COM_CONTENT_ARTICLE="Article"\n'),
                       ("installation/language/en-GB/joomla.ini", 'INSTL="Install"\n'),
                       ("language/en-GB/install.xml", "<extension/>")):
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text)
    subprocess.run(["sh", "-c", "git init -q -b 5.4-dev && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m en"], cwd=repo, check=True)

    root = tmp_path / "site"
    (root / "details5").mkdir(parents=True)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    url = "http://127.0.0.1:%d" % server.server_address[1]
    packs = {"af-ZA": {"language/af-ZA/joomla.ini": 'JSAVE="Stoor"\n',
                       "administrator/language/af-ZA/com_content.ini":
                       'COM_CONTENT_ARTICLE="Artikel"\n',
                       "pkg_af-ZA.xml": "<extension/>", "script.php": "<?php"},
             # A folder around the files, and macOS's copies.
             "sr-YU": {"sr-YU/language/sr-YU/joomla.ini": 'JSAVE="Sačuvaj"\n',
                       "language/sr-YU/joomla.ini": 'JSAVE="Sačuvaj"\n',
                       "__MACOSX/sr-YU/language/sr-YU/._joomla.ini": "\x00\x05",
                       # Not a manifest: ignored.
                       "readme.xml": "<extension",
                       "script.php": "<?php"}}
    # Packages of a zip per client, named in the manifest.
    clients = {}
    for client, files in (("site", {"joomla.ini": 'JSAVE="Speichern"\n'}),
                          ("admin", {"com_content.ini": 'COM_CONTENT_ARTICLE="Beitrag"\n'})):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        clients["%s_de-DE.zip" % client] = inner.getvalue()
    packs["de-DE"] = dict(clients, **{"pkg_de-DE.xml": (
        '<extension type="package"><files>'
        '<file type="language" client="site" id="de-DE">site_de-DE.zip</file>'
        '<file type="language" client="administrator" id="de-DE">admin_de-DE.zip</file>'
        '</files></extension>')})
    extensions = []
    for lang, files in packs.items():
        with zipfile.ZipFile(root / ("%s.zip" % lang), "w") as archive:
            for name, text in files.items():
                archive.writestr(name, text)
        sha256 = hashlib.sha256((root / ("%s.zip" % lang)).read_bytes()).hexdigest()
        (root / "details5" / ("%s_details.xml" % lang)).write_text(details(
            ("5.4.11.1", "%s/%s.zip" % (url, lang), sha256),
            ("5.4.3.1", "%s/old.zip" % url, "0" * 64)))
        extensions.append('<extension element="pkg_%s" version="5.4.11.1" '
                          'detailsurl="%s/details5/%s_details.xml"/>' % (lang, url, lang))
    (root / "translationlist_5.xml").write_text(
        "<extensionset>%s</extensionset>" % "".join(extensions))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, {"release": "5", "ref": "5.4-dev", "base_url": url,
                 "repository": repo.as_uri()}
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


TEMPLATE = "**/language/en-GB/*.ini"


def fetcher(settings, **extra):
    fetcher = Joomla(dict(settings, **extra))
    fetcher.use_template(TEMPLATE)
    return fetcher


def joomla_block(settings):
    return block("joomla", "joomla", files=["**/language/{lang}/*.ini"], format="joomla-ini",
                 template=TEMPLATE, pause=0,
                 languages={"sr-YU": "sr@latin", "en-GB": ""}, **settings)


def test_fetch(joomla, tmp_path, pauses):
    root, settings = joomla
    files = fetcher(settings, pause=2).fetch(str(tmp_path / "out"))
    # English from joomla-cms; the newest version of each pack.
    assert sorted(files) == [
        ("administrator/language/af-ZA/com_content.ini", "af-ZA"),
        ("administrator/language/de-DE/com_content.ini", "de-DE"),
        ("administrator/language/en-GB/com_content.ini", "en-GB"),
        ("installation/language/en-GB/joomla.ini", "en-GB"),
        ("language/af-ZA/joomla.ini", "af-ZA"),
        ("language/de-DE/joomla.ini", "de-DE"),
        ("language/en-GB/joomla.ini", "en-GB"),
        ("language/sr-YU/joomla.ini", "sr-YU")]
    # The English is what the template matches.
    assert not (tmp_path / "out/language/en-GB/install.xml").exists()
    assert not (tmp_path / "out/script.php").exists()
    assert pauses == [2, 2, 2]


def test_sha256_mismatch(joomla, tmp_path, caplog):
    root, settings = joomla
    with zipfile.ZipFile(root / "af-ZA.zip", "w") as archive:
        archive.writestr("language/af-ZA/joomla.ini", 'JSAVE="Anders"\n')
    files = fetcher(settings, pause=0).fetch(str(tmp_path / "out"))
    assert sorted({lang for path, lang in files} - {"en-GB"}) == ["de-DE", "sr-YU"]
    assert "af-ZA.zip: SHA-256 doesn't match" in caplog.text


def test_no_download(joomla):
    root, settings = joomla
    (root / "details5/af-ZA_details.xml").write_text(details())
    with pytest.raises(FetchError, match="no download for 5.4.11.1"):
        fetcher(settings)._download(fetcher(settings).packs()[0][2], "5.4.11.1")


@pytest.mark.parametrize("text, error", [
    ("<extensionset><extension/></extensionset>", "not a list of language packs"),
    ("<html>", "translationlist_5.xml"),
])
def test_bad_list(joomla, text, error):
    root, settings = joomla
    (root / "translationlist_5.xml").write_text(text)
    with pytest.raises(FetchError, match=error):
        fetcher(settings).packs()


def test_fingerprint(joomla):
    root, settings = joomla
    before = fetcher(settings).fingerprint()
    listing = root / "translationlist_5.xml"
    listing.write_text(listing.read_text().replace("5.4.11.1", "5.4.12.1", 1))
    assert fetcher(settings).fingerprint() != before


def test_needs_template(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(block("j", "joomla", release="5", ref="5.4-dev",
                          files=["**/language/{lang}/*.ini"]))
    with pytest.raises(ValueError, match="joomla needs a template"):
        load_sources(str(path))


@pytest.mark.parametrize("release", ["5.4", "latest"])
def test_invalid(tmp_path, release):
    path = tmp_path / "sources.toml"
    path.write_text(block("j", "joomla", release=release, ref="5.4-dev",
                          files=["**/language/{lang}/*.ini"]))
    with pytest.raises(ValueError, match="release must be a Joomla major version"):
        load_sources(str(path))


def test_update(run, joomla):
    root, settings = joomla
    result = run(joomla_block(settings), "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 5 files: 5 units (0 skipped)." in result.output
    assert lookup("de", "Article") == ["Beitrag"]
    assert lookup("af", "Save") == ["Stoor"]
    assert lookup("af", "Article") == ["Artikel"]
    assert lookup("sr@latin", "Save") == ["Sačuvaj"]


def test_fetch_some_languages(joomla, tmp_path):
    root, settings = joomla
    joomla_fetcher = fetcher(settings)
    lang = joomla_fetcher.languages()[0]
    files = joomla_fetcher.fetch(str(tmp_path / "out"), [lang])
    # The English template comes every time.
    assert {code for path, code in files} == {lang, "en-GB"}
