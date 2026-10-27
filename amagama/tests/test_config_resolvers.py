#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import json
import subprocess

from amagama.updater import inlang, l10ntoml, lingui
from amagama.updater.generate import Names, check_block

URL = "https://git.example/org/app"


def generated(module, paths, texts, prefix="t"):
    """module.blocks() of one repository with paths and its configurations' texts."""
    found, skipped = module.blocks([URL], prefix, names=Names(), pause=0,
                                   read=lambda url: (paths, texts))
    for block in found:
        check_block(block)
    return {block["name"]: block for block in found}, skipped


L10N = """
basepath = "."
[env]
    l = "{l10n_base}/locales/{locale}/"
[[paths]]
    reference = "locales/en/**"
    l10n = "{l}**"
[[paths]]
    reference = "app/res/values/strings.xml"
    l10n = "app/res/values-{android_locale}/strings.xml"
[[paths]]
    reference = "other/en/*.ftl"
    l10n = "{l10n_base}/../l10n/{locale}/*.ftl"
[[paths]]
    reference = "x.ini"
    l10n = "x-{locale}.ini"
[[paths]]
    l10n = "nothing"
"""


def test_l10n_toml():
    paths = ["l10n.toml", "locales/en/main.ftl", "locales/en/sub/a.ftl", "locales/en/b.properties",
             "locales/de/main.ftl", "locales/de/b.properties", "app/res/values/strings.xml",
             "app/res/values-de/strings.xml", "x.ini", "x-de.ini"]
    found, skipped = generated(l10ntoml, paths, {"l10n.toml": L10N})
    assert {name: (b["files"], b.get("format"), b.get("template")) for name, b in found.items()} \
        == {"t-app-locales": (["locales/{lang}/**/*.ftl"], "fluent", "locales/en/**/*.ftl"),
            "t-app-locales-2": (["locales/{lang}/**/*.properties"], "properties",
                                "locales/en/**/*.properties"),
            "t-app-strings": (["app/res/values-{lang}/strings.xml"], "android",
                              "app/res/values/strings.xml")}
    assert skipped == {("paths", "path outside the repository"): 1, ("paths", "type .ini"): 1,
                       ("paths", "path without reference or l10n"): 1}


def test_l10n_toml_basepath_and_errors():
    paths = ["sub/l10n.toml", "sub/en-US/a.ftl", "sub/fr/a.ftl"]
    text = 'basepath = "."\n[[paths]]\nreference = "en-US/*.ftl"\nl10n = "{locale}/*.ftl"\n'
    found, skipped = generated(l10ntoml, paths, {"sub/l10n.toml": text, "l10n.toml": "[[x"})
    assert [b["files"] for b in found.values()] == [["sub/{lang}/*.ftl"]]
    assert skipped == {("paths", "unreadable l10n.toml"): 1}
    found, skipped = generated(l10ntoml, paths, {})
    assert skipped == {("repositories", "no l10n.toml"): 1}
    text = '[[paths]]\nreference = "en.ftl"\nl10n = "translated.ftl"\n'
    found, skipped = generated(l10ntoml, paths, {"l10n.toml": text, "x/l10n.toml": None})
    assert skipped == {("paths", "l10n without {locale}"): 1,
                       ("paths", "unreadable l10n.toml"): 1}


LINGUI = """
import { defineConfig } from '@lingui/conf';
import { formatter } from '@lingui/format-po';
export default defineConfig({
  sourceLocale: 'en',
  locales: ['en', 'de'],
  catalogs: [{ path: '<rootDir>/src/locales/{locale}/messages', include: ['src'] }],
  format: formatter({ lineNumbers: false }),
});
"""


def test_lingui():
    paths = ["web/lingui.config.ts", "web/src/locales/en/messages.po",
             "web/src/locales/de/messages.po"]
    found, skipped = generated(lingui, paths, {"web/lingui.config.ts": LINGUI})
    assert found == {"t-app": {"name": "t-app", "title": "app", "source": "git", "url": URL,
                               "files": ["web/src/locales/{lang}/messages.po"],
                               "exclude": ["web/src/locales/en/messages.po"],
                               "sparse": ["web/src/locales/*/messages.po"]}}
    assert skipped == {}


def test_lingui_json_and_package_json():
    paths = ["package.json", "locale/en.json", "locale/de.json"]
    config = {"name": "app", "lingui": {"catalogs": [{"path": "locale/{locale}"}],
                                        "format": "minimal", "sourceLocale": "en"}}
    found, skipped = generated(lingui, paths, {"package.json": json.dumps(config)})
    [block] = found.values()
    assert (block["files"], block["format"], block["template"]) == (
        ["locale/{lang}.json"], "json", "locale/en.json")
    config = {"catalogs": [{"path": "locale/{locale}"}], "format": "minimal"}
    found, _ = generated(lingui, paths, {".linguirc": json.dumps(config)})
    assert len(found) == 1


