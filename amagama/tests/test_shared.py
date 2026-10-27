#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


from amagama.tests.test_updater import block, git_repo, run  # noqa: F401 - fixtures
from amagama.updater import fetchers
from amagama.updater.fetchers import Git


def counted(monkeypatch):
    """The ls-remote calls Git makes."""
    calls = []
    git = Git._git

    def counting(self, *args, clone=None):
        if args[0] == "ls-remote":
            calls.append(args[1])
        return git(self, *args, clone=clone)
    monkeypatch.setattr(Git, "_git", counting)
    return calls


def test_remembered_commits(git_repo, monkeypatch):  # noqa: F811 - fixture
    repo, git = git_repo
    calls = counted(monkeypatch)
    one, two = Git({"url": repo.as_uri()}), Git({"url": repo.as_uri()})
    with fetchers.remembered_commits():
        assert one.fingerprint() == two.fingerprint()
        assert Git({"url": repo.as_uri(), "ref": "main"}).fingerprint() == one.fingerprint()
    assert len(calls) == 2
    one.fingerprint()
    one.fingerprint()
    assert len(calls) == 4


def test_fetch_sources_asks_once(run, git_repo, monkeypatch):  # noqa: F811 - fixture
    repo, git = git_repo
    text = (block("a", "git", url=repo.as_uri(), files=["po/{lang}.po"])
            + block("b", "git", url=repo.as_uri(), files=["po/{lang}.po"], exclude=["po/zu.po"]))
    calls = counted(monkeypatch)
    result = run(text)
    assert result.exit_code == 0, result.output
    # Each block checks the commit when it fetches; the repository is asked once.
    assert calls == [repo.as_uri()]
