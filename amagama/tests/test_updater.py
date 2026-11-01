#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import functools
import http.server
import io
import json
import os
import subprocess
import tarfile
import threading

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.updater import compressed, fetchers
from amagama.updater.fetchers import DamnedLies, FetchError, Git, combine
from amagama.updater.sources import load_sources, select, sparse_patterns


def write_tarball(path, members):
    """members: {name: text}"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tarfile.open(path, "w:gz") as tar:
        for name, text in members.items():
            data = text.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def block(name, source, **settings):
    """One [[block]] of a sources file."""
    lines = ['[[block]]', 'title = "%s"' % name,
             'name = "%s"' % name, 'source = "%s"' % source]
    def toml(value):
        if isinstance(value, dict):
            return "{%s}" % ", ".join("%s = %s" % (json.dumps(k), json.dumps(v))
                                      for k, v in value.items())
        return json.dumps(value)
    lines += ['%s = %s' % (key, toml(value)) for key, value in settings.items()]
    return "\n".join(lines) + "\n"


def write_stats(root, **translated):
    statistics = [{"lang_locale": lang, "ui": {"translated": count}, "doc": {"translated": 0}}
                  for lang, count in translated.items()]
    (root / "api/v1/releases/gnome-51/stats").write_text(
        json.dumps({"release": {"name": "gnome-51"}, "statistics": statistics}))


class QuietHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, *args):
        pass


@pytest.fixture
def site(tmp_path):
    """A local web server standing in for l10n.gnome.org and the TP."""
    root = tmp_path / "site"
    (root / "api/v1/releases/gnome-51").mkdir(parents=True)
    write_stats(root, af=10, zu=5, ach=0)
    for lang in ("af", "zu"):
        write_tarball(str(root / "languages" / lang / "gnome-51/ui.tar.gz"), {
            "glib.glib-2-90.%s.po" % lang: "msgid \"\"\nmsgstr \"\"\n",
            "glib.glib-2-90.pot": "msgid \"\"\nmsgstr \"\"\n",
        })
        write_tarball(str(root / "backups/last" / ("%s.tgz" % lang)), {
            "%s/bash-5.2.%s.po" % (lang, lang): "msgid \"\"\nmsgstr \"\"\n",
        })
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield root, "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "po").mkdir(parents=True)
    (repo / "po/af.po").write_text("af")
    (repo / "po/zu.po").write_text("zu")

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                        "-C", str(repo)] + list(args), check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "one")
    return repo, git


@pytest.fixture
def run(amagama_config, tmp_path):
    cache = tmp_path / "cache"
    sources = tmp_path / "sources.toml"

    def run(sources_text, *args):
        sources.write_text(sources_text)
        amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(cache))
        return CliRunner().invoke(manage, ("fetch_sources",) + args)
    run.cache = cache
    return run


def fetched(cache, block):
    with open(os.path.join(cache, block, "fetched.json")) as f:
        return [tuple(file) for file in json.load(f)["files"]]


GNOME = '''
[[block]]
title = "T"
name = "gnome-51"
source = "damned-lies"
release = "gnome-51"
base_url = "%s"
# Fetched whenever the release's statistics change, not in rotation.
max_age_days = 0
'''


def test_disabled_blocks(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(block("on", "translationproject")
                    + block("off", "translationproject", disabled="duplicates on"))
    assert [b.name for b in load_sources(str(path))] == ["on"]
    assert [(b.name, b.enabled, b.disabled) for b in load_sources(str(path), disabled=True)] == [
        ("on", True, None), ("off", False, "duplicates on")]
    # A disabled block keeps its name.
    path.write_text(block("off", "translationproject", disabled="duplicates on")
                    + block("off", "translationproject"))
    with pytest.raises(ValueError, match="block off: duplicate name"):
        load_sources(str(path))


@pytest.mark.parametrize("reason", ["", " ", True])
def test_disabled_needs_reason(tmp_path, reason):
    path = tmp_path / "sources.toml"
    path.write_text(block("off", "translationproject", disabled=reason))
    with pytest.raises(ValueError, match="disabled must be the reason"):
        load_sources(str(path))


def test_tags(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text('[defaults]\ntags = ["core"]\n\n'
                    + block("a", "translationproject")
                    + block("b", "translationproject", tags=["trial"])
                    + block("c", "translationproject", tags=["terminology"])
                    + block("d", "git", url="https://git.example/d", files=["po/{lang}.po"],
                            segment=True))
    assert {b.name: b.tags for b in load_sources(str(path))} == {
        "a": {"core", "ui"}, "b": {"core", "trial", "ui"}, "c": {"core", "terminology"},
        "d": {"core", "docs"}}


@pytest.mark.parametrize("tags, error", [
    ("core", "tags must be a list of tags"),
    (["kore"], "unknown kore"),
    (["core", "platform"], "tags can have one of core, platform, discovered"),
])
def test_tags_checked(tmp_path, tags, error):
    path = tmp_path / "sources.toml"
    path.write_text(block("a", "translationproject", tags=tags))
    with pytest.raises(ValueError, match=error):
        load_sources(str(path))


def test_select(tmp_path):
    path = tmp_path / "sources.toml"
    path.write_text(block("a", "translationproject", tags=["core"])
                    + block("b", "translationproject", tags=["platform", "trial"])
                    + block("c", "translationproject"))
    blocks = load_sources(str(path))

    def names(*args):
        return [b.name for b in select(blocks, *args)]
    assert names() == ["a", "b", "c"]
    assert names(["core", "platform"]) == ["a", "b"]
    assert names(None, ["trial"]) == ["a", "c"]
    assert names(["ui"], ["core"]) == ["b", "c"]
    with pytest.raises(ValueError, match="unknown kore"):
        select(blocks, ["kore"])


def test_fetch_sources_tags(run, amagama_config):
    sources = (block("a", "translationproject", tags=["core"])
               + block("b", "translationproject", tags=["platform"])
               + block("c", "translationproject", tags=["platform", "trial"]))
    result = run(sources, "--check")
    assert result.output.split() == ["a", "b", "c"]
    result = run(sources, "--check", "--tag", "platform")
    assert result.output.split() == ["b", "c"]
    amagama_config(UPDATER_TAGS=["platform"], UPDATER_EXCLUDE_TAGS=["trial"])
    result = run(sources, "--check")
    assert result.output.split() == ["b"]
    result = run(sources, "--check", "--block", "a")
    assert result.exit_code == 2
    assert "No block named a." in result.output
    result = run(sources, "--check", "--tag", "kore")
    assert result.exit_code == 2


def test_update_tags(amagama_config, tmp_path, monkeypatch):
    from amagama.updater import pipeline
    called = {}
    monkeypatch.setattr(pipeline, "update", lambda *args, **kwargs: called.update(kwargs))
    amagama_config(UPDATER_TAGS=["core"], UPDATER_EXCLUDE_TAGS=["trial"])
    result = CliRunner().invoke(manage, ("update", "-s", "en"))
    assert result.exit_code == 0, result.output
    assert (called["tags"], called["exclude_tags"]) == (["core"], ["trial"])


@pytest.mark.parametrize("settings, error", [
    ({"language_in_ids": "yes", "format": "json", "template": "en.json"},
     "language_in_ids must be true or false"),
    ({"language_in_ids": True}, "language_in_ids is for blocks with a template"),
])
def test_language_in_ids_checked(tmp_path, settings, error):
    path = tmp_path / "sources.toml"
    path.write_text(block("x", "git", url="https://git.example/x", files=["{lang}.json"],
                          **settings))
    with pytest.raises(ValueError, match=error):
        load_sources(str(path))


class TestSourcesFile(object):

    def test_shipped_sources(self):
        blocks = load_sources()
        assert len(blocks) == 2443
        assert (blocks[0].name, blocks[0].title, blocks[0].source) == \
            ("legacy-2019", "Legacy OSS", "snapshot")
        assert [block.name for block in blocks[1:9]] == ["gnome-%d" % n for n in range(44, 52)]
        assert {block.title for block in blocks[1:9]} == {"GNOME"}
        assert blocks[8].display_title == "GNOME 51"
        assert blocks[0].display_title == "Legacy OSS"

    @pytest.mark.parametrize("text, error", [
        ('[[block]]\nsource = "git"', "block 1: no name"),
        (block("a/b", "translationproject"), "can't contain /"),
        (block("a", "cvs"), "source must be one of"),
        (block("a", "translationproject") + block("a", "translationproject"),
         "block a: duplicate name"),
        (block("a", "damned-lies"), "block a: missing release"),
        (block("a", "translationproject", branch="x"), "unknown branch"),
        (block("a", "git", url="x"), "files must give each file's language with {lang}"),
        (block("a", "git", url="x", files=["po/*.po"]), "files must give each file's language"),
        (block("a", "git", url="x", files="po/{lang}.po"), "files must be a list of filemasks"),
        (block("a", "translationproject", files=["../{lang}.po"]), "bad filemask"),
        (block("a", "translationproject", files=["{lang}/{lang}.po"]), "{lang} at most once"),
        (block("a", "translationproject", exclude=["{lang}.po"]), "not in exclude"),
        (block("a", "translationproject", languages=["af"]), "languages must map codes"),
        (block("a", "translationproject", format="yaml"), "format must be one of"),
        (block("a", "translationproject", format="android"), "format android needs a template"),
        (block("a", "translationproject", format="po", template="en.po"),
         "template is for monolingual formats only"),
        (block("a", "translationproject", format="json", files=["{lang}.json"],
               template="{lang}.json"), "template must be a path, without {lang}"),
        (block("a", "translationproject", format="json", files=["*/{lang}.json"],
               template="en.json"), "template must have the same wildcards"),
        (block("a", "translationproject", format="json", template="en.json"),
         "template must have the same wildcards"),
        (block("a", "translationproject", exclude_ids="@*"), "exclude_ids must be a list"),
        (block("a", "translationproject", keep="*.grdp"), "keep must be a list of filemasks"),
        (block("a", "translationproject", keep=["{lang}.grdp"]), "not in exclude"),
        (block("a", "translationproject", columns=["source", "target"]),
         "columns and header_row are for format csv only"),
        (block("a", "translationproject", format="csv", columns=["source", "english"]),
         "columns must list"),
        (block("a", "translationproject", format="csv", columns=["source"]), "columns must list"),
        (block("a", "translationproject", format="csv", header_row=3),
         "header_row must be true, false or the first cell"),
        (block("a", "translationproject", format="csv", header_row=True), "header_row needs columns"),
        (block("a", "translationproject", format="po", xliff_states=["none"]),
         "xliff_states is for XLIFF files only"),
        (block("a", "translationproject", xliff_states=["done"]), "xliff_states must list"),
        (block("a", "translationproject", xliff_states=[]), "xliff_states must list"),
        (block("a", "translationproject", max_age_days=-1), "max_age_days must be a whole number"),
        (block("a", "translationproject", max_age_days=1.5), "max_age_days must be a whole number"),
        (block("a", "translationproject", segment="yes"), "segment must be true or false"),
        (block("a", "translationproject", weight=11), "weight must be a number from 0 to 10"),
        (block("a", "translationproject", weight=-0.5), "weight must be a number from 0 to 10"),
        (block("a", "translationproject", weight="1"), "weight must be a number from 0 to 10"),
        (block("a", "translationproject", weight=True), "weight must be a number from 0 to 10"),
        (block("a", "git", url="x", files=["{lang}.po"], sparse="*.po"),
         "sparse must be a list of patterns"),
        (block("a", "http", base_url="x", path="all.po"), "path must contain {lang}"),
        (block("a", "http", base_url="x", path="{lang}.po", fallback="*.po"),
         "fallback must contain {lang}"),
        (block("a", "translationproject", licence="GPL2"), "not a current SPDX licence expression"),
        ('[[block]\n', "sources.toml"),
        ('[[block]]\nname = "a"\ntitle = " "\nsource = "translationproject"',
         "block a: no title"),
        ('[[block]]\nname = "a"\ntitle = "A"\nsource = "snapshot"',
         "block a: missing snapshot"),
        ('[[block]]\nname = "a"\ntitle = "A"\nversion = 51\nsource = "translationproject"',
         "block a: version must be a non-empty string"),
    ])
    def test_invalid(self, tmp_path, text, error):
        path = tmp_path / "sources.toml"
        path.write_text(text)
        with pytest.raises(ValueError, match=error):
            load_sources(str(path))

    def test_defaults(self, tmp_path):
        (tmp_path / "sources.toml").write_text(
            'include = ["w.toml"]\n[defaults]\nsource = "translationproject"\nmin_age_days = 60\n'
            '[[block]]\nname = "a"\ntitle = "A"\n'
            '[[block]]\nname = "b"\ntitle = "B"\nmin_age_days = 7\n')
        (tmp_path / "w.toml").write_text(block("c", "translationproject"))
        blocks = {b.name: b for b in load_sources(str(tmp_path / "sources.toml"))}
        assert {name: (b.source, b.min_age_days) for name, b in blocks.items()} == {
            "a": ("translationproject", 60), "b": ("translationproject", 7),
            # An included file's blocks don't take the including file's defaults.
            "c": ("translationproject", 0)}

    def test_weight(self, tmp_path):
        (tmp_path / "sources.toml").write_text(
            '[defaults]\nsource = "translationproject"\nweight = 0.5\n'
            '[[block]]\nname = "a"\ntitle = "A"\n'
            '[[block]]\nname = "b"\ntitle = "B"\nweight = 2\n')
        (tmp_path / "plain.toml").write_text(block("c", "translationproject"))
        blocks = load_sources(str(tmp_path / "sources.toml")) + load_sources(
            str(tmp_path / "plain.toml"))
        assert [(b.name, b.weight) for b in blocks] == [("a", 0.5), ("b", 2.0), ("c", 1.0)]

    @pytest.mark.parametrize("text", ['defaults = 1\n', '[defaults]\nname = "x"\n',
                                      '[defaults]\nversions = ["1"]\n'])
    def test_defaults_invalid(self, tmp_path, text):
        (tmp_path / "sources.toml").write_text(text)
        with pytest.raises(ValueError, match="defaults must be a table of block settings"):
            load_sources(str(tmp_path / "sources.toml"))

    def test_include(self, tmp_path):
        (tmp_path / "more").mkdir()
        (tmp_path / "sources.toml").write_text(
            'include = ["more/weblate.toml"]\n' + block("a", "translationproject"))
        (tmp_path / "more/weblate.toml").write_text(
            'include = ["deeper.toml"]\n' + block("b", "translationproject"))
        (tmp_path / "more/deeper.toml").write_text(block("c", "translationproject"))
        assert [b.name for b in load_sources(str(tmp_path / "sources.toml"))] == ["a", "b", "c"]

    @pytest.mark.parametrize("files, error", [
        ({"sources.toml": 'include = ["w.toml"]\n' + block("a", "translationproject"),
          "w.toml": block("a", "translationproject")}, "w.toml: block a: duplicate name"),
        ({"sources.toml": 'include = ["w.toml"]\n', "w.toml": 'include = ["sources.toml"]\n'},
         "sources.toml is included twice"),
        ({"sources.toml": 'include = "w.toml"\n'}, "include must be a list of files"),
        ({"sources.toml": 'include = ["missing.toml"]\n'}, "missing.toml"),
    ])
    def test_include_invalid(self, tmp_path, files, error):
        for name, text in files.items():
            (tmp_path / name).write_text(text)
        with pytest.raises((ValueError, OSError), match=error):
            load_sources(str(tmp_path / "sources.toml"))

    def test_licence_normalised(self, tmp_path):
        path = tmp_path / "sources.toml"
        path.write_text(block("a", "translationproject", licence="gpl-2.0-or-later"))
        assert load_sources(str(path))[0].licence == "GPL-2.0-or-later"


class TestFetch(object):

    def test_damned_lies(self, run, site, tmp_path):
        root, url = site
        result = run(GNOME % url)
        assert result.exit_code == 0, result.output
        assert "gnome-51: 2 files, 2 languages." in result.output
        assert fetched(run.cache, "gnome-51") == [
            ("af/glib.glib-2-90.af.po", "af"), ("zu/glib.glib-2-90.zu.po", "zu")]
        # Kept compressed.
        assert not (run.cache / "gnome-51/af/glib.glib-2-90.af.po").exists()
        compressed.decompress(str(run.cache / "gnome-51/af/glib.glib-2-90.af.po.zst"),
                              str(tmp_path / "af.po"))
        assert (tmp_path / "af.po").read_text() == "msgid \"\"\nmsgstr \"\"\n"
        assert not (run.cache / "gnome-51/af/glib.glib-2-90.pot").exists()
        # Nothing translated: not downloaded (its tarball would be only templates).
        assert not (run.cache / "gnome-51/ach").exists()

    def test_damned_lies_languages_per_tarball(self, site):
        root, url = site
        (root / "api/v1/releases/gnome-51/stats").write_text(json.dumps({"statistics": [
            {"lang_locale": "af", "ui": {"translated": 5}, "doc": {"translated": 2}},
            {"lang_locale": "zu", "ui": {"translated": 5}, "doc": {"translated": 0}},
        ]}))
        help_block = DamnedLies({"release": "gnome-51", "tarballs": ["doc"], "base_url": url})
        ui_block = DamnedLies({"release": "gnome-51", "base_url": url})
        assert help_block.languages() == ["af"]
        assert ui_block.languages() == ["af", "zu"]

    def compendia(self, root):
        for lang in ("de", "fr"):
            path = root / "compendia" / lang / ("compendium-%s-LATEST.po" % lang)
            path.parent.mkdir(parents=True)
            path.write_text("msgid \"\"\nmsgstr \"\"\n")
        # Only pre-compendia: the newest is used.
        for stamp in ("20261023", "20261024"):
            path = root / "compendia/wo" / ("pre-compendium-wo-stamp%s.po" % stamp)
            path.parent.mkdir(exist_ok=True)
            path.write_text("msgid \"\"\nmsgstr \"\"\n")
        # Listed, but with neither.
        (root / "compendia/xh").mkdir()
        return block("debian", "http", base_url=self.url + "/compendia/", max_age_days=0,
                     path="{lang}/compendium-{lang}-LATEST.po",
                     fallback="{lang}/pre-compendium-{lang}-stamp*.po", pause=0)

    def test_http_per_language(self, run, site):
        root, self.url = site
        result = run(self.compendia(root))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "debian") == [
            ("de/compendium-de-LATEST.po", "de"), ("fr/compendium-fr-LATEST.po", "fr"),
            ("wo/pre-compendium-wo-stamp20261024.po", "wo")]
        assert not (run.cache / "debian/xh").exists()

    def test_http_per_language_change(self, run, site):
        root, self.url = site
        text = self.compendia(root)
        run(text)
        assert run(text, "--check").output == ""
        path = root / "compendia/fr/compendium-fr-LATEST.po"
        path.write_text("changed")
        mtime = os.stat(path).st_mtime + 10
        os.utime(path, (mtime, mtime))
        assert run(text, "--check").output == "debian\n"

    def test_tarballs_paced(self, run, site, pauses):
        root, url = site
        run(block("tp", "translationproject", base_url=url + "/backups/last/", pause=2,
                  max_age_days=0))
        assert pauses == [2, 2]

    def test_translationproject(self, run, site):
        root, url = site
        result = run(block("tp", "translationproject", base_url=url + "/backups/last/",
                           max_age_days=0))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "tp") == [("af/bash-5.2.af.po", "af"), ("zu/bash-5.2.zu.po", "zu")]

    def test_languages_normalised(self, run, git_repo):
        repo, git = git_repo
        (repo / "po/pt-BR.po").write_text("pt")
        (repo / "po/sr-Latn.po").write_text("sr")
        git("add", ".")
        git("commit", "-q", "-m", "codes")
        result = run(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"],
                           languages={"zu": ""}))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "x") == [
            ("po/af.po", "af"), ("po/pt-BR.po", "pt_BR"), ("po/sr-Latn.po", "sr@latin")]
        assert "not language codes" not in result.output

    def test_language_overrides(self, run, site):
        root, url = site
        result = run(block("tp", "translationproject", base_url=url + "/backups/last/",
                           languages={"af": "af_ZA", "zu": ""}))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "tp") == [("af/bash-5.2.af.po", "af_ZA")]

    def test_unknown_language_codes(self, run, site):
        root, url = site
        write_tarball(str(root / "backups/last/x!.tgz"), {"x!/bash-5.2.x!.po": "x"})
        result = run(block("tp", "translationproject", base_url=url + "/backups/last/",
                           max_age_days=0))
        assert result.exit_code == 0, result.output
        assert "tp: not language codes, skipped: x!" in result.output
        assert [lang for path, lang in fetched(run.cache, "tp")] == ["af", "zu"]

    def test_merged_languages(self, run, git_repo):
        repo, git = git_repo
        (repo / "po/de.po").write_text("de")
        (repo / "po/de-formal.po").write_text("de")
        (repo / "po/sr-Latn.po").write_text("sr")
        (repo / "po/sr@latin.po").write_text("sr")
        git("add", ".")
        git("commit", "-q", "-m", "variants")
        result = run(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"],
                           languages={"de-formal": "de"}))
        assert result.exit_code == 0, result.output
        # Accepted in languages: not reported; sr-Latn and sr@latin weren't.
        assert "x: codes merged: sr-Latn, sr@latin -> sr@latin" in result.output
        assert "-> de" not in result.output
        with open(run.cache / "x/fetched.json") as f:
            state = json.load(f)
        assert state["languages"]["de"] == ["de", "de-formal"]
        assert list(state["merged_languages"]) == ["sr@latin"]

    def test_git(self, run, git_repo):
        repo, git = git_repo
        (repo / "README").write_text("not a translation")
        git("add", ".")
        git("commit", "-q", "-m", "readme")
        result = run(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"]))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "x") == [("po/af.po", "af"), ("po/zu.po", "zu")]
        assert not (run.cache / "x/.git").exists()

    def test_unchanged_files_not_compressed_again(self, run, git_repo, tmp_path, monkeypatch):
        repo, git = git_repo
        text = block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"])
        assert run(text).exit_code == 0
        (repo / "po/af.po").write_text("af changed")
        git("commit", "-q", "-am", "af")
        done = []
        compress = compressed.compress
        monkeypatch.setattr(compressed, "compress", lambda path: done.append(path) or compress(path))
        assert run(text).exit_code == 0
        assert [os.path.relpath(path, run.cache / "x.new") for path in done] == ["po/af.po"]
        for lang, text in (("af", "af changed"), ("zu", "zu")):
            compressed.decompress(str(run.cache / "x/po" / (lang + ".po.zst")), str(tmp_path / lang))
            assert (tmp_path / lang).read_text() == text

    def test_git_symlinks(self, run, git_repo, tmp_path):
        """A link to a kept file is kept; one leading outside the clone isn't."""
        repo, git = git_repo
        (tmp_path / "secret").write_text("not for import")
        # Linked to a kept file, and to a file that isn't kept.
        os.symlink("af.po", repo / "po/af_ZA.po")
        (repo / "README").write_text("readme")
        os.symlink("../README", repo / "po/ab.po")
        # Leading outside the clone.
        os.symlink(str(tmp_path / "secret"), repo / "po/xh.po")
        os.symlink("../../secret", repo / "po/st.po")
        git("add", ".")
        git("commit", "-q", "-m", "links")
        # The whole tree, so the link targets are there.
        result = run(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"], sparse=["**"]))
        assert result.exit_code == 0, result.output
        assert [path for path, lang in fetched(run.cache, "x")] == [
            "po/ab.po", "po/af.po", "po/af_ZA.po", "po/zu.po"]
        assert "x: symlinks leading outside the block, skipped: po/st.po, po/xh.po" in result.output
        unpacked = tmp_path / "ab.po"
        compressed.decompress(str(run.cache / "x/po/ab.po.zst"), str(unpacked))
        assert unpacked.read_text() == "readme"
        assert not list((run.cache / "x").rglob("*.po"))

    def test_git_nongnu(self, run, git_repo):
        repo, git = git_repo
        (repo / "af").mkdir()
        (repo / "af/app.po").write_text("af")
        (repo / "README").write_text("not in a language directory")
        git("add", ".")
        git("commit", "-q", "-m", "nongnu")
        # po/af.po would match too, with "po" as its language.
        result = run(block("x", "git", url=repo.as_uri(), files=["{lang}/*.po"], exclude=["po/**"]))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "x") == [("af/app.po", "af")]
        assert not (run.cache / "x/README").exists()

    def test_git_sparse(self, git_repo, tmp_path):
        repo, git = git_repo
        (repo / "res/values-af").mkdir(parents=True)
        (repo / "res/values-af/strings.xml").write_text("af")
        (repo / "res/values-af/icon.png").write_text("not text")
        git("add", ".")
        git("commit", "-q", "-m", "res")
        fetcher = Git({"url": repo.as_uri(), "sparse": ["res/values*/strings.xml"]})
        assert fetcher.fetch(str(tmp_path / "x")) == [("res/values-af/strings.xml", None)]

    def test_git_shared_repository(self, run, git_repo):
        repo, git = git_repo
        (repo / "help").mkdir()
        (repo / "help/af.po").write_text("af help")
        (repo / "big.bin").write_bytes(os.urandom(100000))
        git("add", ".")
        git("commit", "-q", "-m", "help")
        git("config", "uploadpack.allowfilter", "true")
        text = (block("ui", "git", url=repo.as_uri(), files=["po/{lang}.po"])
                + block("help", "git", url=repo.as_uri(), files=["help/{lang}.po"]))
        assert run(text).exit_code == 0
        assert fetched(run.cache, "ui") == [("po/af.po", "af"), ("po/zu.po", "zu")]
        assert fetched(run.cache, "help") == [("help/af.po", "af")]
        # One repository for both, without the files neither reads.
        shared = run.cache / ".repos/local"
        assert [path.name for path in shared.rglob("*.git")] == ["repo.git"]
        clone = next(shared.rglob("*.git"))
        missing = fetchers.run_tool("git", "-C", str(clone), "rev-list", "--objects",
                                    "--missing=print", "refs/fetched/HEAD", trusted=str(clone))
        assert len([line for line in missing.splitlines() if line.startswith("?")]) == 1
        assert not fetchers.run_tool("git", "-C", str(clone), "worktree", "list", "--porcelain",
                                     trusted=str(clone)).count("worktree") > 1
        # A refetch brings the change.
        (repo / "po/af.po").write_text("af changed")
        git("commit", "-q", "-am", "af")
        assert run(text, "--block", "ui").exit_code == 0
        compressed.decompress(str(run.cache / "ui/po/af.po.zst"), str(run.cache / "af"))
        assert (run.cache / "af").read_text() == "af changed"

    def test_git_sparse_from_files(self, git_repo, tmp_path):
        repo, git = git_repo
        (repo / "README").write_text("not a translation")
        git("add", ".")
        git("commit", "-q", "-m", "readme")
        sources = tmp_path / "sources.toml"
        sources.write_text(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"]))
        fetcher = load_sources(str(sources))[0].fetcher
        assert fetcher.settings["sparse"] == ["po/*.po"]
        assert fetcher.fetch(str(tmp_path / "x")) == [("po/af.po", None), ("po/zu.po", None)]

    def test_sparse_patterns(self):
        assert sparse_patterns(["a/{lang}.json", "b/**/{lang}.json"], "a/en.json",
                               ["b/**/*.grdp"]) == ["a/*.json", "a/en.json", "b/**/*.grdp",
                                                    "b/**/*.json"]

    def test_gitiles_fingerprint(self, monkeypatch):
        urls = []

        def http_text(url):
            urls.append(url)
            return ')]}\'\n{"commit": "eb3df117", "tree": "d58fe66d"}'
        monkeypatch.setattr(fetchers, "http_text", http_text)
        git = Git({"url": "https://chromium.googlesource.com/chromium/src", "ref": "main"})
        assert git.fingerprint() == "eb3df117"
        assert urls == ["https://chromium.googlesource.com/chromium/src/+/main?format=JSON"]

    def test_gitiles_fingerprint_bad_json(self, monkeypatch):
        monkeypatch.setattr(fetchers, "http_text", lambda url: "<html>")
        with pytest.raises(FetchError, match="format=JSON"):
            Git({"url": "https://chromium.googlesource.com/chromium/src"}).fingerprint()

    def test_template(self, run, git_repo):
        repo, git = git_repo
        (repo / "en.json").write_text('{"save": "Save"}')
        git("add", ".")
        git("commit", "-q", "-m", "en")
        result = run(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"],
                           format="json", template="en.json"))
        assert result.exit_code == 0, result.output
        assert result.output == "x: 2 files, 2 languages.\n"

    def test_git_ref(self, run, git_repo):
        repo, git = git_repo
        git("checkout", "-q", "-b", "stable")
        (repo / "po/nl.po").write_text("nl")
        git("add", ".")
        git("commit", "-q", "-m", "stable")
        git("checkout", "-q", "main")
        result = run(block("x", "git", url=repo.as_uri(), ref="stable", files=["po/{lang}.po"]))
        assert result.exit_code == 0, result.output
        assert ("po/nl.po", "nl") in fetched(run.cache, "x")

    def test_git_missing_path(self, run, git_repo):
        repo, git = git_repo
        result = run(block("x", "git", url=repo.as_uri(), files=["l10n/{lang}.po"]))
        assert result.exit_code == 1
        assert "x: failed: no file matches l10n/{lang}.po" in result.output
        assert not run.cache.joinpath("x").exists()

    def test_git_unreachable(self, run, tmp_path):
        result = run(block("x", "git", url=(tmp_path / "missing").as_uri(), files=["{lang}.po"]))
        assert result.exit_code == 1
        assert "x: failed: git ls-remote" in result.output

    def test_corrupt_tarball(self, run, site):
        root, url = site
        (root / "languages/zu/gnome-51/ui.tar.gz").write_bytes(b"not a tarball")
        result = run(GNOME % url)
        assert result.exit_code == 1
        assert "gnome-51: failed:" in result.output
        # No partial fetch, just the failure on record.
        assert os.listdir(run.cache) == ["gnome-51.failed"]

    def test_snapshot(self, run):
        text = '[[block]]\ntitle = "Legacy OSS"\nname = "legacy-2019"\n' \
            'source = "snapshot"\nsnapshot = "legacy"'
        result = run(text)
        assert result.exit_code == 0, result.output
        assert "legacy-2019: 0 files, 0 languages." in result.output
        assert run(text, "--check").output == ""

    def test_select_from_tarballs(self, run, site):
        root, url = site
        result = run(block("tp", "translationproject", base_url=url + "/backups/last/",
                           files=["{lang}/*.po"], exclude=["zu/**"]))
        assert result.exit_code == 0, result.output
        assert fetched(run.cache, "tp") == [("af/bash-5.2.af.po", "af")]
        assert not (run.cache / "tp/zu").exists()

    def test_failure_keeps_previous(self, run, site):
        root, url = site
        run(GNOME % url)
        (root / "languages/zu/gnome-51/ui.tar.gz").unlink()
        write_stats(root, af=11, zu=5, ach=0)
        result = run(GNOME % url)
        assert result.exit_code == 1
        assert "gnome-51: failed:" in result.output
        assert "404" in result.output
        assert len(fetched(run.cache, "gnome-51")) == 2
        assert sorted(os.listdir(run.cache)) == ["gnome-51", "gnome-51.failed"]
        with open(run.cache / "gnome-51.failed") as f:
            assert json.load(f)["runs"] == 1
        # A fetch that works again clears it.
        write_tarball(str(root / "languages/zu/gnome-51/ui.tar.gz"),
                      {"glib.glib-2-90.zu.po": "msgid \"\"\nmsgstr \"\"\n"})
        assert run(GNOME % url).exit_code == 0
        assert sorted(os.listdir(run.cache)) == ["gnome-51"]

    def test_unchanged_not_fetched(self, run, site):
        root, url = site
        run(GNOME % url)
        result = run(GNOME % url)
        assert result.exit_code == 0, result.output
        assert result.output == "Unchanged since the last fetch: 1 blocks.\n"
        # Named, it's fetched anyway.
        assert "gnome-51: 2 files" in run(GNOME % url, "--block", "gnome-51").output

    def test_only_named_block(self, run, site):
        root, url = site
        text = GNOME % url + block("tp", "translationproject", base_url=url + "/backups/last/")
        result = run(text, "--block", "tp")
        assert result.exit_code == 0, result.output
        assert os.listdir(run.cache) == ["tp"]

    def test_unknown_block(self, run, site):
        root, url = site
        result = run(GNOME % url, "--block", "kde")
        assert result.exit_code == 2
        assert "No block named kde." in result.output

    def test_bad_sources_file(self, run):
        result = run(block("a", "cvs"))
        assert result.exit_code == 1
        assert "source must be one of" in result.output