def test_lingui_left_out():
    paths = ["lingui.config.js"]
    cases = [
        ("export default { catalogs: [{ path: catalogPath }] }",
         "no catalog path with {locale} the patterns can read"),
        ("export default { format: 'csv', catalogs: [{ path: 'l/{locale}' }] }", "format csv"),
        ("export default { sourceLocale: 'de', catalogs: [{ path: 'l/{locale}' }] }",
         "source not English"),
        ("export default { catalogs: [{ path: '../{locale}' }] }",
         "catalog outside the repository"),
    ]
    for text, reason in cases:
        assert generated(lingui, paths, {"lingui.config.js": text})[1] == {("catalogs", reason): 1}
    assert generated(lingui, paths, {"package.json": '{"name": "x"}'})[1] == {
        ("repositories", "no Lingui configuration"): 1}
    assert generated(lingui, paths, {"package.json": "{"})[1] == {
        ("repositories", "no Lingui configuration"): 1}
    assert generated(lingui, paths, {"lingui.config.js": None})[1] == {
        ("catalogs", "unreadable configuration"): 1}
    assert lingui.settings(".linguirc", "[]") is None


def test_inlang():
    settings = {
        "sourceLanguageTag": "en",
        "languageTags": ["en", "de"],
        "plugin.inlang.i18next": {"pathPattern": {"common": "./locales/{languageTag}/common.json",
                                                  "extra": "./locales/{languageTag}/x.yaml"}},
        "plugin.inlang.messageFormat": {"pathPattern": "./messages/{languageTag}.json"},
        "plugin.inlang.other": {"pathPattern": "./nothing.json"},
    }
    paths = ["web/project.inlang/settings.json", "web/locales/en/common.json",
             "web/locales/de/common.json", "web/messages/en.json", "web/messages/de.json"]
    found, skipped = generated(inlang, paths,
                               {"web/project.inlang/settings.json": json.dumps(settings)})
    assert {name: (b["files"], b["template"]) for name, b in found.items()} == {
        "t-app-locales": (["web/locales/{lang}/common.json"], "web/locales/en/common.json"),
        "t-app-messages": (["web/messages/{lang}.json"], "web/messages/en.json")}
    assert skipped == {("patterns", "pathPattern without a language or not JSON"): 2}


def test_inlang_left_out():
    path = "project.inlang/settings.json"
    cases = [
        ("{", "unreadable settings.json"),
        (json.dumps({"baseLocale": "de"}), "source not English"),
        (json.dumps({"baseLocale": "en"}), "no plugin with a pathPattern"),
    ]
    for text, reason in cases:
        assert generated(inlang, [path], {path: text})[1] == {("patterns", reason): 1}
    assert generated(inlang, [path], {path: None})[1] == {
        ("patterns", "unreadable settings.json"): 1}
    assert generated(inlang, [], {})[1] == {
        ("repositories", "no project.inlang/settings.json"): 1}
    text = json.dumps({"baseLocale": "en", "plugin.x": {"pathPattern": "m/{locale}.json"}})
    assert generated(inlang, [path], {path: text})[1] == {
        ("patterns", "pathPattern matches nothing"): 1}


def test_read_from_repository(tmp_path):
    repo = tmp_path / "repo"
    files = {
        "l10n.toml": '[[paths]]\nreference = "ftl/en/a.ftl"\nl10n = "ftl/{locale}/a.ftl"\n',
        "ftl/en/a.ftl": "a = A\n", "ftl/de/a.ftl": "a = Ä\n",
        "lingui.config.js": "export default { format: '@lingui/format-json', "
                            "catalogs: [{ path: 'js/{locale}' }] }",
        "js/en.json": '{"a": "A"}', "js/de.json": '{"a": "Ä"}',
        "project.inlang/settings.json": json.dumps(
            {"baseLocale": "en", "plugin.inlang.messageFormat": {"pathPattern": "m/{locale}.json"}}),
        "m/en.json": '{"a": "A"}', "m/de.json": '{"a": "Ä"}',
    }
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m c"], cwd=repo, check=True)
    gone = (tmp_path / "gone").as_uri()
    for module, mask in ((l10ntoml, "ftl/{lang}/a.ftl"), (lingui, "js/{lang}.json"),
                         (inlang, "m/{lang}.json")):
        found, skipped = module.blocks([repo.as_uri(), gone], "t", names=Names(), pause=0)
        assert [block["files"] for block in found] == [[mask]]
        assert skipped == {("repositories", "not found"): 1}
