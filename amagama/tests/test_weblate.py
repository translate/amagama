import functools
import http.server
import json
import threading
import types

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_updater import QuietHandler, block
from amagama.updater import weblate
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
        "licence": "GPL-2.0-or-later"}}
    assert skipped == {"glossary (terminology)": 1}


def test_monolingual_block_per_component():
    found, skipped = generated([
        component("app", "strings", "https://git.example/app", "res/values-*/strings.xml",
                  file_format="aresource", template="res/values/strings.xml"),
        component("app", "other", "https://git.example/app", "res/values-*/other.xml",
                  file_format="aresource", template="res/values/other.xml"),
        component("app", "nolang", "https://git.example/app", "*.json", file_format="json"),
    ])
    assert sorted(found) == ["test-app", "test-app-2"]
    assert found["test-app"]["format"] == "android"
    assert found["test-app"]["template"] in ("res/values/strings.xml", "res/values/other.xml")
    assert skipped == {"monolingual without a template": 1}


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
        "segment": True, "licence": "CC-BY-SA-4.0"}}


def test_names_taken():
    found, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")],
                               taken={"test-app"})
    assert list(found) == ["test-app-2"]


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
                                         "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert "1 blocks." in result.output
    assert "Skipped 1 components: repository imported by another block." in result.output
    assert [(b.name, b.min_age_days) for b in load_sources(str(output))] == [("test-app", 60)]


def test_command_stdout(amagama_config, instance, tmp_path):
    # No sources file to check against; only projects matching --project.
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test",
                                         "--project", "t*"))
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
