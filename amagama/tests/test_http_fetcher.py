import pytest

from amagama.updater import fetchers
from amagama.updater.fetchers import FetchError, HttpPerLanguage


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
