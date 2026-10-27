#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import json
import subprocess

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_updater import block, git_repo, run  # noqa: F401 - fixtures
from amagama.updater.fetchers import FetchError, Git
from amagama.updater.sources import load_sources


@pytest.fixture
def mirrored(git_repo, tmp_path):  # noqa: F811 - fixture
    """(upstream repo, git in it, mirror path, a block of both)."""
    repo, git = git_repo
    mirror = tmp_path / "mirror.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(repo), str(mirror)], check=True)
    path = tmp_path / "sources.toml"
    path.write_text(block("x", "git", url=repo.as_uri(), mirror=mirror.as_uri(),
                          files=["po/{lang}.po"]))
    [loaded] = load_sources(str(path))
    return repo, git, mirror, loaded


def head(repo):
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def commit(repo, git, text):
    (repo / "po/af.po").write_text(text)
    git("commit", "-q", "-am", text)
    return head(repo)


def age(cache, days):
    """Make the mirror's record days old."""
    path = cache / "x.mirror"
    state = json.loads(path.read_text())
    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
    path.write_text(json.dumps(dict(state, since=since.isoformat())))


def test_level_mirror_used(mirrored, tmp_path):
    repo, git, mirror, loaded = mirrored
    cache = tmp_path / "cache"
    assert loaded.fingerprint(str(cache)) == head(repo)
    assert loaded.fetcher.source == mirror.as_uri()
    assert loaded.mirror_note(str(cache)) is None
    loaded.fetch(str(cache))
    origin = subprocess.run(["git", "-C", loaded.fetcher.repository(), "remote", "get-url",
                             "origin"], check=True, capture_output=True, text=True).stdout
    assert origin.strip() == mirror.as_uri()


def test_behind_mirror(mirrored, tmp_path):
    repo, git, mirror, loaded = mirrored
    cache = tmp_path / "cache"
    old = head(mirror)
    new = commit(repo, git, "af 2")
    # Behind for less than mirror_lag_days: the mirror's commit.
    assert loaded.fingerprint(str(cache)) == old
    assert loaded.fetcher.source == mirror.as_uri()
    age(cache, 15)
    assert loaded.fingerprint(str(cache)) == new
    assert loaded.fetcher.source == repo.as_uri()
    assert "behind and unchanged since" in loaded.mirror_note(str(cache))
    assert "kept current" not in loaded.mirror_note(str(cache))
    age(cache, 30)
    assert loaded.mirror_note(str(cache)).endswith("is it kept current?")
    # The mirror moves, though not level yet: used again.
    commit(repo, git, "af 3")
    subprocess.run(["git", "-C", str(mirror), "fetch", "-q", str(repo), new + ":refs/heads/main"],
                   check=True)
    assert loaded.fingerprint(str(cache)) == new
    assert loaded.fetcher.source == mirror.as_uri()
    assert loaded.mirror_note(str(cache)) is None
    # Level again: the record goes.
    subprocess.run(["git", "-C", str(mirror), "fetch", "-q", str(repo), "main:refs/heads/main"],
                   check=True)
    loaded.fingerprint(str(cache))
    assert not (cache / "x.mirror").exists()


def test_unreachable_mirror(git_repo, tmp_path):  # noqa: F811 - fixture
    repo, git = git_repo
    path = tmp_path / "sources.toml"
    path.write_text(block("x", "git", url=repo.as_uri(), mirror=(tmp_path / "gone").as_uri(),
                          files=["po/{lang}.po"]))
    [loaded] = load_sources(str(path))
    cache = tmp_path / "cache"
    assert loaded.fingerprint(str(cache)) == head(repo)
    assert loaded.fetcher.source == repo.as_uri()
    assert loaded.mirror_note(str(cache)).startswith("mirror unreachable, fetched from upstream")


def test_mirror_lag_days(mirrored, tmp_path):
    repo, git, mirror, _ = mirrored
    path = tmp_path / "lag.toml"
    path.write_text('[defaults]\nmirror_lag_days = 0\n\n'
                    + block("x", "git", url=repo.as_uri(), mirror=mirror.as_uri(),
                            files=["po/{lang}.po"])
                    + block("y", "translationproject"))
    loaded = load_sources(str(path))
    assert [b.mirror_lag_days for b in loaded] == [0, 0]
    new = commit(repo, git, "af 2")
    assert loaded[0].fingerprint(str(tmp_path / "cache")) == new
    assert loaded[0].fetcher.source == repo.as_uri()


@pytest.mark.parametrize("settings, error", [
    ({"mirror": "file:///same"}, "mirror must be the URL of a copy of url"),
    ({"mirror": "nowhere"}, "mirror must be the URL of a copy of url"),
    ({"mirror_lag_days": -1}, "mirror_lag_days must be a whole number of days"),
])
def test_mirror_checked(tmp_path, settings, error):
    path = tmp_path / "sources.toml"
    path.write_text(block("x", "git", url="file:///same", files=["po/{lang}.po"], **settings))
    with pytest.raises(ValueError, match=error):
        load_sources(str(path))


def test_fetch_sources_reports(run, mirrored, tmp_path):  # noqa: F811 - fixture
    repo, git, mirror, _ = mirrored
    text = block("x", "git", url=repo.as_uri(), mirror=mirror.as_uri(), files=["po/{lang}.po"])
    assert run(text).exit_code == 0
    commit(repo, git, "af 2")
    assert run(text, "--block", "x").exit_code == 0
    age(run.cache, 15)
    result = run(text, "--block", "x")
    assert result.exit_code == 0, result.output
    assert "x: mirror %s behind and unchanged since" % mirror.as_uri() in result.output


def test_update_reports(amagama_config, mirrored, tmp_path):
    repo, git, mirror, _ = mirrored
    sources = tmp_path / "update.toml"
    sources.write_text(block("x", "git", url=repo.as_uri(), mirror=mirror.as_uri(),
                             files=["po/{lang}.po"]))
    cache = tmp_path / "cache"
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(cache), UPDATER_WORKERS=1)

    def update():
        result = CliRunner().invoke(manage, ("update", "-s", "en"))
        assert result.exit_code == 0, result.output
        return result.output
    assert "Fetched x: 2 files." in update()
    commit(repo, git, "af 2")
    assert "Nothing changed." in update()
    age(cache, 15)
    assert "x: mirror %s behind and unchanged since" % mirror.as_uri() in update()


def test_missing_ref(git_repo):  # noqa: F811 - fixture
    repo, git = git_repo
    with pytest.raises(FetchError, match="no ref gone"):
        Git({"url": repo.as_uri(), "ref": "gone"}).commit(repo.as_uri())
