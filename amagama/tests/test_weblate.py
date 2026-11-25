#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import contextlib
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
from amagama.updater import generate, weblate
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
    found, skipped = weblate.blocks(components, "test", names=generate.Names(
        kwargs.pop("taken", ()), kwargs.pop("previous", None)), **kwargs)
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


def test_xliff_with_template_is_monolingual():
    found, skipped = generated([
        # Symfony's: each <source> an id, the English in messages.en.xlf.
        component("kimai", "messages", "https://git.example/kimai", "translations/messages.*.xlf",
                  file_format="xliff", template="translations/messages.en.xlf"),
        component("other", "app", "https://git.example/other", "locale/*.xlf",
                  file_format="xliff"),
    ])
    assert found["test-kimai"]["format"] == "xliff-monolingual"
    assert found["test-kimai"]["template"] == "translations/messages.en.xlf"
    assert found["test-other"]["format"] == "xliff"
    assert "template" not in found["test-other"]
    assert skipped == {}


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
        component("toml", "ui", "https://git.example/toml", "*.toml", file_format="toml"),
        component("noderiv", "docs", "https://git.example/noderiv", "po/*.po", license="CC-BY-ND-3.0"),
        component("bad", "ui", "https://git.example/bad", "po/*.po", license="proprietary"),
        component("known", "ui", "https://git.example/known.git/", "po/*.po"),
    ], covered=["https://git.example/known"])
    assert found == {}
    assert sorted(skipped) == ["format toml", "no-derivatives licence",
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


def test_folded_mask_leaves_out_skipped():
    # Folding the masks would also read the components left out.
    app = [component("app", "c%d" % i, "https://git.example/app", "locale/*/LC_MESSAGES/c%d.po" % i)
           for i in range(11)]
    left_out = [
        component("app", "glossary", "https://git.example/app", "locale/*/LC_MESSAGES/terms.po",
                  is_glossary=True),
        component("app", "noderivs", "https://git.example/app", "locale/*/LC_MESSAGES/noderivs.po",
                  license="CC-BY-ND-4.0"),
        component("app", "de", "https://git.example/app", "locale/*/LC_MESSAGES/de.po", source="de"),
    ]
    found, skipped = generated(app + left_out)
    assert found["test-app"]["files"] == sorted("locale/{lang}/LC_MESSAGES/c%d.po" % i
                                                for i in range(11))
    assert skipped == {"glossary (terminology)": 1, "no-derivatives licence": 1,
                       "source not English": 1}
    # A wildcard extension matches the folded mask's.
    found, _ = generated(app + [component("app", "terms", "https://git.example/app",
                                          "locale/*/LC_MESSAGES/terms.*", is_glossary=True)])
    assert len(found["test-app"]["files"]) == 11
    # With nothing left out under it, the mask is folded.
    found, _ = generated(app + [component("app", "terms", "https://git.example/app",
                                          "locale/*/terms.tbx", file_format="tbx")])
    assert found["test-app"]["files"] == ["locale/{lang}/**/*.po"]


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
    assert generate.identity("https://git.example/app.git", None, None, False, "proprietary") == (
        "git.example/app", None, None, False, "proprietary")


def test_names_kept():
    first, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")])
    assert list(first) == ["test-app"]
    previous = {generate.identity(b["url"], b.get("ref"), b.get("template"), b.get("segment"),
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


@pytest.mark.parametrize("reverse", [False, True])
def test_names_kept_same_identity(tmp_path, reverse):
    # Two components of one repository in different formats share an
    # identity: each keeps its own name, whichever is listed first.
    components = [
        component("fmit", "appdata", "https://git.example/fmit", "distrib/appdata_tr/*.po"),
        component("fmit", "fmit", "https://git.example/fmit", "tr/fmit_*.ts", file_format="ts"),
    ][::-1 if reverse else 1]
    path = tmp_path / "w.toml"
    path.write_text(generate.toml([
        {"name": "test-fmit-appdata", "title": "Fmit", "source": "git",
         "url": "https://git.example/fmit", "ref": "main", "files": ["distrib/appdata_tr/{lang}.po"],
         "licence": "GPL-2.0-or-later"},
        {"name": "test-fmit-2", "title": "Fmit", "source": "git", "url": "https://git.example/fmit",
         "ref": "main", "files": ["tr/fmit_{lang}.ts"], "format": "ts", "licence": "GPL-2.0-or-later"},
    ], "Generated"), encoding="utf-8")
    for _ in range(3):
        names, _, _ = generate.context(load_sources(str(path)), "test")
        found, _ = weblate.blocks(components, "test", names=names)
        assert {b["name"]: b["files"] for b in found} == {
            "test-fmit-appdata": ["distrib/appdata_tr/{lang}.po"],
            "test-fmit-2": ["tr/fmit_{lang}.ts"]}
        path.write_text(generate.toml(found, "Generated"), encoding="utf-8")


def test_toml_defaults(tmp_path):
    found, skipped = generated([component("app", "ui", "https://git.example/app", "po/*.po")])
    text = generate.toml(list(found.values()), "Generated", {"source": "git", "min_age_days": 60})
    assert "[defaults]\nsource = \"git\"\nmin_age_days = 60\n" in text
    assert text.count('source = "git"') == 1
    path = tmp_path / "w.toml"
    path.write_text(text)
    assert [(b.source, b.min_age_days) for b in load_sources(str(path))] == [("git", 60)]


def test_toml_round_trip(tmp_path):
    found, skipped = generated([component("app", "ui", "https://git.example/äpp", "po/*.po")])
    path = tmp_path / "w.toml"
    path.write_text(generate.toml(list(found.values()), "Generated\nReview"), encoding="utf-8")
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
    monkeypatch.setattr(generate, "time", types.SimpleNamespace(sleep=slept.append, monotonic=lambda: 0))
    assert [c["project"]["slug"] for c in weblate.components(instance, pause=3)] == ["app", "tool"]
    # The second page waits; the first doesn't.
    assert slept == [3]


class OnceLimited(QuietHandler):
    """Refuses the first request with 429 Too Many Requests."""

    refused = False

    def do_GET(self):
        if not OnceLimited.refused:
            OnceLimited.refused = True
            self.send_response(429)
            self.send_header("Retry-After", "622")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        super().do_GET()


def test_components_wait_out_429(instance, tmp_path, pauses, monkeypatch):
    monkeypatch.setattr(OnceLimited, "refused", False)
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(OnceLimited, directory=str(tmp_path / "weblate")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        found = weblate.components("http://127.0.0.1:%d" % server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
    # The second page is the instance fixture's own server's.
    assert [c["project"]["slug"] for c in found] == ["app", "tool"]
    assert pauses == [623]


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


def test_command_weight(amagama_config, instance, tmp_path):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    output = tmp_path / "weblate-test.toml"
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test",
                                         "--output", str(output), "--no-verify",
                                         "--weight", "0.8"))
    assert result.exit_code == 0, result.output
    text = output.read_text()
    assert "--min-age-days 60 --weight 0.8 --tag platform\n" in text
    assert 'tags = ["platform"]\n' in text
    assert "[defaults]\nsource = \"git\"\nmin_age_days = 60\nweight = 0.8\n" in text
    assert {b.weight for b in load_sources(str(output))} == {0.8}


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
    @contextlib.contextmanager
    def clone(url, ref):
        # Files can't be read from a clone that isn't there: kept, as unknown.
        yield trees[url, ref] and ("/nonexistent", trees[url, ref]) if (url, ref) in trees else None
    return generate.check(blocks, pause=0, clone=clone)


def test_check():
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


def test_check_duplicate_after_fallback():
    blocks = [{"name": "a-1.x", "url": "u", "ref": "1.x", "files": ["po/{lang}.po"]},
              {"name": "a-2.x", "url": "u", "ref": "2.x", "files": ["po/{lang}.po"]}]
    cloned = []
    trees = {("u", None): ["po/af.po"]}

    @contextlib.contextmanager
    def clone(url, ref):
        cloned.append((url, ref))
        yield ("/nonexistent", trees[url, ref]) if (url, ref) in trees else None
    kept, problems = generate.check(blocks, pause=0, clone=clone)
    assert [b["name"] for b in kept] == ["a-1.x"]
    assert problems == {"branch gone, default branch used": 1, "duplicate after branch fallback": 1}
    # Both refs fall back to one clone of the default branch.
    assert cloned == [("u", "1.x"), ("u", None), ("u", "2.x")]



def test_check_unreadable_repository():
    """A repository that couldn't be read keeps its blocks as they were."""
    blocks = [{"name": "down", "url": "u1", "ref": "main", "files": ["po/{lang}.po"]},
              {"name": "gone", "url": "u2", "ref": "main", "files": ["po/{lang}.po"]}]

    @contextlib.contextmanager
    def clone(url, ref):
        if url == "u1":
            raise FetchError("git clone u1: Could not resolve host")
        yield None
    kept, problems = generate.check(blocks, pause=0, clone=clone)
    assert kept == [blocks[0]]
    assert problems == {"repository not read, kept as it was": 1,
                        "repository or branch not found": 1}


@pytest.mark.parametrize("error, found", [
    ("remote: Repository not found.\nfatal: repository 'u' not found", False),
    ("fatal: could not read Username for 'https://github.com': terminal prompts disabled", False),
    ("warning: Could not find remote branch 1.x to clone.", False),
    ("fatal: unable to access 'u': Could not resolve host: git.example", True),
    ("fatal: unable to access 'u': The requested URL returned error: 503", True),
    ("git clone timed out after 3600 seconds", True)])
def test_clone_failure(monkeypatch, error, found):
    """A repository or branch that isn't there is None; any other failure raises."""
    def run_tool(*args, **kwargs):
        raise FetchError(error)
    monkeypatch.setattr(generate, "run_tool", run_tool)
    if found:
        with pytest.raises(FetchError):
            with generate.clone("https://git.example/u", "1.x"):
                pass
    else:
        with generate.clone("https://git.example/u", "1.x") as cloned:
            assert cloned is None


def test_clone_empty_repository(tmp_path):
    """A repository with no commits has nothing to read: not there."""
    repo = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    with generate.clone(repo.as_uri(), None) as cloned:
        assert cloned is None


def test_clone_tree_unreadable(monkeypatch, tmp_path):
    """A clone whose tree can't be listed (a timeout) is unreadable, not gone."""
    run_tool = generate.run_tool

    def failing(*args, **kwargs):
        if "ls-tree" in args:
            raise FetchError("git ls-tree timed out after 3600 seconds")
        return run_tool(*args, **kwargs)
    monkeypatch.setattr(generate, "run_tool", failing)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org",
                    "commit", "-q", "--allow-empty", "-m", "one"], check=True)
    with pytest.raises(FetchError, match="timed out"):
        with generate.clone(repo.as_uri(), None):
            pass


def test_tree(tmp_path):
    repo = tmp_path / "repo"
    (repo / "po").mkdir(parents=True)
    (repo / "po/af.po").write_text("af")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m po && git branch stable"],
                   cwd=repo, check=True)
    assert generate.tree(repo.as_uri(), "stable") == ["po/af.po"]
    assert generate.tree(repo.as_uri(), None) == ["po/af.po"]
    assert generate.tree(repo.as_uri(), "gone") is None
    assert generate.tree((tmp_path / "nothing").as_uri(), None) is None


