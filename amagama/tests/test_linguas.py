#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import subprocess

import pytest

from amagama.updater import linguas, weblate
from amagama.updater.sources import load_sources

TREE = [("po/LINGUAS", "l1"), ("po/app.pot", "pot1"), ("po/af.po", "af1"), ("po/de.po", "de1"),
        ("po/POTFILES.in", "pf1"), ("po/meson.build", "mb1"), ("help/af/af.po", "h1")]


def test_candidates():
    assert linguas.candidates(TREE) == {"po": (["po/af.po", "po/de.po"], ("pot1",))}


def test_candidates_fingerprint_without_pot():
    tree = [("po/LINGUAS", "l1"), ("po/de.po", "de1"), ("po/af.po", "af1")]
    assert linguas.candidates(tree) == {"po": (["po/af.po", "po/de.po"], ("af1", "de1"))}


def test_candidates_left_out():
    tree = [("LINGUAS", "l0"), ("de.po", "d0"),  # not in a po directory
            ("lib/po/LINGUAS", "l1"), ("lib/po/app.pot", "p1"),  # no translations
            ("vendor/x/po/LINGUAS", "l2"), ("vendor/x/po/de.po", "d2"),
            ("subprojects/y/po/LINGUAS", "l3"), ("subprojects/y/po/de.po", "d3"),
            ("tools/po/LINGUAS", "l4"), ("tools/po/de.po", "d4"), ("tools/po/notes.po", "n4")]
    # A .po whose name isn't a language isn't a translation.
    assert linguas.candidates(tree) == {"tools/po": (["tools/po/de.po"], ("d4",))}


def generated(repositories, **kwargs):
    trees = {"https://github.com/org/app": {"po": (["po/af.po"], ("pot1",), 500)}}
    trees.update(kwargs.pop("trees", {}))
    return linguas.blocks(repositories, "linguas", pause=0,
                          read=lambda url, seen: trees.get(url), **kwargs)


def test_blocks():
    found, skipped = generated(["https://github.com/org/app"],
                               licences={"github.com/org/app": "GPL-3.0-only"})
    assert found == [{"name": "linguas-app", "title": "app", "source": "git",
                      "url": "https://github.com/org/app", "files": ["po/{lang}.po"],
                      "sparse": ["po/*.po"], "licence": "GPL-3.0-only"}]
    assert skipped == {}


def test_blocks_round_trip(tmp_path):
    found, _ = generated(["https://github.com/org/app"], licences={"github.com/org/app": "MIT"})
    path = tmp_path / "t.toml"
    path.write_text(weblate.toml(found, "Generated", {"source": "git"}), encoding="utf-8")
    [loaded] = load_sources(str(path))
    assert (loaded.name, loaded.licence) == ("linguas-app", "MIT")


def test_blocks_skipped():
    found, skipped = generated(
        ["https://github.com/org/app", "https://github.com/fork/app", "https://git.example/gone",
         "https://git.example/none", "https://git.example/tiny", "https://git.example/old"],
        covered=["https://git.example/old.git"],
        trees={"https://github.com/fork/app": {"po": (["po/af.po"], ("pot1",), None)},
               "https://git.example/none": {},
               "https://git.example/tiny": {"po": (["po/af.po"], ("pot2",), 99)}})
    assert [b["name"] for b in found] == ["linguas-app"]
    assert skipped == {("repositories", "imported by another block"): 1,
                       ("repositories", "not found"): 1,
                       ("repositories", "no po/LINGUAS with translations beside it"): 1,
                       ("directories", "copy of another repository's translations"): 1,
                       ("directories", "fewer than 100 translated units"): 1}


def test_blocks_seen_passed_to_read():
    seen = []

    def read(url, fingerprints):
        seen.append(fingerprints)
        return {"po": (["po/af.po"], (url,), 500)}

    linguas.blocks(["a://x/one", "a://x/two"], "linguas", pause=0, read=read)
    assert seen == [frozenset(), frozenset({("a://x/one",)})]


def test_blocks_mirrors():
    read = []
    linguas.blocks(["https://git.example/o/app", "https://git.example/o/tool"], "linguas", pause=0,
                   read=lambda url, seen: read.append(url),
                   mirrors={"https://git.example/o/app": "https://github.com/o/app"})
    assert read == ["https://github.com/o/app", "https://git.example/o/tool"]


