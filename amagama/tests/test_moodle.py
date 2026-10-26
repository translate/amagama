#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import functools
import hashlib
import json
import os
import http.server
import threading
import zipfile

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_pairing import lookup
from amagama.tests.test_updater import QuietHandler, block, fetched
from amagama.updater.fetchers import FetchError, Moodle
from amagama.updater.sources import load_sources


def strings(**pairs):
    return "<?php\n" + "".join("$string['%s'] = '%s';\n" % pair for pair in pairs.items())


@pytest.fixture
def moodle(tmp_path):
    """A local stand-in for packaging.moodle.org's 5.1 language packs."""
    root = tmp_path / "moodle"
    release = root / "5.1"
    release.mkdir(parents=True)
    packs = {
        "af": {"moodle.php": strings(save="Stoor veranderinge", course="Kursus"),
               "langconfig.php": strings(thislanguage="Afrikaans")},
        # Overrides for Moodle Workplace, German like de.
        "de_wp": {"moodle.php": strings(course="Kurs")},
        # Listed alphabetically, as Moodle does.
        "en": {"moodle.php": strings(save="Save changes", course="Course"),
               "langconfig.php": strings(thislanguage="English")},
    }
    index = []
    for lang, files in packs.items():
        with zipfile.ZipFile(release / ("%s.zip" % lang), "w") as archive:
            for name, text in files.items():
                archive.writestr("%s/%s" % (lang, name), text)
        md5 = hashlib.md5((release / ("%s.zip" % lang)).read_bytes()).hexdigest()
        index.append("%s,%s,%s" % (lang, md5, lang))
    (release / "languages.md5").write_text("\n".join(index) + "\n")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, "http://127.0.0.1:%d" % server.server_address[1]
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


def moodle_block(url, **extra):
    return block("moodle", "moodle", release="5.1", base_url=url, pause=0,
                 files=["{lang}/*.php"], exclude=["*/langconfig.php"], format="php",
                 template="en/*.php", languages={"de_wp": "de"}, **extra)


def test_fetch(run, moodle, pauses):
    root, url = moodle
    result = run(moodle_block(url, max_age_days=0).replace("pause = 0", "pause = 2"))
    assert result.exit_code == 0, result.output
    # The English pack is the template; langconfig isn't a translation.
    assert fetched(run.cache, "moodle") == [
        ("af/moodle.php", "af", "en/moodle.php"), ("de_wp/moodle.php", "de", "en/moodle.php")]
    assert pauses == [2, 2, 2]


def test_md5_mismatch(moodle, tmp_path):
    root, url = moodle
    with zipfile.ZipFile(root / "5.1/af.zip", "w") as archive:
        archive.writestr("af/moodle.php", strings(save="Anders"))
    with pytest.raises(FetchError, match="MD5 doesn't match"):
        Moodle({"release": "5.1", "base_url": url, "pause": 0}).fetch(str(tmp_path / "out"))


def test_bad_index(moodle):
    root, url = moodle
    (root / "5.1/languages.md5").write_text("<html>Not found</html>\n")
    with pytest.raises(FetchError, match="not a language pack index"):
        Moodle({"release": "5.1", "base_url": url}).fingerprint()


def test_index_change(run, moodle):
    root, url = moodle
    text = moodle_block(url, max_age_days=0)
    run(text)
    assert run(text, "--check").output == ""
    index = root / "5.1/languages.md5"
    index.write_text(index.read_text() + "zu,%s,zu\n" % ("0" * 32))
    assert run(text, "--check").output == "moodle\n"


@pytest.mark.parametrize("release", ["5", "latest", 5.1])
def test_invalid(tmp_path, release):
    path = tmp_path / "sources.toml"
    path.write_text(block("m", "moodle", release=release, files=["{lang}/*.php"]))
    with pytest.raises(ValueError, match="release must be a Moodle release"):
        load_sources(str(path))


def test_update(run, moodle):
    root, url = moodle
    result = run(moodle_block(url, max_age_days=0), "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 2 files: 3 units (0 skipped)." in result.output
    assert lookup("af", "Save changes") == ["Stoor veranderinge"]
    assert lookup("de", "Course") == ["Kurs"]


def test_fetch_some_languages(moodle, tmp_path):
    root, url = moodle
    fetcher = Moodle({"release": "5.1", "base_url": url, "pause": 0})
    lang = fetcher.languages()[0]
    files = fetcher.fetch(str(tmp_path / "out"), [lang])
    # English, the template, comes with any language.
    assert files and {code for path, code in files} == {lang, "en"}


def test_rotation_fetches_english(run, moodle):
    # A day's share is one language; English, the template, comes too.
    root, url = moodle
    result = run(moodle_block(url, max_age_days=3), "-s", "en", command="update")
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 2 units (0 skipped)." in result.output
    assert lookup("af", "Save changes") == ["Stoor veranderinge"]


def test_missing_carried_file_fails_the_block(run, moodle):
    root, url = moodle
    text = moodle_block(url, max_age_days=3)
    assert run(text).exit_code == 0
    os.remove(run.cache / "moodle/af/moodle.php.zst")
    state = run.cache / "moodle/fetched.json"
    fetched_state = json.loads(state.read_text())
    # Fetched a day ago, af most recently: af isn't due, so it's carried.
    fetched_state["fetched"] = (datetime.datetime.now(datetime.timezone.utc)
                                - datetime.timedelta(days=1)).isoformat()
    fetched_state["language_fetched"] = {lang: "2000-01-02T00:00:00+00:00"
                                         for lang in fetched_state["language_fetched"]}
    fetched_state["language_fetched"]["af"] = "2030-01-01T00:00:00+00:00"
    state.write_text(json.dumps(fetched_state))
    result = run(text)
    assert result.exit_code == 1
    assert "moodle: failed: af/moodle.php.zst is missing from the last fetch" in result.output