def test_command_checks(amagama_config, instance, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setattr(generate, "check", lambda blocks: (blocks[:1], {"filemask matches nothing": 1}))
    monkeypatch.setattr(generate, "add_mirrors", lambda blocks: blocks)
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test"))
    assert result.exit_code == 0, result.output
    assert "1 blocks." in result.output
    assert "Skipped 1 components: filemask matches nothing." in result.output


def test_command_checks_only_changed(amagama_config, instance, tmp_path, monkeypatch):
    sources = tmp_path / "sources.toml"
    sources.write_text('include = ["weblate-test.toml"]\n')
    output = tmp_path / "weblate-test.toml"
    CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test", "--no-verify",
                                "--output", str(output)))
    amagama_config(UPDATER_SOURCES=str(sources))
    first = load_sources(str(output))
    checked = []
    monkeypatch.setattr(generate, "check", lambda blocks: (checked.extend(blocks) or blocks, {}))
    monkeypatch.setattr(generate, "add_mirrors", lambda blocks: blocks)
    # As written last time: nothing is checked again.
    result = CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test",
                                         "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert checked == []
    assert [b.name for b in load_sources(str(output))] == [b.name for b in first]
    # One that reads differently is checked.
    text = output.read_text()
    output.write_text(text.replace('files = ["', 'files = ["old/{lang}.po", "', 1))
    CliRunner().invoke(manage, ("weblate_sources", instance, "--prefix", "test", "--output", str(output)))
    assert [b["name"] for b in checked] == [first[0].name]