class TestFirstFetches(object):

    def sources(self, url, *names):
        return "".join(block(name, "translationproject", base_url=url + "/backups/last/")
                       for name in names)

    def test_empty_cache_fills_over_runs(self, run, site, amagama_config):
        root, url = site
        amagama_config(UPDATER_FIRST_FETCHES=2)
        text = self.sources(url, "a", "b", "c")
        result = run(text)
        assert result.exit_code == 0, result.output
        assert "Blocks not fetched yet, left for later runs: 1." in result.output
        assert len(os.listdir(run.cache)) == 2
        result = run(text)
        assert "left for later" not in result.output
        assert sorted(os.listdir(run.cache)) == ["a", "b", "c"]

    def test_failed_first_fetch_does_not_count(self, run, site, amagama_config, tmp_path):
        root, url = site
        amagama_config(UPDATER_FIRST_FETCHES=1)
        bad = block("x", "git", url=(tmp_path / "missing").as_uri(), files=["{lang}.po"])
        assert run(bad).exit_code == 1
        assert os.listdir(run.cache) == ["x.failed"]
        # x is retried but doesn't take the one first fetch.
        result = run(bad + self.sources(url, "a"))
        assert result.exit_code == 1
        assert "x: failed" in result.output
        assert sorted(os.listdir(run.cache)) == ["a", "x.failed"]

    def test_named_blocks_ignore_limit(self, run, site, amagama_config):
        root, url = site
        amagama_config(UPDATER_FIRST_FETCHES=1)
        result = run(self.sources(url, "a", "b"), "--block", "a", "--block", "b")
        assert result.exit_code == 0, result.output
        assert sorted(os.listdir(run.cache)) == ["a", "b"]

    def test_no_limit(self, run, site, amagama_config):
        root, url = site
        amagama_config(UPDATER_FIRST_FETCHES=None)
        assert run(self.sources(url, "a", "b")).exit_code == 0
        assert sorted(os.listdir(run.cache)) == ["a", "b"]


