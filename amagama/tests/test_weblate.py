#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import http.server
import json
import subprocess
import threading
import types

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_updater import QuietHandler, block
from amagama.updater import weblate
from amagama.updater.formats import check_format
from amagama.updater.fetchers import FetchError
from amagama.updater.sources import load_sources


def component(project, slug, repo, filemask, branch="main", file_format="po", template="",
              license="GPL-2.0-or-later", source="en", **extra):
    return dict({"project": {"slug": project, "name": project.title()}, "slug": slug,
                 "url": "https://weblate.example/api/components/%s/%s/" % (project, slug),
                 "repo": repo, "branch": branch, "filemask": filemask, "template": template,
                 "file_format": file_format, "license": license,
                 "source_language": {"code": source}, "is_glossary": False}, **extra)


def generated(components, **kwargs):
    found, skipped = weblate.blocks(components, "test", **kwargs)
    return {b["name"]: b for b in found}, skipped


def test_components_grouped_by_repository():
    found, skipped = generated([
        component("app", "ui", "https://git.example/app.git", "po/*.po"),
        component("app", "man", "https://git.example/app.git", "man/po/*.po"),
        component("app", "glossary", "https://git.example/app.git", "*.tbx", file_format="tbx",
                  is_glossary=True),
    ])
    assert found == {"test-app": {
        "name": "test-app", "title": "App", "source": "git", "url": "https://git.example/app.git",
        "ref": "main", "files": ["man/po/{lang}.po", "po/{lang}.po"],
        "sparse": ["man/po/*.po", "po/*.po"], "licence": "GPL-2.0-or-later"}}
    assert skipped == {"glossary (terminology)": 1}


def test_monolingual_block_per_component():
    found, skipped = generated([
        component("app", "strings", "https://git.example/app", "res/values-*/strings.xml",
                  file_format="aresource", template="res/values/strings.xml"),
        component("app", "other", "https://git.example/app", "res/values-*/other.xml",
                  file_format="aresource", template="res/values/other.xml"),
        component("app", "nolang", "https://git.example/app", "*.json", file_format="json"),
    ])
    # Told apart by component, so a name doesn't depend on the project's other blocks.
    assert sorted(found) == ["test-app-other", "test-app-strings"]
    assert found["test-app-strings"]["format"] == "android"
    assert found["test-app-strings"]["template"] == "res/values/strings.xml"
    assert found["test-app-strings"]["sparse"] == ["res/values-*/strings.xml",
                                                   "res/values/strings.xml"]
    assert skipped == {"monolingual without a template": 1}


def test_bilingual_template_ignored():
    # Weblate's template for a bilingual format is its source file.
    found, skipped = generated([component("qt", "ui", "https://git.example/qt", "ts/app_*.ts",
                                          file_format="ts", template="ts/app_en.ts")])
    assert "template" not in found["test-qt"]
    assert found["test-qt"]["format"] == "ts"


def test_i18next():
    found, skipped = generated([component("web", "ui", "https://git.example/web",
                                          "locales/*/translation.json", file_format="i18nextv4",
                                          template="locales/en/translation.json")])
    assert found["test-web"]["format"] == "json"


def test_branches_are_versions():
    found, skipped = generated([
        component("app", "ui", "https://git.example/app", "po/*.po", branch="1.2.x"),
        component("app", "ui-new", "https://git.example/app", "po/*.po", branch="1.5.x"),
    ])
    assert {name: (b["version"], b["ref"]) for name, b in found.items()} == {
        "test-app-1.2.x": ("1.2.x", "1.2.x"), "test-app-1.5.x": ("1.5.x", "1.5.x")}


def test_branches_not_releases():
    found, skipped = generated([
        component("abrt", "abrt", "https://git.example/l10n", "*.po", branch="abrt"),
        component("abrt", "libreport", "https://git.example/l10n", "*.po", branch="libreport"),
    ])
    assert {name: (b.get("version"), b["ref"]) for name, b in found.items()} == {
        "test-abrt-abrt": (None, "abrt"), "test-abrt-libreport": (None, "libreport")}


def test_repositories():
    linked = component("app", "parent", "https://git.example/app", "po/*.po")
    found, skipped = generated([
        linked,
        component("app", "child", "weblate://app/parent", "help/*/*.po",
                  linked_component=linked["url"]),
        component("ssh", "ui", "git@github.com:org/ssh.git", "po/*.po"),
        component("local", "ui", "local:", "po/*.po", git_export="https://weblate.example/git/local/ui/"),
        component("gone", "ui", "", "po/*.po"),
    ])
    # The first * of help/*/*.po is the language.
    assert found["test-app"]["files"] == ["help/{lang}/*.po", "po/{lang}.po"]
    assert found["test-ssh"]["url"] == "https://github.com/org/ssh.git"
    assert found["test-local"]["url"] == "https://weblate.example/git/local/ui/"
    assert "ref" not in found["test-local"]
    assert skipped == {"no repository": 1}


