import subprocess

import pytest

from amagama.updater import fetchers
from amagama.updater.fetchers import FetchError, Svn


def svn(*args):
    subprocess.run(args, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A Subversion repository laid out like KDE's l10n-kf6."""
    path = tmp_path / "svnrepo"
    svn("svnadmin", "create", str(path))
    tree = tmp_path / "tree"
    for name, text in {
        "af/messages/app/app.po": "af",
        "af/docmessages/app/app.po": "af docs",
        "de/messages/app/app.po": "de",
        "de/messages/lib/lib.po": "de",
        "zu/docmessages/app/app.po": "zu docs",
        "teamnames": "af=Afrikaans",
    }.items():
        (tree / name).parent.mkdir(parents=True, exist_ok=True)
        (tree / name).write_text(text)
    url = path.as_uri() + "/trunk/l10n-kf6"
    svn("svn", "import", "-q", "-m", "l10n", str(tree), url)
    return url, tmp_path


def test_languages_are_directories(repo):
    url, tmp_path = repo
    assert Svn({"url": url}).languages() == ["af", "de", "zu"]


def test_fetch_dirs(repo):
    url, tmp_path = repo
    files = Svn({"url": url, "dirs": ["messages"], "pause": 0}).fetch(str(tmp_path / "dest"))
    # zu has no messages; docmessages aren't asked for.
    assert sorted(files) == [("af/messages/app/app.po", "af"), ("de/messages/app/app.po", "de"),
                             ("de/messages/lib/lib.po", "de")]


def test_fetch_whole_language(repo):
    url, tmp_path = repo
    files = Svn({"url": url, "pause": 0}).fetch(str(tmp_path / "dest"))
    assert ("zu/docmessages/app/app.po", "zu") in files
    assert len(files) == 5


def test_fingerprint_changes_on_commit(repo):
    url, tmp_path = repo
    fetcher = Svn({"url": url})
    before = fetcher.fingerprint()
    (tmp_path / "new.po").write_text("new")
    svn("svn", "import", "-q", "-m", "more", str(tmp_path / "new.po"), url + "/af/messages/new.po")
    assert fetcher.fingerprint() != before


def test_unreachable(tmp_path):
    with pytest.raises(FetchError, match="svn info"):
        Svn({"url": (tmp_path / "missing").as_uri()}).fingerprint()


def test_export_failure_stops_fetch(repo, monkeypatch):
    url, tmp_path = repo
    fetcher = Svn({"url": url, "dirs": ["messages"], "pause": 0})
    fetcher.languages()

    def broken(tool, *args):
        raise FetchError("svn export: E175002: Connection reset")
    monkeypatch.setattr(fetchers, "run_tool", broken)
    with pytest.raises(FetchError, match="Connection reset"):
        fetcher.fetch(str(tmp_path / "dest"))