def test_blocks_names():
    trees = {"https://git.example/other/app": {"po": (["po/af.po"], ("pot2",), 500)},
             "https://git.example/org/suite": {"po": (["po/af.po"], ("pot3",), 500),
                                               "plugin/po": (["plugin/po/af.po"], ("pot4",), 500)}}
    found, _ = generated(["https://github.com/org/app", "https://git.example/other/app",
                          "https://git.example/org/suite"], taken={"linguas-suite"}, trees=trees)
    # A repository whose name is taken gets its owner's too; a second
    # directory is named by its path.
    assert [(b["name"], b["files"]) for b in found] == [
        ("linguas-app", ["po/{lang}.po"]), ("linguas-other-app", ["po/{lang}.po"]),
        ("linguas-org-suite-plugin", ["plugin/po/{lang}.po"]),
        ("linguas-org-suite", ["po/{lang}.po"])]


def test_blocks_names_numbered():
    found, _ = generated(["https://github.com/org/app"], taken={"linguas-app", "linguas-org-app"})
    assert [b["name"] for b in found] == ["linguas-org-app-2"]


def test_blocks_not_valid():
    found, skipped = generated(["https://github.com/org/app"], licences={"github.com/org/app": "GPL-3.0"})
    assert found == []
    assert list(skipped) == [("directories", "not a valid block: "
                              "Unknown license key(s): GPL-3.0")]


def test_blocks_names_kept():
    previous = {weblate.identity("https://github.com/org/app", None, None, False, None): "linguas-mine"}
    found, _ = generated(["https://github.com/org/app"], previous=previous)
    assert [b["name"] for b in found] == ["linguas-mine"]
    # Not when another block has it now.
    found, _ = generated(["https://github.com/org/app"], previous=previous, taken={"linguas-mine"})
    assert [b["name"] for b in found] == ["linguas-app"]


PO = """\
msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open"
msgstr "Oop"

#, fuzzy
msgid "Close"
msgstr "Maak toe"

msgid "Save"
msgstr ""
"""


@pytest.fixture
def repository(tmp_path):
    """A local gettext repository, with a copy of it vendored."""
    repo = tmp_path / "repo"
    for directory in ("po", "vendor/lib/po", "src"):
        (repo / directory).mkdir(parents=True)
    (repo / "po/LINGUAS").write_text("af\nde\n")
    (repo / "po/app.pot").write_text("pot")
    (repo / "po/af.po").write_text(PO)
    (repo / "po/de.po").write_text(PO.replace("Oop", "Öffnen"))
    (repo / "po/nl.po").write_text('msgid "Open\n')  # unreadable: left out
    (repo / "vendor/lib/po/LINGUAS").write_text("af\n")
    (repo / "vendor/lib/po/af.po").write_text(PO)
    (repo / "src/main.c").write_text("int main;")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m po"], cwd=repo, check=True)
    return repo


def test_repository(repository, tmp_path):
    pot = subprocess.run(["git", "rev-parse", "HEAD:po/app.pot"], cwd=repository, check=True,
                         capture_output=True, text=True).stdout.strip()
    # Each file's one translated unit counts; fuzzy and untranslated don't.
    files = ["po/af.po", "po/de.po", "po/nl.po"]
    assert linguas.repository(repository.as_uri()) == {"po": (files, (pot,), 2)}
    assert linguas.repository(repository.as_uri(), {(pot,)}) == {"po": (files, (pot,), None)}
    assert linguas.repository((tmp_path / "nothing").as_uri()) is None


@pytest.mark.parametrize("failing, expected", [("ls-tree", None), ("sparse-checkout", {})])
def test_repository_git_fails(repository, monkeypatch, failing, expected):
    # The clone works; reading its tree, or checking out its files, doesn't.
    run_tool = linguas.run_tool

    def failing_tool(tool, *args, **kwargs):
        if failing in args:
            raise linguas.FetchError("git %s failed" % failing)
        return run_tool(tool, *args, **kwargs)

    monkeypatch.setattr(linguas, "run_tool", failing_tool)
    assert linguas.repository(repository.as_uri()) == expected
