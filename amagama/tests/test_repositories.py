#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


from amagama.updater import crowdin, repositories
from amagama.updater.generate import Names, identity

PO = {"files": ["po/{lang}.po"], "sparse": ["po/*.po"]}
ANDROID = {"files": ["res/values-{lang}/s.xml"], "format": "android", "template": "res/values/s.xml"}


def generated(resolved, urls=None, **kwargs):
    """repositories.blocks() with resolved as {url: resolve's result}."""
    read = []

    def resolve(url):
        read.append(url)
        return resolved.get(url)
    names = Names(kwargs.pop("taken", ()), kwargs.pop("previous", None))
    found, skipped = repositories.blocks(urls or list(resolved), "t", resolve, "things",
                                         names=names, pause=0, **kwargs)
    return found, skipped, read


def test_blocks():
    found, skipped, _ = generated({"https://git.example/org/app": (
        [dict(PO, suffix=None), dict(ANDROID, suffix="android")], {("things", "unread"): 2})})
    assert found == [
        {"name": "t-app", "title": "app", "source": "git", "url": "https://git.example/org/app",
         "files": ["po/{lang}.po"], "sparse": ["po/*.po"]},
        {"name": "t-app-android", "title": "app", "source": "git",
         "url": "https://git.example/org/app", "files": ["res/values-{lang}/s.xml"],
         "format": "android", "template": "res/values/s.xml"}]
    assert skipped == {("things", "unread"): 2}


def test_blocks_skipped():
    found, skipped, read = generated(
        {"https://git.example/b": None,
         "https://git.example/c": ([dict(PO, files=["../{lang}.po"], suffix=None)], {})},
        urls=["https://git.example/a.git", "https://git.example/b", "https://git.example/c"],
        covered=["https://git.example/a"])
    assert found == []
    assert read == ["https://git.example/b", "https://git.example/c"]
    assert skipped == {("repositories", "imported by another block"): 1,
                       ("repositories", "not found"): 1,
                       ("things", "not a valid block: bad filemask '../{lang}.po'"): 1}


def test_blocks_licences_and_mirrors():
    url = "https://gitlab.example/GNOME/app"
    found, _, read = generated({"https://github.com/GNOME/app": ([dict(PO, suffix=None)], {})},
                               urls=[url], licences={"gitlab.example/GNOME/app": "GPL-2.0-or-later"},
                               mirrors={url: "https://github.com/GNOME/app"})
    # The mirror is read; the block is the repository's.
    assert read == ["https://github.com/GNOME/app"]
    assert [(b["url"], b["licence"]) for b in found] == [(url, "GPL-2.0-or-later")]


def test_blocks_names():
    resolved = {"https://git.example/org/app": ([dict(PO, suffix=None)], {}),
                "https://git.example/other/app": ([dict(PO, suffix=None), dict(PO, suffix=None)], {})}
    found, _, _ = generated(resolved, taken={"t-other-app"})
    # A taken name gets the owner's too, then a number.
    assert [b["name"] for b in found] == ["t-app", "t-other-app-2", "t-other-app-3"]


def test_blocks_names_kept():
    url = "https://git.example/org/app"
    previous = {identity(url, None, None, False, None): "t-mine",
                identity(url, None, "res/values/s.xml", False, None): "t-taken"}
    found, _, _ = generated({url: ([dict(PO, suffix=None), dict(ANDROID, suffix="android")], {})},
                            previous=previous, taken={"t-taken"})
    assert [b["name"] for b in found] == ["t-mine", "t-app-android"]


def test_blocks_names_kept_only_unique():
    url = "https://git.example/org/app"
    previous = {identity(url, None, None, False, None): "t-mine"}
    found, _, _ = generated({url: ([dict(PO, suffix=None), dict(PO, files=["l10n/{lang}.po"],
                                                                 suffix="l10n")], {})},
                            previous=previous)
    # Two blocks read the same as far as identity() tells: neither keeps it.
    assert [b["name"] for b in found] == ["t-app", "t-app-l10n"]


def test_slug():
    assert repositories.slug("--My App (beta)/x--") == "My-App-beta-x"


def test_configs_left_out():
    assert repositories.configs(["crowdin.yml", "a/crowdin.yml", "vendor/x/crowdin.yml",
                                 "tests/fixtures/crowdin.yml", "src/__tests__/crowdin.yml"],
                                crowdin.CONFIG) == ["crowdin.yml", "a/crowdin.yml"]


def test_join():
    assert repositories.join("web", "./po/{lang}.po") == "web/po/{lang}.po"
    assert repositories.join("", "/po//x.po") == "po/x.po"
    assert repositories.join("a", "..") == ""
    assert repositories.join("a", "../../x") is None


def test_format_of():
    assert repositories.format_of("res/values-de/strings.xml") == "android"
    assert repositories.format_of("x/strings.xml") is None
    assert repositories.format_of("a/b.FTL") == "fluent"