def test_skipped():
    found, skipped = generated([
        component("fr", "ui", "https://git.example/fr", "po/*.po", source="fr"),
        component("yaml", "ui", "https://git.example/yaml", "*.yml", file_format="yaml"),
        component("noderiv", "docs", "https://git.example/noderiv", "po/*.po", license="CC-BY-ND-3.0"),
        component("bad", "ui", "https://git.example/bad", "po/*.po", license="proprietary"),
        component("known", "ui", "https://git.example/known.git/", "po/*.po"),
    ], covered=["https://git.example/known"])
    assert found == {}
    assert sorted(skipped) == ["format yaml", "no-derivatives licence",
                               "not a valid block: Unknown license key(s): proprietary",
                               "repository imported by another block", "source not English"]


def test_docs():
    found, skipped = generated(
        [component("test-docs-notes", "page%d" % i, "https://git.example/notes", "po/*/main/p%d.po" % i,
                   license="CC-BY-SA-4.0") for i in range(12)],
        docs=["test-docs-*"])
    # Many single-file components: the repository's PO under the language.
    assert found == {"test-docs-notes": {
        "name": "test-docs-notes", "title": "Test-Docs-Notes", "source": "git",
        "url": "https://git.example/notes", "ref": "main", "files": ["po/{lang}/**/*.po"],
        "sparse": ["po/*/**/*.po"], "segment": True, "licence": "CC-BY-SA-4.0"}}


def test_docs_component():
    found, skipped = generated([
        component("app", "ui", "https://git.example/app", "po/*.po"),
        component("app", "docs", "https://git.example/app-docs", "docs/po/*.po"),
    ], docs=["*/docs"])
    assert {name: b.get("segment", False) for name, b in found.items()} == {
        "test-app-ui": False, "test-app-docs": True}


def test_names_taken():
    found, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")],
                               taken={"test-app"})
    assert list(found) == ["test-app-ui"]


def test_names_counted_last():
    found, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")],
                               taken={"test-app", "test-app-ui"})
    assert list(found) == ["test-app-ui-2"]


def test_identity_keeps_unknown_licence():
    assert weblate.identity("https://git.example/app.git", None, None, False, "proprietary") == (
        "git.example/app", None, None, False, "proprietary")


def test_names_kept():
    first, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")])
    assert list(first) == ["test-app"]
    previous = {weblate.identity(b["url"], b.get("ref"), b.get("template"), b.get("segment"),
                                 b.get("licence")): name
                for name, b in first.items()}
    # The project gains a component in another format: the first block keeps its name.
    found, skipped = generated([
        component("app", "ui", "https://git.example/app", "po/*.po"),
        component("app", "android", "https://git.example/app", "res/values-*/strings.xml",
                  file_format="aresource", template="res/values/strings.xml"),
    ], previous=previous)
    assert sorted(found) == ["test-app", "test-app-android"]
    assert found["test-app"]["files"] == ["po/{lang}.po"]


def test_toml_defaults(tmp_path):
    found, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")])
    text = weblate.toml(list(found.values()), "Generated", {"source": "git", "min_age_days": 60})
    assert "[defaults]\nsource = \"git\"\nmin_age_days = 60\n" in text
    assert text.count('source = "git"') == 1
    path = tmp_path / "w.toml"
    path.write_text(text)
    assert [(b.source, b.min_age_days) for b in load_sources(str(path))] == [("git", 60)]


def test_toml_round_trip(tmp_path):
    found, skipped = generated([component("app", "ui", "https://git.example/äpp", "po/*.po")])
    path = tmp_path / "w.toml"
    path.write_text(weblate.toml(list(found.values()), "Generated\nReview"), encoding="utf-8")
    assert path.read_text(encoding="utf-8").startswith("# Generated\n# Review\n")
    assert [b.name for b in load_sources(str(path))] == ["test-app"]


@pytest.fixture
def instance(tmp_path):
    """A local Weblate whose components come on two pages."""
    root = tmp_path / "weblate"
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    url = "http://127.0.0.1:%d" % server.server_address[1]
    pages = [[component("app", "ui", "https://git.example/app", "po/*.po")],
             [component("tool", "ui", "https://git.example/tool", "po/*.po")]]
    for i, results in enumerate(pages):
        page = root / "api/components" / ("page%d" % i if i else "")
        page.mkdir(parents=True)
        (page / "index.html").write_text(json.dumps({
            "next": "%s/api/components/page%d/" % (url, i + 1) if i + 1 < len(pages) else None,
            "results": results}))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield url
    server.shutdown()
    server.server_close()


def test_components(instance, monkeypatch):
    slept = []
    monkeypatch.setattr(weblate, "time", types.SimpleNamespace(sleep=slept.append))
    assert [c["project"]["slug"] for c in weblate.components(instance, pause=3)] == ["app", "tool"]
    assert slept == [3]


def test_command(amagama_config, instance, tmp_path):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("tool", "git", url="https://git.example/tool", files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources))
    output = tmp_path / "weblate-test.toml"
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test",
                                         "--output", str(output), "--no-verify"))
    assert result.exit_code == 0, result.output
    assert "1 blocks." in result.output
    assert "Skipped 1 components: repository imported by another block." in result.output
    assert [(b.name, b.min_age_days) for b in load_sources(str(output))] == [("test-app", 60)]


