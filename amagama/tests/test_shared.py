#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import os

from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_transifex import repository  # noqa: F401 - fixture
from amagama.tests.test_updater import block, git_repo, run  # noqa: F401 - fixtures
from amagama.updater import fetchers, generate
from amagama.updater.fetchers import FetchError, Git
from amagama.updater.sources import load_sources, unused_repositories


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


def test_unused_repositories(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(block("a", "git", url="https://git.example/org/a", files=["po/{lang}.po"])
                    + block("b", "git", url="https://git.example/org/b", files=["po/{lang}.po"],
                            disabled="kept for later")
                    + block("c", "translationproject"))
    store = tmp_path / "cache/.repos/git.example/org"
    for name in ("a.git", "b.git", "gone.git"):
        (store / name).mkdir(parents=True)
        (store / name / "HEAD").write_text("ref: refs/heads/main\n")
    (store / "gone.git/refs").mkdir()
    (store / "gone.git/refs/HEAD").write_text("not a repository")
    assert unused_repositories(load_sources(str(path), disabled=True), str(tmp_path / "cache")) \
        == [str(store / "gone.git")]


def test_fetch_sources_prunes(run, git_repo):  # noqa: F811 - fixture
    repo, git = git_repo
    text = block("a", "git", url=repo.as_uri(), files=["po/{lang}.po"])
    assert run(text).exit_code == 0
    stale = run.cache / ".repos/git.example/old.git"
    stale.mkdir(parents=True)
    (stale / "HEAD").write_text("ref: refs/heads/main\n")
    result = run(text, "--block", "a")
    assert stale.exists()
    result = run(text)
    assert result.exit_code == 0, result.output
    assert "Removed 1 repositories no block uses." in result.output
    assert not stale.exists()
    assert os.path.isdir(Git({"url": repo.as_uri()}).repository(str(run.cache / ".repos")))


def test_clone_shared(git_repo, tmp_path, monkeypatch):  # noqa: F811 - fixture
    repo, git = git_repo
    store = str(tmp_path / "repos")
    with generate.shared_repositories(store):
        with generate.clone(repo.as_uri(), None) as (directory, paths):
            assert sorted(paths) == ["po/af.po", "po/zu.po"]
            # A worktree of the shared repository while it's in use.
            assert os.path.isfile(os.path.join(directory, ".git"))
        shared = Git({"url": repo.as_uri()}).repository(store)
        assert os.path.isfile(os.path.join(shared, "HEAD"))
        with generate.clone((tmp_path / "gone").as_uri(), None) as cloned:
            assert cloned is None

        # The shared repository failing, a throwaway clone is made.
        def failing(self, dest):
            raise FetchError("locked")
        monkeypatch.setattr(Git, "worktree", failing)
        with generate.clone(repo.as_uri(), "main") as (directory, paths):
            assert sorted(paths) == ["po/af.po", "po/zu.po"]
            assert os.path.isdir(os.path.join(directory, ".git"))


def test_generator_uses_cache(amagama_config, repository, tmp_path):  # noqa: F811 - fixture
    cache = tmp_path / "cache"
    cache.mkdir()
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"), UPDATER_CACHE_DIR=str(cache))
    result = CliRunner().invoke(manage, ("transifex_sources", repository, "--prefix", "tx"))
    assert result.exit_code == 0, result.output
    assert os.path.isfile(os.path.join(Git({"url": repository}).repository(str(cache / ".repos")),
                                       "HEAD"))
