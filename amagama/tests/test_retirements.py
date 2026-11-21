#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import os

import pytest
from click.testing import CliRunner

from amagama.cli import manage

from amagama.tests.conftest import RETIREMENTS
from amagama.tests.test_updater import block
from amagama.tests import test_pairing
from amagama.updater import retirements
from amagama.updater.fetchers import FetchError
from amagama.updater.languages import described_retired, normalise_language, not_language
from amagama.updater.retirements import refresh

NOW = datetime.datetime(2026, 11, 21, tzinfo=datetime.timezone.utc)


def test_parse():
    table = retirements.parse(RETIREMENTS)
    assert table["xzc"] == retirements.Retirement("Changed", "C", ("deu",), "2010-01-01")
    assert table["xzs"].successors == ("afr", "nld")
    assert table["xzn"].successors == ()
    with pytest.raises(ValueError):
        retirements.parse("<html>Not found</html>")


@pytest.mark.parametrize("code, expected", [
    # One successor: that language.
    ("xzc", "de"), ("xzd_BE", "nl_BE"), ("xzq", "zu"),
    # Split or never in use: kept, for a person to map.
    ("xzs", "xzs"), ("xzn", "xzn"),
    ("xzz", None),
])
def test_normalise_retired(retired, code, expected):
    assert normalise_language(code) == expected


def test_unknown_without_table():
    assert normalise_language("xzc") is None
    assert not_language("xzc") == "not an ISO 639 code"


def test_described_retired(retired):
    assert described_retired({"xzc": "de", "xzs": "xzs"}) == (
        "xzc (retired 2010-01-01, changed (deu); now de), "
        "xzs (retired 2013-01-01, split (afr, nld); kept: a block's languages can map it)")


def test_refresh_downloads(tmp_path):
    path = tmp_path / "cache" / retirements.FILENAME
    fetched = []

    def fetch(url):
        fetched.append(url)
        return RETIREMENTS
    assert refresh(str(path), fetch, NOW) is None
    assert fetched == [retirements.URL]
    assert path.read_text(encoding="utf-8") == RETIREMENTS
    # A recent copy isn't downloaded again.
    assert refresh(str(path), fetch) is None
    assert len(fetched) == 1


def test_refresh_old_copy(tmp_path):
    path = tmp_path / retirements.FILENAME
    path.write_text(RETIREMENTS, encoding="utf-8")
    old = (NOW - datetime.timedelta(days=retirements.MAX_AGE_DAYS + 1)).timestamp()
    os.utime(path, (old, old))

    def failing(url):
        raise FetchError("%s: HTTP Error 503" % url)
    note = refresh(str(path), failing, NOW)
    assert note.startswith("Couldn't download ISO 639-3's retired codes")
    assert note.endswith("using the copy of 2026-08-22.")
    assert path.read_text(encoding="utf-8") == RETIREMENTS
    # A page that isn't the table keeps the copy too.
    assert "using the copy" in refresh(str(path), lambda url: "<html></html>", NOW)
    assert refresh(str(path), lambda url: RETIREMENTS.replace("xzn", "xzo"), NOW) is None
    assert "xzo" in path.read_text(encoding="utf-8")


def test_refresh_relative_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert refresh(retirements.FILENAME, lambda url: RETIREMENTS, NOW) is None
    assert (tmp_path / retirements.FILENAME).read_text(encoding="utf-8") == RETIREMENTS


def test_refresh_unwritable(tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("")
    note = refresh(str(blocked / retirements.FILENAME), lambda url: RETIREMENTS, NOW)
    assert note.startswith("Couldn't save ISO 639-3's retired codes in ")


def test_update_unwritable_cache(amagama_config, tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("")
    amagama_config(ISO639_RETIREMENTS_FILE=str(blocked / retirements.FILENAME),
                   UPDATER_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setattr(retirements, "refresh",
                        lambda filename: refresh(filename, lambda url: RETIREMENTS))
    result = CliRunner().invoke(manage, ("update", "-s", "en", "--no-fetch"))
    assert "Couldn't save ISO 639-3's retired codes" in result.output
    assert "Traceback" not in result.output


def test_new_app_forgets_cached_codes(retired, amagama_config, tmp_path):
    from amagama.application import amagama_server_factory
    from amagama.tmdb import lang_to_table
    amagama_config(ISO639_RETIREMENTS_FILE=str(retired))
    amagama_server_factory()
    assert lang_to_table("xzc") == "de"
    amagama_config(ISO639_RETIREMENTS_FILE=str(tmp_path / "none.tab"))
    amagama_server_factory()
    assert lang_to_table("xzc") is None


def test_refresh_no_copy(tmp_path):
    path = tmp_path / retirements.FILENAME

    def failing(url):
        raise FetchError("offline")
    assert refresh(str(path), failing, NOW) == \
        "Couldn't download ISO 639-3's retired codes (offline); they aren't recognised."
    assert not path.exists()
    assert retirements.load(str(path)) == {}


update = test_pairing.update


def test_update_retired_codes(update, retired, amagama_config, tmp_path):
    amagama_config(ISO639_RETIREMENTS_FILE=str(retired))
    po = 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\nmsgid "Save"\nmsgstr "%s"\n'
    url = test_pairing.git_repo(tmp_path / "repo", {
        "po/xzc.po": po % "Speichern", "po/xzs.po": po % "Stoor"})
    result = update(block("game", "git", url=url, files=["po/{lang}.po"]))
    assert result.exit_code == 0, result.output
    assert ("Retired codes in game: xzc (retired 2010-01-01, changed (deu); now de), "
            "xzs (retired 2013-01-01, split (afr, nld); kept: a block's languages can map it)"
            ) in result.output
    assert test_pairing.lookup("de", "Save") == ["Speichern"]
    assert test_pairing.lookup("xzs", "Save") == ["Stoor"]


def test_web_app_reads_the_table(retired, amagama_config):
    amagama_config(ISO639_RETIREMENTS_FILE=str(retired))
    from amagama.application import amagama_server_factory
    from amagama.updater import languages
    languages.use_retirements({})
    amagama_server_factory()
    assert normalise_language("xzs") == "xzs"