def test_command_regenerates_own_blocks(amagama_config, instance, tmp_path):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("test-app", "git", url="https://git.example/app", files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources))
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test",
                                         "--no-verify"))
    assert result.exit_code == 0, result.output
    assert 'name = "test-app"' in result.output


def test_command_stdout(amagama_config, instance, tmp_path):
    # No sources file to check against; only projects matching --project.
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test",
                                         "--project", "t*", "--no-verify"))
    assert result.exit_code == 0, result.output
    assert 'name = "test-tool"' in result.output
    assert "test-app" not in result.output
    assert "# Generated by: amagama-manage weblate_sources %s --prefix test --project t*" \
        % instance in result.output


def test_command_bad_instance(amagama_config, instance, tmp_path):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    result = CliRunner().invoke(manage, ("weblate_sources", instance + "/nothing", "--prefix", "t"))
    assert result.exit_code == 1
    assert "api/components" in result.output


def test_components_not_json(instance, tmp_path):
    page = tmp_path / "weblate/html/api/components"
    page.mkdir(parents=True)
    (page / "index.html").write_text("<html>Not Weblate</html>")
    with pytest.raises(FetchError, match="api/components"):
        weblate.components(instance + "/html")


def test_formats_are_readers():
    """Every format the generator writes is one amaGama reads."""
    for format in filter(None, weblate.FORMATS.values()):
        check_format(format)


@pytest.mark.parametrize("params, expected", [
    ({"properties_encoding": "utf-8"}, "properties-utf8"),
    ({"properties_encoding": "utf-16"}, "properties-utf16"),
    ({"properties_encoding": "iso-8859-1"}, "properties"),
    ({}, "properties"),
])
def test_properties_encoding(params, expected):
    # A corrected reader keeps the block's name.
    found, skipped = generated([component("app", "ui", "https://git.example/app",
                                          "src/Bundle_*.properties", file_format="properties",
                                          template="src/Bundle.properties",
                                          file_format_params=params)])
    assert found["test-app"]["format"] == expected


def test_dead_host():
    found, skipped = generated([component("libuser", "ui", "https://pagure.io/libuser/", "po/*.po")])
    assert found == {}
    assert skipped == {"host pagure.io archived": 1}


def verified(blocks, trees):
    return weblate.verify(blocks, pause=0, tree=lambda url, ref: trees.get((url, ref)))


def test_verify():
    blocks = [
        {"name": "ok", "url": "u1", "ref": "main", "files": ["po/{lang}.po", "old/{lang}.po"],
         "sparse": ["old/*.po", "po/*.po"]},
        {"name": "moved", "url": "u2", "ref": "master", "version": "master",
         "files": ["po/{lang}.po"]},
        {"name": "gone", "url": "u3", "ref": "main", "files": ["po/{lang}.po"]},
        {"name": "notemplate", "url": "u1", "ref": "main", "files": ["res/values-{lang}/s.xml"],
         "template": "res/values/s.xml"},
        {"name": "missing", "url": "u4", "files": ["po/{lang}.po"]},
    ]
    trees = {("u1", "main"): ["po/af.po", "res/values-af/s.xml"],
             ("u2", None): ["po/af.po"],
             ("u3", "main"): ["README"]}
    kept, problems = verified(blocks, trees)
    # The mask that matches nothing is dropped; the missing branch becomes the default.
    assert kept == [
        {"name": "ok", "url": "u1", "ref": "main", "files": ["po/{lang}.po"], "sparse": ["po/*.po"]},
        {"name": "moved", "url": "u2", "files": ["po/{lang}.po"], "sparse": ["po/*.po"]}]
    assert problems == {"branch gone, default branch used": 1, "filemask matches nothing": 2,
                        "repository or branch not found": 1}


def test_verify_duplicate_after_fallback():
    blocks = [{"name": "a-1.x", "url": "u", "ref": "1.x", "files": ["po/{lang}.po"]},
              {"name": "a-2.x", "url": "u", "ref": "2.x", "files": ["po/{lang}.po"]}]
    kept, problems = verified(blocks, {("u", None): ["po/af.po"]})
    assert [b["name"] for b in kept] == ["a-1.x"]
    assert problems == {"branch gone, default branch used": 1, "duplicate after branch fallback": 1}


def test_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / "po").mkdir(parents=True)
    (repo / "po/af.po").write_text("af")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m po && git branch stable"],
                   cwd=repo, check=True)
    assert weblate._tree(repo.as_uri(), "stable") == ["po/af.po"]
    assert weblate._tree(repo.as_uri(), None) == ["po/af.po"]
    assert weblate._tree(repo.as_uri(), "gone") is None
    assert weblate._tree((tmp_path / "nothing").as_uri(), None) is None


def test_command_verifies(amagama_config, instance, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setattr(weblate, "verify", lambda blocks: (blocks[:1], {"filemask matches nothing": 1}))
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test"))
    assert result.exit_code == 0, result.output
    assert "1 blocks." in result.output
    assert "Skipped 1 components: filemask matches nothing." in result.output
