#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import subprocess

import pytest

from amagama.updater import crowdin
from amagama.updater.generate import Names, identity, toml
from amagama.updater.sources import load_sources

CONFIG = """\
base_path: .
preserve_hierarchy: true
files:
  - source: /po/app.pot
    translation: /po/%two_letters_code%.po
    languages_mapping:
      two_letters_code:
        pt-BR: pt_BR
        nb: no
  - source: /res/values/strings.xml
    translation: /res/values-%android_code%/%original_file_name%
  - source: ./web\\\\locale\\\\en\\\\*.json
    translation: /web/locale/%locale%/%file_name%.%file_extension%
"""

TREE = ["po/app.pot", "po/af.po", "po/no.po", "res/values/strings.xml",
        "res/values-af/strings.xml", "web/locale/en/app.json", "web/locale/af/app.json",
        "crowdin.yml"]


def test_files():
    found, skipped = crowdin.files(CONFIG, "sub")
    assert skipped == {}
    assert found == [
        {"name": "app", "mask": "sub/po/{lang}.po", "format": "po", "template": None,
         "source": "sub/po/app.pot", "languages": {"no": "nb"}},
        {"name": "strings", "mask": "sub/res/values-{lang}/strings.xml", "format": "android",
         "template": "sub/res/values/strings.xml", "source": None, "languages": {}},
        # ./ and backslashes are paths; the source's * stays in its translations.
        {"name": "locale", "mask": "sub/web/locale/{lang}/*.json", "format": "json",
         "template": "sub/web/locale/en/*.json", "source": None, "languages": {}}]


@pytest.mark.parametrize("translation, source, expected", [
    ("/%two_letters_code%/LC_MESSAGES/%file_name%.po", "app.pot", "{lang}/LC_MESSAGES/app.po"),
    # The source's directory, so this source's translations sit beside it.
    ("%original_path%/%locale%/%original_file_name%", "src/*.json", "src/{lang}/*.json"),
    ("/mac/%osx_code%/Main.strings", "mac/en.lproj/Main.strings", "mac/{lang}.lproj/Main.strings"),
    # A second language placeholder is the same code.
    ("/%locale%/%locale_with_underscore%.json", "en.json", "{lang}/*.json"),
])
def test_mask(translation, source, expected):
    assert crowdin.mask(translation, source)[0] == expected


@pytest.mark.parametrize("translation, reason", [
    ("/%language%/app.json", "placeholder %language%"),
    ("/en/app.json", "translation without a language"),
])
def test_mask_left_out(translation, reason):
    assert crowdin.mask(translation, "app.json") == (None, reason)


def test_languages():
    mapping = {"osx_code": {"zh-Hans": "zh-Hans.lproj", "pt-BR": "pt-PT.lproj"},
               "locale": {"es-ES": "es"}}
    # Only the codes that wouldn't otherwise be Crowdin's language.
    assert crowdin._languages(mapping, "osx_code") == {"pt-PT": "pt_BR"}
    assert crowdin._languages(mapping, "two_letters_code") == {}
    assert crowdin._languages("not a mapping", "locale") == {}


@pytest.mark.parametrize("config, reason", [
    ("files: [{source: a.json}]", "file without source or translation"),
    ("files: [{source: a.strings, translation: '%locale%/a.strings'}]", "type .strings"),
    ("files: [{source: a.yml, translation: '%locale%/a.yml', type: yaml}]", "type yaml"),
    ("files: [{source: a.json, translation: '%language%.json'}]", "placeholder %language%"),
])
def test_files_skipped(config, reason):
    assert crowdin.files(config) == ([], {reason: 1})


@pytest.mark.parametrize("config", ["files: [", "project_id: 1", "files: nothing"])
def test_files_unreadable(config):
    assert crowdin.files(config) == ([], {"unreadable crowdin.yml": 1})


def test_files_type_and_base_path():
    config = ("base_path: app\nfiles:\n  - source: /i18n/en.txt\n    type: json\n"
              "    translation: /i18n/%two_letters_code%.txt\n")
    [found], _ = crowdin.files(config, "client")
    assert (found["mask"], found["format"], found["template"]) == (
        "client/app/i18n/{lang}.txt", "json", "client/app/i18n/en.txt")


