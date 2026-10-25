import functools
import http.server
import threading

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_pairing import lookup
from amagama.tests.test_update import po
from amagama.tests.test_updater import QuietHandler, block, fetched
from amagama.updater.fetchers import Drupal, FetchError
from amagama.updater.sources import load_sources

SERVER = """<?xml version="1.0" encoding="UTF-8"?>
<l10n_server><languages>
<language><name>Afrikaans</name><code>af</code></language>
<language><name>Zulu</name><code>zu</code></language>
<language><name>English</name><code>en</code></language>
</languages></l10n_server>"""


def history(*versions):
    """versions: a version, or (version, core_compatibility)."""
    releases = "".join(
        "<release><version>%s</version><status>published</status>%s</release>" % (
            version, "<core_compatibility>%s</core_compatibility>" % compatibility
            if compatibility else "")
        for version, compatibility in (v if isinstance(v, tuple) else (v, None) for v in versions))
    return "<project><releases>%s</releases></project>" % releases


@pytest.fixture
def drupal(tmp_path):
    """Local stand-ins for ftp.drupal.org's translations and the release history."""
    root = tmp_path / "drupal"
    (root / "files/translations/all/drupal").mkdir(parents=True)
    (root / "files/translations/all/token").mkdir(parents=True)
    (root / "files/translations/l10n_server.xml").write_text(SERVER)
    for project, versions in (("drupal", ("12.0.0-beta1", "11.4.11", "11.4.10", "10.6.3")),
                              ("token", (("8.x-1.17", "^10.3 || ^11"),
                                         ("8.x-1.12", "^9.2 || ^10")))):
        (root / "release-history" / project).mkdir(parents=True)
        (root / "release-history" / project / "current").write_text(history(*versions))
    for name, lang, pair in (("drupal-11.4.11.af.po", "af", ("Save", "Stoor")),
                             ("drupal-11.4.11.zu.po", "zu", ("Save", "Londoloza")),
                             ("drupal-11.4.11.en.po", "en", ("Save", "Save")),
                             ("token-8.x-1.17.af.po", "af", ("Token", "Teken"))):
        project = name.split("-")[0]
        (root / "files/translations/all" / project / name).write_text(po(lang, pair),
                                                                       encoding="utf-8")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % server.server_address[1]
    yield root, {"base_url": url + "/files/translations", "releases_url": url + "/release-history"}
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


def test_release(drupal):
    root, urls = drupal
    fetcher = Drupal(dict(urls, projects=["drupal", "token"]))
    # 12.0.0-beta1 is a pre-release; 8.x-1.17 isn't.
    assert fetcher.releases() == [("drupal", "11.4.11"), ("token", "8.x-1.17")]


def test_no_stable_release(drupal):
    root, urls = drupal
    (root / "release-history/token/current").write_text(history("2.0.0-rc1"))
    with pytest.raises(FetchError, match="no stable release"):
        Drupal(dict(urls, projects=["token"])).fingerprint()


def test_fetch(run, drupal, pauses):
    root, urls = drupal
    result = run(block("drupal", "drupal", projects=["drupal", "token"], pause=2,
                       languages={"en": ""}, **urls))
    assert result.exit_code == 0, result.output
    # token has no Zulu: skipped.
    assert fetched(run.cache, "drupal") == [
        ("drupal/drupal-11.4.11.af.po", "af"), ("drupal/drupal-11.4.11.zu.po", "zu"),
        ("token/token-8.x-1.17.af.po", "af")]
    assert pauses == [2] * 6


def test_release_changes_fingerprint(run, drupal):
    root, urls = drupal
    text = block("drupal", "drupal", projects=["token"], pause=0, **urls)
    run(text)
    assert run(text, "--check").output == ""
    (root / "release-history/token/current").write_text(history("8.x-1.18", "8.x-1.17"))
    assert run(text, "--check").output == "drupal\n"


def test_server_error_stops_fetch(drupal, tmp_path):
    root, urls = drupal
    (root / "files/translations/l10n_server.xml").write_text("<l10n_server>")
    with pytest.raises(FetchError, match="l10n_server.xml"):
        Drupal(dict(urls, projects=["token"])).fetch(str(tmp_path / "out"))


@pytest.mark.parametrize("compatibility, majors", [
    ("^10.3 || ^11", {10, 11}),
    ("^9.2 || ^10 || ^11", {9, 10, 11}),
    ("~10.1", {10}),
    (">=9.3 <11", {9, 10}),
    ("^11.0", {11}),
    (None, set()),
])
def test_core_majors(compatibility, majors):
    assert Drupal.core_majors(compatibility) == majors


def test_release_for_core(drupal):
    root, urls = drupal
    (root / "release-history/token/current").write_text(history(
        ("8.x-1.18", "^11"), ("8.x-1.17", "^10.3 || ^11"), ("8.x-1.12", "^9.2 || ^10")))
    assert Drupal(dict(urls, projects=["drupal", "token"], core="10")).releases() == [
        ("drupal", "10.6.3"), ("token", "8.x-1.17")]
    assert Drupal(dict(urls, projects=["drupal", "token"], core="11")).releases() == [
        ("drupal", "11.4.11"), ("token", "8.x-1.18")]
    with pytest.raises(FetchError, match="no stable release for core 9"):
        Drupal(dict(urls, projects=["drupal"], core="9")).releases()


@pytest.mark.parametrize("projects", ["drupal", [], ["../x"]])
def test_invalid(tmp_path, projects):
    path = tmp_path / "sources.toml"
    path.write_text(block("d", "drupal", projects=projects))
    with pytest.raises(ValueError, match="projects must list"):
        load_sources(str(path))


def test_invalid_core(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(block("d", "drupal", projects=["drupal"], core="11.x"))
    with pytest.raises(ValueError, match="core must be a Drupal major"):
        load_sources(str(path))


def test_update(run, drupal):
    root, urls = drupal
    result = run(block("drupal", "drupal", projects=["drupal", "token"], pause=0,
                       languages={"en": ""}, **urls), "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 3 files: 3 units (0 skipped)." in result.output
    assert lookup("af", "Token") == ["Teken"]
    assert lookup("zu", "Save") == ["Londoloza"]