def test_check_importable(tmp_path):
    repo = tmp_path / "repo"
    files = {
        # Strings moved elsewhere: an empty template.
        "moved/values/strings.xml": "<resources></resources>",
        "moved/values-af/strings.xml": '<resources><string name="a">A</string></resources>',
        # A new module: translation files with nothing in them yet.
        "new/Bundle.properties": "save=Save\n",
        "new/Bundle_af.properties": "\n",
        "new/Bundle_de.properties": "",
        "props/Bundle.properties": "save=Save\n",
        "props/Bundle_af.properties": "\n",
        "props/Bundle_de.properties": "save=Speichern\n",
        "untranslated/af.po": 'msgid ""\nmsgstr ""\n"Language: af\\n"\n\nmsgid "Save"\nmsgstr ""\n',
        "po/af.po": 'msgid "Save"\nmsgstr "Stoor"\n',
        "broken/values/strings.xml": '<resources><string name="a">A</string></resources>',
        "broken/values-af/strings.xml": '<resources><string name="a">A</str',
        "mixed/values-af/strings.xml": '<resources><string name="a">A</str',
        "mixed/values-de/strings.xml": '<resources><string name="a">B</string></resources>',
    }
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m l10n"], cwd=repo, check=True)
    url = repo.as_uri()
    blocks = [
        {"name": "moved", "url": url, "files": ["moved/values-{lang}/strings.xml"],
         "format": "android", "template": "moved/values/strings.xml"},
        {"name": "new", "url": url, "files": ["new/Bundle_{lang}.properties"],
         "format": "properties", "template": "new/Bundle.properties"},
        {"name": "props", "url": url, "files": ["props/Bundle_{lang}.properties"],
         "format": "properties", "template": "props/Bundle.properties"},
        {"name": "untranslated", "url": url, "files": ["untranslated/{lang}.po"]},
        {"name": "po", "url": url, "files": ["po/{lang}.po"]},
        # A file that can't be read has no translations; the others still count.
        {"name": "broken", "url": url, "files": ["broken/values-{lang}/strings.xml"],
         "format": "android", "template": "broken/values/strings.xml"},
        {"name": "mixed", "url": url, "files": ["mixed/values-{lang}/strings.xml"],
         "format": "android", "template": "broken/values/strings.xml"},
        {"name": "no template", "url": url, "files": ["po/{lang}.po"], "format": "android",
         "template": "missing/strings.xml"},
        {"name": "gone", "url": (tmp_path / "gone").as_uri(), "files": ["po/{lang}.po"]},
    ]
    kept, problems = generate.check(blocks, pause=0)
    assert [b["name"] for b in kept] == ["props", "po", "mixed"]
    # One clone reads all of a repository's blocks; a missing template is
    # a block left out before its files are read.
    assert problems == {"template has no units": 1, "no translations": 3,
                        "filemask matches nothing": 1, "repository or branch not found": 1}


