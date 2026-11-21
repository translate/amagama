#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import os

import pytest

from amagama.updater import fetchers
from amagama.updater.fetchers import FetchError, HttpPerLanguage
from amagama.updater.sources import Block


@pytest.fixture
def fetcher(monkeypatch):
    fetcher = HttpPerLanguage({"base_url": "https://example.org/compendia",
                               "path": "{lang}/{lang}.po", "pause": 0})
    monkeypatch.setattr(fetcher, "languages", lambda: ["fr"])

    def server_error(*args):
        raise FetchError("https://example.org/compendia/fr/fr.po: HTTP Error 500", 500)
    monkeypatch.setattr(fetchers, "http_fingerprint", server_error)
    monkeypatch.setattr(fetchers, "download", server_error)
    return fetcher


def test_url(fetcher):
    assert fetcher.url("pt_BR") == "https://example.org/compendia/pt_BR/pt_BR.po"


def test_server_error_stops_fingerprint(fetcher):
    with pytest.raises(FetchError, match="HTTP Error 500"):
        fetcher.fingerprint()


def test_server_error_stops_fetch(fetcher, tmp_path):
    with pytest.raises(FetchError, match="HTTP Error 500"):
        fetcher.fetch(str(tmp_path))


def test_fetch_some_languages(fetcher, monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "languages", lambda: ["de", "fr"])
    monkeypatch.setattr(fetchers, "download", lambda url, path: open(path, "w").close())
    assert fetcher.fetch(str(tmp_path), ["fr"]) == [("fr/fr.po", "fr")]


@pytest.mark.parametrize("parts", [("fr",), ("fr", "fr.po"), ("a/../fr",)])
def test_inside(parts, tmp_path):
    assert fetchers.inside(str(tmp_path), *parts) == os.path.normpath(os.path.join(tmp_path, *parts))


@pytest.mark.parametrize("parts", [("..",), (".",), ("../x",), ("fr", "../../x"), ("/etc",)])
def test_inside_refuses(parts, tmp_path):
    with pytest.raises(FetchError, match="not a path in the fetch"):
        fetchers.inside(str(tmp_path), *parts)


def test_listed_code_outside_fetch(monkeypatch, tmp_path):
    """A listing entry that decodes to ../ isn't fetched outside the fetch."""
    fetcher = HttpPerLanguage({"base_url": "https://example.org/compendia",
                               "path": "{lang}/{lang}.po", "pause": 0})
    monkeypatch.setattr(fetchers, "http_text", lambda url: '<a href="%2e%2e%2fescaped/">x</a>')
    monkeypatch.setattr(fetchers, "download", lambda url, path: open(path, "w").close())
    assert fetcher.languages() == ["../escaped"]
    with pytest.raises(FetchError, match="not a path in the fetch"):
        fetcher.fetch(str(tmp_path / "out"))
    assert not (tmp_path / "escaped").exists()


def test_block_refuses_path_outside_fetch(monkeypatch, tmp_path):
    """A fetcher's path outside the fetch isn't removed as an unknown language."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "notes.txt").write_text("keep")
    block = Block("x", "X", "http", base_url="https://example.org/", path="{lang}/{lang}.po")
    monkeypatch.setattr(block.fetcher, "fetch", lambda dest: [("../victim/notes.txt", "xx")])
    monkeypatch.setattr(block, "fingerprint", lambda cache_dir: None)
    monkeypatch.setattr(block.fetcher, "rotates", False)
    with pytest.raises(FetchError, match="not a path in the fetch"):
        block.fetch(str(tmp_path))
    assert (victim / "notes.txt").read_text() == "keep"
