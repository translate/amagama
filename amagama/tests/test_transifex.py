#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import subprocess

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_updater import block
from amagama.updater import generate, transifex
from amagama.updater.fetchers import FetchError
from amagama.updater.sources import load_sources

CONFIG = """\
[main]
host = https://app.transifex.com
lang_map = nb: no, pt_BR: pt-br

[o:org:p:app:r:ui]
file_filter = po/<lang>.po
source_file = po/app.pot
source_lang = en
type = PO

[app.docs]
file_filter = .\\docs\\<lang>\\<lang>.po
source_file = docs/en/en.po
source_lang = en_US
type = po

[o:org:p:app:r:android]
file_filter = res/values-<lang>/strings.xml
source_file = res/values/strings.xml
source_lang = en
type = ANDROID
lang_map = de_CH: de-rCH, zh_Hans: zh
"""

TREE = ["po/af.po", "po/no.po", "po/app.pot", "docs/en/en.po", "docs/af/af.po",
        "res/values/strings.xml", "res/values-af/strings.xml", ".tx/config"]


def test_resources():
    found, skipped = transifex.resources(CONFIG, "sub")
    assert skipped == {}
    assert found == [
        {"resource": "ui", "mask": "sub/po/{lang}.po", "format": "po", "template": None,
         "source": "sub/po/app.pot", "languages": {"no": "nb"}},
        # A second <lang> is the same code; ./ and backslashes are paths.
        {"resource": "docs", "mask": "sub/docs/{lang}/*.po", "format": "po", "template": None,
         "source": "sub/docs/en/en.po", "languages": {"no": "nb"}},
        # The section's lang_map adds to [main]'s.
        {"resource": "android", "mask": "sub/res/values-{lang}/strings.xml", "format": "aresource",
         "template": "sub/res/values/strings.xml", "source": None,
         "languages": {"no": "nb", "zh": "zh_CN"}}]


@pytest.mark.parametrize("section, reason", [
    ("file_filter = po/af.po\nsource_lang = en\ntype = PO", "file_filter without <lang>"),
    ("file_filter = po/<lang>.po\nsource_lang = de\ntype = PO", "source not English"),
    ("file_filter = po/<lang>.po\ntype = PO", "source not English"),
    ("file_filter = <lang>.strings\nsource_lang = en\ntype = STRINGS", "type STRINGS"),
    ("file_filter = <lang>.po\nsource_lang = en", "type not given"),
    ("file_filter = <lang>.json\nsource_lang = en\ntype = KEYVALUEJSON",
     "monolingual without a source_file"),
])
def test_resources_skipped(section, reason):
    assert transifex.resources("[p.r]\n" + section) == ([], {reason: 1})


def test_resources_main_settings():
    found, _ = transifex.resources("[main]\ntype = QT\nsource_lang = en\n"
                                   "[p.r]\nfile_filter = t/app_<lang>.ts\n")
    assert [(r["mask"], r["format"]) for r in found] == [("t/app_{lang}.ts", "ts")]


def test_resources_unreadable():
    assert transifex.resources("file_filter = <lang>.po") == ([], {"unreadable .tx/config": 1})


def generated(repositories, **kwargs):
    trees = {"https://github.com/org/app": (TREE, {".tx/config": CONFIG})}
    trees.update(kwargs.pop("trees", {}))
    names = generate.Names(kwargs.pop("taken", ()), kwargs.pop("previous", None))
    return transifex.blocks(repositories, "tx", names=names, pause=0, read=trees.get, **kwargs)


def test_blocks():
    found, skipped = generated(["https://github.com/org/app"])
    assert found == [
        {"name": "tx-app", "title": "app", "source": "git", "url": "https://github.com/org/app",
         "files": ["docs/{lang}/*.po", "po/{lang}.po"], "exclude": ["docs/en/en.po"],
         "sparse": ["docs/*/*.po", "po/*.po"], "languages": {"no": "nb"}},
        {"name": "tx-app-android", "title": "app", "source": "git",
         "url": "https://github.com/org/app", "files": ["res/values-{lang}/strings.xml"],
         "sparse": ["res/values-*/strings.xml", "res/values/strings.xml"], "format": "android",
         "template": "res/values/strings.xml", "languages": {"no": "nb", "zh": "zh_CN"}}]
    assert skipped == {}


def test_blocks_round_trip(tmp_path):
    found, _ = generated(["https://github.com/org/app"])
    path = tmp_path / "t.toml"
    path.write_text(generate.toml(found, "Generated"), encoding="utf-8")
    assert [b.name for b in load_sources(str(path))] == ["tx-app", "tx-app-android"]


def test_blocks_subdirectory():
    tree = ["client/.tx/config", "client/po/af.po"]
    config = "[p.r]\nfile_filter = po/<lang>.po\nsource_lang = en\ntype = PO\n"
    found, _ = generated(["https://gitlab.example/org/tool.git"],
                         trees={"https://gitlab.example/org/tool.git":
                                (tree, {"client/.tx/config": config})})
    assert [(b["name"], b["files"]) for b in found] == [("tx-tool", ["client/po/{lang}.po"])]