def test_check_reads_a_sample(tmp_path, monkeypatch):
    # openSUSE's documentation: thousands of files hold only a header.
    repo = tmp_path / "repo"
    empty = 'msgid ""\nmsgstr "Language: af\\n"\n\nmsgid "Save"\nmsgstr ""\n'
    files = {"late/m%03d/af.po" % n: empty for n in range(200)}
    files.update({"late/m%03d/af.po" % n: 'msgid "Save"\nmsgstr "Stoor"\n' for n in range(200, 250)})
    files.update({"none/m%03d/af.po" % n: empty for n in range(150)})
    files.update({"few/m%03d/af.po" % n: empty for n in range(101)})
    # An empty template: its files aren't downloaded.
    files["moved/values/strings.xml"] = "<resources></resources>"
    files.update({"moved/values-%s/strings.xml" % lang: '<resources><string name="a">A</string>'
                  '</resources>' for lang in ("af", "de", "nl")})
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text, encoding="utf-8")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m l10n"], cwd=repo, check=True)
    checkouts = []
    checkout_only = generate.checkout_only

    def counted(directory, paths):
        checkouts.append(len(paths))
        return checkout_only(directory, paths)
    monkeypatch.setattr(generate, "checkout_only", counted)
    url = repo.as_uri()
    blocks = [{"name": "late", "url": url, "files": ["late/**/{lang}.po"]},
              {"name": "none", "url": url, "files": ["none/**/{lang}.po"]},
              {"name": "few", "url": url, "files": ["few/**/{lang}.po"]},
              {"name": "moved", "url": url, "files": ["moved/values-{lang}/strings.xml"],
               "format": "android", "template": "moved/values/strings.xml"}]
    kept, problems = generate.check(blocks, pause=0)
    assert [b["name"] for b in kept] == ["late"]
    assert problems == {"no translations in 75 of its files": 1,
                        "no translations in 51 of its files": 1, "template has no units": 1}
    # One checkout a block, of at most EMPTY_SAMPLE files; the template first.
    assert checkouts == [84, 75, 51, 1]