class TestCheck(object):

    def test_unfetched_block_changed(self, run, site):
        root, url = site
        result = run(GNOME % url, "--check")
        assert result.exit_code == 0, result.output
        assert result.output == "gnome-51\n"
        assert not run.cache.exists()

    def test_damned_lies_statistics_change(self, run, site):
        root, url = site
        run(GNOME % url)
        assert run(GNOME % url, "--check").output == ""
        write_stats(root, af=11, zu=5, ach=0)
        assert run(GNOME % url, "--check").output == "gnome-51\n"

    def test_tarball_change(self, run, site):
        root, url = site
        text = block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=0)
        run(text)
        assert run(text, "--check").output == ""
        tarball = root / "backups/last/af.tgz"
        write_tarball(str(tarball), {"af/bash-5.2.af.po": "changed"})
        mtime = os.stat(tarball).st_mtime + 10
        os.utime(tarball, (mtime, mtime))
        assert run(text, "--check").output == "tp\n"

    def test_git_change(self, run, git_repo):
        repo, git = git_repo
        text = block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"])
        run(text)
        assert run(text, "--check").output == ""
        (repo / "po/af.po").write_text("af, changed")
        git("commit", "-q", "-am", "two")
        assert run(text, "--check").output == "x\n"

    def test_min_age_days(self, run, git_repo, monkeypatch):
        repo, git = git_repo
        text = block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"], min_age_days=10)
        run(text)
        (repo / "po/af.po").write_text("af, changed")
        git("commit", "-q", "-am", "two")
        state_path = run.cache / "x/fetched.json"
        state = json.loads(state_path.read_text())

        def age(days, fetches):
            fetched = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
            state_path.write_text(json.dumps(dict(state, fetched=fetched.isoformat(),
                                                  fetches=fetches)))
        # Resting: not even checked.
        age(0, 2)
        with monkeypatch.context() as patch:
            patch.setattr(Git, "fingerprint", lambda self: pytest.fail("checked"))
            assert run(text, "--check").output == ""
        age(10, 2)
        assert run(text, "--check").output == "x\n"

    def test_first_rest_staggered(self):
        def rest(name, fetches):
            b = load_sources_text(block(name, "translationproject", min_age_days=60))[0]
            return b.rest({"fetches": fetches}).days
        firsts = {rest("w%d" % i, 1) for i in range(20)}
        # Spread over the period, the same each time for a block.
        assert len(firsts) > 10 and max(firsts) < 60 and rest("w1", 1) == rest("w1", 1)
        assert rest("w1", 2) == 60

    def test_no_fingerprint_always_changed(self):
        assert combine([("a", "etag"), ("b", None)]) is None
        assert combine([("a", "1"), ("b", "2")]) == combine([("b", "2"), ("a", "1")])