def test_configs():
    assert crowdin.configs(["crowdin.yml", "a/crowdin.yaml", ".crowdin.yml", "a/.crowdin.yml",
                            "vendor/x/crowdin.yml", "crowdin.yml.bak", "mycrowdin.yml"]) == [
        "crowdin.yml", "a/crowdin.yaml", ".crowdin.yml", "a/.crowdin.yml"]


def generated(repositories, **kwargs):
    trees = {"https://github.com/org/app": (TREE, {"crowdin.yml": CONFIG})}
    trees.update(kwargs.pop("trees", {}))
    return crowdin.blocks(repositories, "crowdin", pause=0, read=trees.get, **kwargs)


def test_blocks():
    found, skipped = generated(["https://github.com/org/app"])
    assert found == [
        {"name": "crowdin-app", "title": "app", "source": "git", "url": "https://github.com/org/app",
         "files": ["po/{lang}.po"], "sparse": ["po/*.po"], "languages": {"no": "nb"}},
        {"name": "crowdin-app-strings", "title": "app", "source": "git",
         "url": "https://github.com/org/app", "files": ["res/values-{lang}/strings.xml"],
         "sparse": ["res/values-*/strings.xml", "res/values/strings.xml"], "format": "android",
         "template": "res/values/strings.xml"},
        {"name": "crowdin-app-locale", "title": "app", "source": "git",
         "url": "https://github.com/org/app",
         "files": ["web/locale/{lang}/*.json"],
         "sparse": ["web/locale/*/*.json", "web/locale/en/*.json"],
         "format": "json", "template": "web/locale/en/*.json"}]
    assert skipped == {}


def test_blocks_round_trip(tmp_path):
    found, _ = generated(["https://github.com/org/app"])
    path = tmp_path / "c.toml"
    path.write_text(toml(found, "Generated"), encoding="utf-8")
    assert [b.name for b in load_sources(str(path))] == [
        "crowdin-app", "crowdin-app-strings", "crowdin-app-locale"]


def test_blocks_skipped():
    stale = "files: [{source: /locale/*.po, translation: '/locale/%two_letters_code%/%original_file_name%'}]"
    found, skipped = generated(
        ["https://github.com/org/app", "https://git.example/gone", "https://git.example/plain",
         "https://git.example/stale", "https://git.example/broken"],
        covered=["https://github.com/org/app.git"],
        trees={"https://git.example/plain": (["po/af.po"], {}),
               "https://git.example/stale": (["README"], {"crowdin.yml": stale}),
               "https://git.example/broken": (["po/af.po"], {"crowdin.yml": None})})
    assert found == []
    assert skipped == {("repositories", "imported by another block"): 1,
                       ("repositories", "not found"): 1, ("repositories", "no crowdin.yml"): 1,
                       ("files", "translation matches nothing"): 1,
                       ("files", "unreadable crowdin.yml"): 1}


def test_blocks_source_excluded():
    config = "files: [{source: /po/en.po, translation: '/po/%two_letters_code%.po'}]"
    found, _ = generated(["https://git.example/o/tool"], trees={
        "https://git.example/o/tool": (["po/en.po", "po/af.po"], {"crowdin.yml": config})})
    assert [(b["files"], b["exclude"]) for b in found] == [(["po/{lang}.po"], ["po/en.po"])]


def test_blocks_names_kept():
    previous = {identity("https://github.com/org/app", None, "res/values/strings.xml", False, None):
                "crowdin-mine"}
    found, _ = generated(["https://github.com/org/app"], names=Names(previous=previous))
    assert [b["name"] for b in found] == ["crowdin-app", "crowdin-mine", "crowdin-app-locale"]


@pytest.fixture
def crowdin_repository(tmp_path):
    """A local repository translated on Crowdin, with a vendored copy."""
    repo = tmp_path / "repo"
    for directory in ("po", "vendor/lib"):
        (repo / directory).mkdir(parents=True)
    (repo / "crowdin.yml").write_text("files: [{source: /po/app.pot, "
                                      "translation: '/po/%two_letters_code%.po'}]\n")
    (repo / "vendor/lib/crowdin.yml").write_text("files: []\n")
    (repo / "po/app.pot").write_text("pot")
    (repo / "po/af.po").write_text("af")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m crowdin"], cwd=repo, check=True)
    return repo.as_uri()


def test_repository(crowdin_repository, tmp_path):
    paths, texts = crowdin.repository(crowdin_repository)
    assert list(texts) == ["crowdin.yml"]
    assert "po/af.po" in paths
    assert crowdin.repository((tmp_path / "nothing").as_uri()) is None