def test_blocks_skipped():
    tree = ["po/af.po"]
    nothing = "[p.r]\nfile_filter = l10n/<lang>.po\nsource_lang = en\ntype = PO\n"
    no_template = ("[p.r]\nfile_filter = res/values-<lang>/s.xml\nsource_file = res/values/s.xml\n"
                   "source_lang = en\ntype = ANDROID\n")
    found, skipped = generated(
        ["https://github.com/org/app", "https://git.example/gone", "https://git.example/plain",
         "https://git.example/empty", "https://git.example/old", "https://git.example/broken"],
        covered=["https://github.com/org/app.git"],
        trees={"https://git.example/plain": (tree, {}),
               "https://git.example/empty": (tree, {".tx/config": nothing}),
               "https://git.example/old": (["res/values-af/s.xml"], {".tx/config": no_template}),
               "https://git.example/broken": (tree, {".tx/config": None})})
    assert found == []
    assert skipped == {("repositories", "imported by another block"): 1,
                       ("repositories", "not found"): 1, ("repositories", "no .tx/config"): 1,
                       ("resources", "file_filter matches nothing"): 2,
                       ("resources", "unreadable .tx/config"): 1}


def test_blocks_resources_skipped():
    config = ("[p.ui]\nfile_filter = po/<lang>.po\nsource_lang = de\ntype = PO\n"
              "[p.up]\nfile_filter = ../po/<lang>.po\nsource_lang = en\ntype = PO\n"
              "[p.res]\nfile_filter = res/*/values-<lang>/s.xml\nsource_file = res/values/s.xml\n"
              "source_lang = en\ntype = ANDROID\n")
    tree = ["po/af.po", "res/a/values-af/s.xml", "res/values/s.xml"]
    found, skipped = generated(["https://git.example/app"],
                               trees={"https://git.example/app": (tree, {".tx/config": config})})
    assert found == []
    assert skipped == {
        ("resources", "source not English"): 1, ("resources", "file_filter matches nothing"): 1,
        ("resources", "not a valid block: template must have the same wildcards as each of files"): 1}


def test_blocks_names():
    trees = {"https://git.example/other/app": (TREE, {".tx/config": CONFIG})}
    found, _ = generated(["https://github.com/org/app", "https://git.example/other/app"],
                         taken={"tx-app-android"}, trees=trees)
    # The template's resource names its block; a repository whose name is
    # taken gets its owner's too.
    assert [b["name"] for b in found] == ["tx-app", "tx-app-android-2", "tx-other-app",
                                          "tx-other-app-android"]


def test_blocks_names_kept():
    from amagama.updater.generate import identity
    previous = {identity("https://github.com/org/app", None, "res/values/strings.xml", False, None):
                "tx-mine"}
    found, _ = generated(["https://github.com/org/app"], previous=previous)
    assert [b["name"] for b in found] == ["tx-app", "tx-mine"]


def test_configs():
    assert transifex.configs([".tx/config", "a/.tx/config", "a.tx/config", ".tx/config.bak",
                              "vendor/lib/.tx/config", "web/node_modules/x/.tx/config"]) \
        == [".tx/config", "a/.tx/config"]


def test_blocks_licences_and_mirrors():
    url = "https://gitlab.example/org/app"
    found, _ = generated([url], licences={"gitlab.example/org/app": "MIT"},
                         mirrors={url: "https://github.com/org/app"})
    assert {(b["url"], b["licence"]) for b in found} == {(url, "MIT")}


@pytest.fixture
def repository(tmp_path):
    """A local repository translated on Transifex, in two directories."""
    repo = tmp_path / "repo"
    for directory in ("", "web/"):
        (repo / directory / ".tx").mkdir(parents=True)
        (repo / directory / ".tx/config").write_text(
            "[p.r]\nfile_filter = po/<lang>.po\nsource_lang = en\ntype = PO\n")
        (repo / directory / "po").mkdir()
        (repo / directory / "po/af.po").write_text("af")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m tx"], cwd=repo, check=True)
    return repo.as_uri()


def test_repository(repository, tmp_path):
    paths, texts = transifex.repository(repository)
    assert sorted(texts) == [".tx/config", "web/.tx/config"]
    assert "web/po/af.po" in paths
    assert transifex.repository((tmp_path / "nothing").as_uri()) is None


def test_repository_unreadable_config(repository, monkeypatch):
    def unreadable(directory, path):
        raise FetchError("git show failed")
    monkeypatch.setattr(transifex, "_read", unreadable)
    assert transifex.repository(repository)[1] == {".tx/config": None, "web/.tx/config": None}


def test_command(amagama_config, repository, tmp_path):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("tool", "git", url="https://git.example/tool", files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources))
    listed = tmp_path / "repositories.txt"
    listed.write_text("# Found by code search\nhttps://git.example/tool\n\n")
    output = tmp_path / "transifex.toml"
    result = CliRunner().invoke(manage, ("transifex_sources", repository, "--prefix", "tx",
                                         "--from-file", str(listed), "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert "1 blocks." in result.output
    assert "Skipped 1 repositories: imported by another block." in result.output
    text = output.read_text()
    assert text.startswith("# Generated by: amagama-manage transifex_sources --prefix tx %s "
                           "--from-file %s --min-age-days 60 --tag discovered\n" % (repository, listed))
    [found] = load_sources(str(output))
    assert (found.name, found.min_age_days) == ("tx-repo", 60)
    assert found.selection.mask_texts == ["po/{lang}.po", "web/po/{lang}.po"]


def test_command_needs_repositories(amagama_config, tmp_path):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    result = CliRunner().invoke(manage, ("transifex_sources", "--prefix", "tx"))
    assert result.exit_code == 2
    assert "Give repositories, or --from-file." in result.output