def test_checkout_only_fetches_once(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    for n in range(20):
        (repo / ("%d.po" % n)).write_text(str(n))
    (repo / "[a].po").write_text("a")
    log = tmp_path / "fetches"
    upload_pack = tmp_path / "upload-pack"
    upload_pack.write_text('#!/bin/sh\necho fetch >> "%s"\nexec git-upload-pack "$@"\n' % log)
    upload_pack.chmod(0o755)
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m l10n && "
                    "git config uploadpack.allowFilter true"], cwd=repo, check=True)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--filter=blob:none", "--no-checkout", repo.as_uri(),
                    str(clone)], check=True)
    subprocess.run(["git", "-C", str(clone), "config", "remote.origin.uploadpack",
                    str(upload_pack)], check=True)
    paths = ["%d.po" % n for n in range(0, 20, 2)] + ["[a].po"]
    generate.checkout_only(str(clone), paths)
    assert sorted(p.name for p in clone.iterdir() if p.name != ".git") == sorted(paths)
    assert log.read_text() == "fetch\n"


def test_project_components(monkeypatch):
    pages = []

    def http_text(url):
        pages.append(url)
        return json.dumps({"results": [component("app", "ui", "https://git.example/app", "po/*.po")],
                           "next": None})
    monkeypatch.setattr(weblate, "http_text", http_text)
    found = weblate.components("https://w.example", project="a b")
    assert [c["slug"] for c in found] == ["ui"]
    assert pages == ["https://w.example/api/projects/a%20b/components/?page_size=1000"]


def test_project_source_language():
    # Older Weblate gives the source language only with the project.
    english, german = (component("app", slug, "https://git.example/%s" % slug, "po/*.po")
                       for slug in ("en", "de"))
    for found, code in ((english, "en"), (german, "de")):
        del found["source_language"]
        found["project"]["source_language"] = {"code": code}
    assert weblate.readable(english) is None
    assert weblate.readable(german) == "source not English"


def test_linked_component_branch():
    # Older Weblate shows a linked component's repository but no branch.
    found, skipped = generated([
        component("panel", "panel", "https://git.example/panel", "panel/*.ts", branch="master",
                  file_format="ts"),
        component("panel", "clock", "https://git.example/panel", "clock/*.ts", branch="",
                  file_format="ts"),
    ])
    assert {name: (b["ref"], b["files"]) for name, b in found.items()} == {
        "test-panel": ("master", ["clock/{lang}.ts", "panel/{lang}.ts"])}


@pytest.mark.parametrize("weblate_format, format", [
    ("xwiki-java-properties", "xwiki-properties"), ("xwiki-page-properties", "xwiki-page"),
    ("yaml", "yaml"), ("ruby-yaml", "ruby-yaml"), ("strings", "strings"),
    ("strings-utf8", "strings"), ("arb", "arb"), ("po-mono", "po-mono"), ("laravel", "laravel"),
    ("cmp-resource", "compose"), ("webextension", "webextension"), ("resx", "resx")])
def test_monolingual_formats(weblate_format, format):
    found, skipped = generated([component("wiki", "ui", "https://git.example/wiki",
                                          "res/App_*.properties", file_format=weblate_format,
                                          template="res/App.properties")])
    assert found["test-wiki"]["format"] == format
    assert found["test-wiki"]["template"] == "res/App.properties"
    check_format(format)


def test_linked_component_branch_scp_repository():
    # The branch is found for a repository written scp-style, too.
    found, skipped = generated([
        component("panel", "panel", "git@git.example:panel.git", "panel/*.ts", branch="master",
                  file_format="ts"),
        component("panel", "clock", "https://git.example/panel.git", "clock/*.ts", branch="",
                  file_format="ts"),
    ])
    assert {name: (b["url"], b["ref"], b["files"]) for name, b in found.items()} == {
        "test-panel": ("https://git.example/panel.git", "master",
                       ["clock/{lang}.ts", "panel/{lang}.ts"])}
