#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import json
import subprocess

from amagama.updater import wlc
from amagama.updater.fetchers import FetchError
from amagama.updater.generate import Names, check_block

URL = "https://github.com/org/app"
HOSTED = "[weblate]\nurl = https://hosted.weblate.org/api/\ntranslation = app/ui\n"


def component(slug, filemask, repo=URL + ".git", branch="main", file_format="po", template="",
              **extra):
    return dict({"slug": slug, "url": "https://hosted.weblate.org/api/components/app/%s/" % slug,
                 "project": {"slug": "app", "name": "App"}, "repo": repo, "branch": branch,
                 "filemask": filemask, "template": template, "file_format": file_format,
                 "license": "MIT", "source_language": {"code": "en"}, "is_glossary": False},
                **extra)


def generated(texts, project_components=(), paths=("po/de.po",), trees=None, counted=None):
    looked_up = []

    def lookup(instance, project):
        looked_up.append((instance, project))
        if project_components is None:
            raise FetchError("404")
        return list(project_components)
    found, skipped = wlc.blocks(
        [URL], "wlc", names=Names(), pause=0, read=lambda url: (list(paths), texts),
        lookup=lookup, count=lambda instance: counted,
        trees=lambda url, ref: list(paths) if trees is None else trees.get(ref))
    for block in found:
        check_block(block)
    return {block["name"]: block for block in found}, skipped, looked_up


def test_settings():
    assert wlc.settings(HOSTED) == ("https://hosted.weblate.org", "app", "ui")
    assert wlc.settings("[weblate]\nurl = https://w.example/api\ntranslation = app\n") == (
        "https://w.example", "app", None)
    assert wlc.settings("[weblate]\nurl = https://w.example/\n") is None
    assert wlc.settings("not ini") is None


def test_blocks():
    components = [
        component("ui", "po/*.po"),
        component("android", "res/values-*/strings.xml", file_format="aresource",
                  template="res/values/strings.xml"),
        component("glossary", "*.tbx", file_format="tbx", is_glossary=True),
        component("other", "po/*.po", repo="https://github.com/org/other.git"),
        component("linked", "help/*.po", repo="weblate://app/ui",
                  linked_component="https://hosted.weblate.org/api/components/app/ui/"),
    ]
    paths = ["po/de.po", "res/values/strings.xml", "res/values-de/strings.xml", "help/de.po"]
    found, skipped, looked_up = generated({".weblate": HOSTED}, components, paths)
    assert looked_up == [("https://hosted.weblate.org", "app")]
    assert {name: (b["files"], b.get("format"), b.get("ref")) for name, b in found.items()} == {
        "wlc-app": (["help/{lang}.po", "po/{lang}.po"], None, "main"),
        "wlc-app-android": (["res/values-{lang}/strings.xml"], "android", "main")}
    assert skipped == {("components", "glossary (terminology)"): 1}


def test_renamed_repository_and_branches():
    components = [
        component("ui", "po/*.po", repo="https://github.com/org/old-name.git", branch="main"),
        component("stable", "po/*.po", repo="https://github.com/org/old-name.git",
                  branch="2.1.x"),
        component("gone", "po/*.po", repo="https://github.com/org/old-name.git", branch="1.0"),
    ]
    found, skipped, _ = generated({".weblate": HOSTED}, components,
                                  trees={"main": ["po/de.po"], "2.1.x": ["po/de.po"]})
    assert {name: (b["ref"], b.get("version")) for name, b in found.items()} == {
        "wlc-app-2.1.x": ("2.1.x", "2.1.x"), "wlc-app-main": ("main", None)}
    assert skipped == {("components", "branch 1.0 not found"): 1}


def test_left_out():
    found, skipped, looked_up = generated(
        {".weblate": "[weblate]\nurl = https://w.example/api/\ntranslation = app/ui\n"},
        counted=120)
    assert (found, looked_up) == ({}, [])
    assert skipped == {("instances",
                        "https://w.example (120 components): a candidate for weblate_sources"): 1}
    found, skipped, _ = generated(
        {".weblate": "[weblate]\nurl = https://w.example/api/\ntranslation = app/ui\n"})
    assert list(skipped) == [("instances",
                              "https://w.example (? components): a candidate for weblate_sources")]
    text = "[weblate]\nurl = https://pagure.io/api/\ntranslation = app/ui\n"
    assert generated({".weblate": text})[1] == {("components", "host pagure.io archived"): 1}
    assert generated({".weblate": None})[1] == {("components", "unreadable .weblate"): 1}
    assert generated({".weblate": HOSTED}, None)[1] == {
        ("components", "project app not found on hosted.weblate.org"): 1}
    assert generated({})[1] == {("repositories", "no .weblate"): 1}
    found, skipped, _ = generated({".weblate": HOSTED}, [component("ui", "gone/*.po")])
    assert skipped == {("components", "filemask matches nothing"): 1}


def test_component_count():
    assert wlc.component_count("https://w.example",
                               get=lambda url: json.dumps({"count": 7, "results": []})) == 7

    def failing(url):
        raise FetchError("404")
    assert wlc.component_count("https://w.example", get=failing) is None
    assert wlc.component_count("https://w.example", get=lambda url: "<html>") is None


def test_read_from_repository(tmp_path):
    repo = tmp_path / "repo"
    (repo / "po").mkdir(parents=True)
    (repo / ".weblate").write_text(HOSTED)
    (repo / "po/de.po").write_text("")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m c"], cwd=repo, check=True)
    found, skipped = wlc.blocks(
        [repo.as_uri(), (tmp_path / "gone").as_uri()], "wlc", names=Names(), pause=0,
        lookup=lambda instance, project: [component("ui", "po/*.po", repo=repo.as_uri())])
    assert [(block["files"], block["ref"]) for block in found] == [(["po/{lang}.po"], "main")]
    assert skipped == {("repositories", "not found"): 1}
