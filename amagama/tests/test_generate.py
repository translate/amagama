#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


from amagama.tests.test_updater import block
from amagama.updater import generate
from amagama.updater.sources import load_sources


def test_names_unique():
    names = generate.Names({"a", "a-2"})
    assert names.unique("a") == "a-3"
    assert names.unique("b") == "b"
    names.take("b")
    assert "b" in names
    assert names.unique("b") == "b-2"
    names.release("b")
    assert names.unique("b") == "b"


def test_names_kept_while_free():
    names = generate.Names({"other"}, {"x": "mine", "y": "other"})
    assert names.kept("x") == "mine"
    assert names.kept("y") is None
    assert names.kept("z") is None
    names.take("mine")
    assert names.kept("x") is None


def test_matching():
    paths = ["po/af.po", "po/de.po", "res/values/s.xml"]
    assert generate.matching(["po/{lang}.po", "gone/{lang}.po"], paths) == ["po/{lang}.po"]
    assert generate.matching(["po/{lang}.po"], paths, template="res/values/s.xml") == ["po/{lang}.po"]
    assert generate.matching(["po/{lang}.po"], paths, template="res/values/gone.xml") == []
    assert not generate.matches("po/{lang", "po/af.po")


def test_context(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(
        block("t-app", "git", url="https://git.example/app", files=["po/{lang}.po"])
        + block("t-off", "git", url="https://git.example/off", files=["po/{lang}.po"],
                disabled="duplicates other")
        + block("other", "git", url="https://git.example/other.git", files=["po/{lang}.po"])
        + block("other-off", "git", url="https://git.example/gone", files=["po/{lang}.po"],
                disabled="gone"),
        encoding="utf-8")
    names, covered, disabled = generate.context(load_sources(str(path), disabled=True), "t")
    assert covered == ["https://git.example/other.git"]
    assert "other" in names and "other-off" in names and "t-app" not in names
    assert names.kept(generate.identity("https://git.example/app.git", None, None, False, None)) \
        == "t-app"
    assert list(disabled) == ["t-off"]
    assert disabled["t-off"]["disabled"] == "duplicates other"


def test_with_disabled():
    found = [{"name": "a", "title": "A"}, {"name": "b", "title": "B"}]
    disabled = {"b": {"name": "b", "title": "B", "disabled": "why"},
                "c": {"name": "c", "title": "C", "disabled": "gone"}}
    blocks, missing = generate.with_disabled(found, disabled)
    assert blocks == [{"name": "a", "title": "A"},
                      {"name": "b", "disabled": "why", "title": "B"},
                      {"name": "c", "title": "C", "disabled": "gone"}]
    assert missing == 1