class TestVersions(object):

    def load(self, tmp_path, text):
        path = tmp_path / "sources.toml"
        path.write_text(text)
        return load_sources(str(path))

    def test_expand(self, tmp_path):
        blocks = self.load(tmp_path, '''
[[block]]
name = "lo-{version}"
title = "LibreOffice"
source = "git"
url = "https://example.org/translations"
ref = "release-{version}"
files = ["source/{lang}/{version}/*.po"]
languages = { xx = "x-{version}" }
max_age_days = 7
versions = ["26.2", { version = "26.8", ref = "libreoffice-26-8" }]
''')
        assert [(b.name, b.version, b.display_title) for b in blocks] == [
            ("lo-26.2", "26.2", "LibreOffice 26.2"), ("lo-26.8", "26.8", "LibreOffice 26.8")]
        assert [b.fetcher.settings["ref"] for b in blocks] == ["release-26.2", "libreoffice-26-8"]
        # {version} is filled inside lists and tables; {lang} is left for the filemask.
        assert blocks[0].selection.mask_texts == ["source/{lang}/26.2/*.po"]
        assert blocks[1].languages == {"xx": "x-26.8"}
        assert [b.max_age_days for b in blocks] == [7, 7]

    @pytest.mark.parametrize("versions, error", [
        ('"51"', "versions must be a list"),
        ("[]", "versions must be a list"),
        ("[51]", "each of versions must be a version, or a table with one"),
        ('[{ ref = "x" }]', "each of versions must be a version, or a table with one"),
        ('["51", "51"]', "block gnome-51: duplicate name"),
    ])
    def test_invalid(self, tmp_path, versions, error):
        text = ('[[block]]\nname = "gnome-{version}"\ntitle = "GNOME"\nsource = "damned-lies"\n'
                'release = "gnome-{version}"\nversions = %s\n' % versions)
        with pytest.raises(ValueError, match=error):
            self.load(tmp_path, text)

    def test_name_needs_version(self, tmp_path):
        with pytest.raises(ValueError, match="a name with versions must contain {version}"):
            self.load(tmp_path, '[[block]]\nname = "gnome"\ntitle = "GNOME"\n'
                                'source = "damned-lies"\nrelease = "gnome-{version}"\n'
                                'versions = ["51"]\n')

    def test_version_and_versions(self, tmp_path):
        with pytest.raises(ValueError, match="give version or versions, not both"):
            self.load(tmp_path, '[[block]]\nname = "gnome-{version}"\ntitle = "GNOME"\n'
                                'version = "50"\nsource = "damned-lies"\n'
                                'release = "gnome-{version}"\nversions = ["51"]\n')


