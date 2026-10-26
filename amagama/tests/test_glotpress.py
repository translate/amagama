#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import http.server
import threading
import urllib.parse

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_pairing import lookup
from amagama.tests.test_update import po
from amagama.tests.test_updater import QuietHandler, block, fetched
from amagama.updater.fetchers import FetchError, GlotPress
from amagama.updater.sources import load_sources

PROJECT = "apps/android/dev"

ROW = '''
<tr><td><strong><a href="/projects/{project}/{slug}/{set}/">{name}</a></strong></td>
<td class="stats percent">1%</td>
<td class="stats translated" title="translated">
  <a href="/projects/{project}/{slug}/{set}/?filters%5Bstatus%5D=current">{count}</a></td>
<td class="stats fuzzy" title="fuzzy">
  <a href="/projects/{project}/{slug}/{set}/?filters%5Bstatus%5D=fuzzy">0</a></td>
</tr>'''


class GlotPressHandler(QuietHandler):
    """Serves pages without their query, as translate.wordpress.org answers them;
    the paths in too_many get a 429 the first time."""

    too_many = set()
    requests = []

    def translate_path(self, path):
        return super().translate_path(urllib.parse.urlsplit(path).path)

    def do_GET(self):
        self.requests.append(self.path)
        path = urllib.parse.urlsplit(self.path).path
        if path in self.too_many:
            self.too_many.discard(path)
            self.send_error(429)
            return
        super().do_GET()


@pytest.fixture
def glotpress(tmp_path):
    """A local stand-in for translate.wordpress.org."""
    root = tmp_path / "glotpress"
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(GlotPressHandler, directory=str(root)))
    url = "http://127.0.0.1:%d" % server.server_address[1]

    def publish(sets):
        """sets: (locale slug, set slug, translated count as shown, [(English, translation)])"""
        project = root / "projects" / PROJECT
        project.mkdir(parents=True, exist_ok=True)
        (project / "index.html").write_text("<table>%s</table>" % "".join(
            ROW.format(project=PROJECT, slug=slug, set=name, name=slug, count=count)
            for slug, name, count, pairs in sets))
        for slug, name, count, pairs in sets:
            export = project / slug / name / "export-translations"
            export.mkdir(parents=True, exist_ok=True)
            (export / "index.html").write_text(po(slug, *pairs))

    publish([("af", "default", "1", [("Save post", "Stoor inskrywing")]),
             ("de", "default", "1,234", [("Save post", "Beitrag speichern")]),
             ("de", "formal", "2", [("Save post", "Beitrag speichern")]),
             ("pt-br", "default", "3", [("Save post", "Salvar post")]),
             ("ca", "default", "1", [("Save post", "Desa l'entrada")]),
             ("ca-val", "default", "1", [("Save post", "Guarda l'entrada")]),
             ("art-xemoji", "default", "1", [("Save post", "💾")]),
             ("zu", "default", "0", [])])
    GlotPressHandler.too_many = set()
    GlotPressHandler.requests = []
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


def gp_block(url, **settings):
    return block("wp-android", "glotpress", project=PROJECT, base_url=url, files=["**/*.po"],
                 max_age_days=0, **settings)


def test_sets(glotpress):
    root, url, publish = glotpress
    # Locales with nothing translated, and those that aren't languages, are left out.
    assert GlotPress({"project": PROJECT, "base_url": url}).sets() == [
        ("af", "default", 1), ("ca", "default", 1), ("ca-val", "default", 1),
        ("de", "default", 1234), ("de", "formal", 2), ("pt-br", "default", 3)]


def test_fetch(run, glotpress, pauses):
    root, url, publish = glotpress
    result = run(gp_block(url, pause=3))
    assert result.exit_code == 0, result.output
    # The formal German is German's; slugs become language codes.
    assert fetched(run.cache, "wp-android") == [
        ("af/default.po", "af"), ("ca-val/default.po", "ca@valencia"), ("ca/default.po", "ca"),
        ("de/default.po", "de"), ("de/formal.po", "de"), ("pt-br/default.po", "pt_BR")]
    assert pauses == [3] * 6
    # Only the current translations are exported.
    assert ("/projects/%s/af/default/export-translations/?format=po&filters%%5Bstatus%%5D=current"
            % PROJECT) in GlotPressHandler.requests


def test_update(run, glotpress):
    root, url, publish = glotpress
    result = run(gp_block(url, pause=0), "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 6 files: 6 units (0 skipped)." in result.output
    assert lookup("af", "Save post") == ["Stoor inskrywing"]
    assert lookup("ca@valencia", "Save post") == ["Guarda l'entrada"]
    assert lookup("pt_BR", "Save post") == ["Salvar post"]


def test_fingerprint_change(run, glotpress):
    root, url, publish = glotpress
    text = gp_block(url, pause=0)
    run(text)
    assert run(text, "--check").output == ""
    publish([("af", "default", "2", [("Save post", "Stoor inskrywing"), ("Undo", "Ontdoen")])])
    assert run(text, "--check").output == "wp-android\n"


def test_too_many_requests_retried(glotpress, pauses):
    root, url, publish = glotpress
    GlotPressHandler.too_many = {"/projects/%s/" % PROJECT,
                                 "/projects/%s/af/default/export-translations/" % PROJECT}
    fetcher = GlotPress({"project": PROJECT, "base_url": url, "pause": 0, "retry_wait": 7})
    files = fetcher.fetch(str(root / "out"), ["af"])
    assert files == [("af/default.po", "af")]
    assert pauses == [7, 7, 0]


def test_too_many_requests_gives_up(glotpress, pauses, monkeypatch):
    root, url, publish = glotpress
    monkeypatch.setattr(GlotPressHandler, "too_many", type("Always", (set,), {
        "__contains__": lambda self, path: True, "discard": lambda self, path: None})())
    with pytest.raises(FetchError, match="429"):
        GlotPress({"project": PROJECT, "base_url": url, "retry_wait": 5}).sets()
    assert pauses == [5, 10]


def test_no_locales(glotpress):
    root, url, publish = glotpress
    (root / "projects" / PROJECT / "index.html").write_text("<p>Sub-projects only</p>")
    with pytest.raises(FetchError, match="no locales listed"):
        GlotPress({"project": PROJECT, "base_url": url}).sets()


@pytest.mark.parametrize("project", ["", "apps/../x", "Apps/android", "apps/android/"])
def test_invalid(tmp_path, project):
    path = tmp_path / "sources.toml"
    path.write_text(block("wp", "glotpress", project=project, files=["**/*.po"]))
    with pytest.raises(ValueError, match="project must be a GlotPress project path"):
        load_sources(str(path))
