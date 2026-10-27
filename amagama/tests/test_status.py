#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import json

from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_refetch import site, update  # noqa: F401 - fixtures
from amagama.tests.test_updater import block, git_repo  # noqa: F401 - fixtures
from amagama.updater import status
from amagama.updater.sources import load_sources


def test_sources_status(update):  # noqa: F811 - fixture
    assert update(30).exit_code == 0
    result = CliRunner().invoke(manage, ("sources_status",))
    assert result.exit_code == 0, result.output
    assert "tp: fetched 0.0 days ago, 1 files, 1 languages, 1 units imported\n" in result.output
    assert "1 blocks." in result.output
    result = CliRunner().invoke(manage, ("sources_status", "--json"))
    [found] = json.loads(result.stdout)
    assert (found["name"], found["tags"], found["units"], found["failures"]) == (
        "tp", ["ui"], 1, 0)
    for flag in ("--failing", "--empty", "--never-fetched"):
        result = CliRunner().invoke(manage, ("sources_status", "--json", flag))
        assert json.loads(result.stdout) == []


def test_block_status(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(block("tp", "translationproject"))
    [loaded] = load_sources(str(path))
    cache = str(tmp_path / "cache")
    found = status.block_status(loaded, cache)
    assert (found["fetched"], found["files"], found["units"]) == (None, None, None)
    assert status.describe(found) == ["tp: never fetched"]
    loaded.record_failure(cache, "404")
    loaded.record_failure(cache, "404 again")
    found = status.block_status(loaded, cache, {"tp": 0})
    assert status.is_empty(found)
    assert status.describe(found) == ["tp: never fetched, 0 units imported",
                                      "  failed 2 runs in a row: 404 again"]


def test_check_sources(amagama_config, git_repo, tmp_path):  # noqa: F811 - fixture
    repo, git = git_repo
    sources = tmp_path / "sources.toml"
    sources.write_text(
        block("good", "git", url=repo.as_uri(), files=["po/{lang}.po"])
        + block("masks", "git", url=repo.as_uri(), files=["po/{lang}.po", "gone/{lang}.po"])
        + block("template", "git", url=repo.as_uri(), files=["po/{lang}.po"], format="json",
                template="po/en.json")
        + block("ref", "git", url=repo.as_uri(), ref="gone", files=["po/{lang}.po"])
        + block("off", "translationproject", disabled="duplicates good"))
    amagama_config(UPDATER_SOURCES=str(sources))
    result = CliRunner().invoke(manage, ("check_sources",))
    assert (result.exit_code, result.output) == (0, "5 blocks, 1 disabled.\n")
    result = CliRunner().invoke(manage, ("check_sources", "--remote"))
    assert result.exit_code == 1
    assert result.output.splitlines()[1:] == [
        "masks: filemask gone/{lang}.po matches nothing",
        "template: template po/en.json missing",
        "ref: repository or ref gone not found",
        "4 git blocks checked, 3 with problems."]
    result = CliRunner().invoke(manage, ("check_sources", "--remote", "--block", "good"))
    assert result.exit_code == 0, result.output
    assert result.output.endswith("1 git blocks checked, 0 with problems.\n")


def test_check_remote_reads_each_tree_once(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(
        block("a", "git", url="https://git.example/r", files=["po/{lang}.po"])
        + block("b", "git", url="https://git.example/r", files=["help/{lang}.po"])
        + block("c", "git", url="https://gitlab.gnome.org/GNOME/x",
                mirror="https://github.com/GNOME/x", files=["po/{lang}.po"])
        + block("d", "translationproject"))
    read = []

    def tree(url, ref):
        read.append(url)
        return ["po/af.po"]
    assert status.check_remote(load_sources(str(path)), pause=0, tree=tree) == {
        "b": ["filemask help/{lang}.po matches nothing"]}
    # The mirror is read, not the upstream.
    assert read == ["https://git.example/r", "https://github.com/GNOME/x"]