def load_sources_text(text):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "sources.toml")
        with open(path, "w") as f:
            f.write(text)
        return load_sources(path)


def test_git_trusts_its_clone(tmp_path):
    """git may run on a clone in a cache another user owns (a mounted volume)."""
    def trusted(**kwargs):
        # The machine's own config may trust directories too (CI runners do);
        # the updater's setting is the one git reports as from the command.
        scopes = fetchers.run_tool("git", "config", "--show-scope", "--get-all", "safe.directory",
                                   **kwargs)
        return [line.split()[1] for line in scopes.splitlines() if line.startswith("command")]
    clone = tmp_path / "block.new"
    assert trusted(trusted=str(clone)) == [os.path.realpath(clone)]
    repo = tmp_path / "repo.git"
    assert trusted(trusted=(str(repo), str(clone))) == [os.path.realpath(repo),
                                                        os.path.realpath(clone)]


class TestRotation(object):
    """A per-language source refreshes its languages over max_age_days."""

    LANGS = ("af", "st", "xh", "zu")

    def write(self, root, lang, text):
        write_tarball(str(root / "backups/last" / ("%s.tgz" % lang)),
                      {"%s/bash.%s.po" % (lang, lang): 'msgid "Save"\nmsgstr "%s"\n' % text})

    def cached(self, cache, lang, tmp_path):
        plain = tmp_path / ("%s.po" % lang)
        compressed.decompress(str(cache / "tp" / lang / ("bash.%s.po.zst" % lang)), str(plain))
        return plain.read_text().split('msgstr "')[1].rstrip('"\n')

    def age(self, cache, days, stagger=False):
        """Move the fetch dates back days; with stagger, af oldest and zu newest."""
        path = cache / "tp" / "fetched.json"
        state = json.loads(path.read_text())

        def back(date, extra=0):
            return (datetime.datetime.fromisoformat(date)
                    - datetime.timedelta(days=days + extra)).isoformat()
        dates = state["language_fetched"]
        state["language_fetched"] = {lang: back(dates[lang], 3 - n if stagger else 0)
                                     for n, lang in enumerate(sorted(dates))}
        state["fetched"] = back(state["fetched"])
        path.write_text(json.dumps(state))

    def fill(self, run, text):
        """The first fetch takes a day's share, then each day a language more."""
        for n in range(len(self.LANGS)):
            if n:
                self.age(run.cache, 1)
            assert run(text).exit_code == 0
            state = json.loads((run.cache / "tp/fetched.json").read_text())
            assert sorted(state["language_fetched"]) == list(self.LANGS[:n + 1])
            assert state["language_count"] == len(self.LANGS)

    def test_rotation(self, run, site, tmp_path):
        root, url = site
        for lang in self.LANGS:
            self.write(root, lang, "one")
        text = block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=4)
        self.fill(run, text)
        # Not due again until max_age_days / 4 has passed.
        assert run(text, "--check").output == ""
        for lang in self.LANGS:
            self.write(root, lang, "two")
        # 2 of 4 days: ceil(4 x 2 / 4) = 2 languages, those fetched longest ago.
        self.age(run.cache, 2)
        assert run(text, "--check").output == "tp\n"
        result = run(text)
        assert result.exit_code == 0, result.output
        assert [self.cached(run.cache, lang, tmp_path) for lang in self.LANGS] == [
            "two", "two", "one", "one"]
        state = json.loads((run.cache / "tp/fetched.json").read_text())
        assert len(state["files"]) == 4 and sorted(state["by_language"]) == list(self.LANGS)
        # The other two next time round.
        self.age(run.cache, 2)
        run(text)
        assert [self.cached(run.cache, lang, tmp_path) for lang in self.LANGS] == ["two"] * 4

    def test_languages_come_and_go(self, run, site, tmp_path):
        root, url = site
        for lang in self.LANGS:
            self.write(root, lang, "one")
        text = block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=4)
        self.fill(run, text)
        # A new language goes ahead of the oldest; a withdrawn one is dropped.
        self.write(root, "nso", "one")
        (root / "backups/last/zu.tgz").unlink()
        self.age(run.cache, 1)
        assert run(text).exit_code == 0
        state = json.loads((run.cache / "tp/fetched.json").read_text())
        assert sorted(state["language_fetched"]) == ["af", "nso", "st", "xh"]
        assert state["language_fetched"]["nso"] > state["language_fetched"]["st"]
        assert sorted(lang for path, lang in fetched(run.cache, "tp")) == ["af", "nso", "st", "xh"]
        assert not (run.cache / "tp/zu").exists()

    def test_rotating_after_whole_fetches(self, run, site, tmp_path):
        root, url = site
        for lang in self.LANGS:
            self.write(root, lang, "one")
        run(block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=0))
        # Without dates to rotate by, the first rotating fetch takes every language.
        path = run.cache / "tp/fetched.json"
        state = json.loads(path.read_text())
        state["fetched"] = (datetime.datetime.fromisoformat(state["fetched"])
                            - datetime.timedelta(days=1)).isoformat()
        path.write_text(json.dumps(state))
        run(block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=4))
        state = json.loads(path.read_text())
        assert sorted(state["language_fetched"]) == list(self.LANGS)

    def test_max_age_zero_fetches_whole(self, run, site, tmp_path):
        root, url = site
        for lang in self.LANGS:
            self.write(root, lang, "one")
        text = block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=0)
        run(text)
        state = json.loads((run.cache / "tp/fetched.json").read_text())
        assert "language_fetched" not in state

    def test_carry_copies_without_links(self, tmp_path, monkeypatch):
        """A carried file comes with its template, copied where links fail."""
        from amagama.updater import sources
        final, new = tmp_path / "x", tmp_path / "x.new"
        (final / "af").mkdir(parents=True)
        (final / "af/strings.xml.zst").write_text("af")
        (final / "en.xml").write_text("en")
        (new).mkdir()
        (new / "en.xml").write_text("fetched again")

        def no_links(source, target):
            raise OSError("links not supported")
        monkeypatch.setattr(os, "link", no_links)
        sources._carry(str(final), str(new), ["af/strings.xml", "af", "en.xml"], {})
        assert (new / "af/strings.xml.zst").read_text() == "af"
        assert (new / "en.xml").read_text() == "fetched again"
