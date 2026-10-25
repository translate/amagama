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
from amagama.updater import fetchers
from amagama.updater.fetchers import DamnedLies, FetchError, Git, combine
from amagama.updater.sources import load_sources


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
'''


class TestSourcesFile(object):

    def test_shipped_sources(self):
        blocks = load_sources()
        assert len(blocks) == 332
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
        (block("a", "translationproject", format="csv", header_row="yes"),
         "header_row must be true or false"),
        (block("a", "translationproject", format="csv", header_row=True), "header_row needs columns"),
        (block("a", "translationproject", format="po", xliff_states=["none"]),
         "xliff_states is for XLIFF files only"),
        (block("a", "translationproject", xliff_states=["done"]), "xliff_states must list"),
        (block("a", "translationproject", xliff_states=[]), "xliff_states must list"),
        (block("a", "translationproject", refetch_days=-1), "refetch_days must be a whole number"),
        (block("a", "translationproject", refetch_days=1.5), "refetch_days must be a whole number"),
        (block("a", "translationproject", segment="yes"), "segment must be true or false"),
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

    def test_damned_lies(self, run, site):
        root, url = site
        result = run(GNOME % url)
        assert result.exit_code == 0, result.output
        assert "gnome-51: 2 files, 2 languages." in result.output
        assert fetched(run.cache, "gnome-51") == [
            ("af/glib.glib-2-90.af.po", "af"), ("zu/glib.glib-2-90.zu.po", "zu")]
        assert (run.cache / "gnome-51/af/glib.glib-2-90.af.po").exists()
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
        return block("debian", "http", base_url=self.url + "/compendia/",
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
        run(block("tp", "translationproject", base_url=url + "/backups/last/", pause=2))
        assert pauses == [2, 2]

    def test_translationproject(self, run, site):
        root, url = site
        result = run(block("tp", "translationproject", base_url=url + "/backups/last/"))
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
        result = run(block("tp", "translationproject", base_url=url + "/backups/last/"))
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
        assert os.listdir(run.cache) == []

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
        result = run(GNOME % url)
        assert result.exit_code == 1
        assert "gnome-51: failed:" in result.output
        assert "404" in result.output
        assert len(fetched(run.cache, "gnome-51")) == 2
        assert sorted(os.listdir(run.cache)) == ["gnome-51"]

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
        text = block("tp", "translationproject", base_url=url + "/backups/last/")
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
refetch_days = 7
versions = ["26.2", { version = "26.8", ref = "libreoffice-26-8" }]
''')
        assert [(b.name, b.version, b.display_title) for b in blocks] == [
            ("lo-26.2", "26.2", "LibreOffice 26.2"), ("lo-26.8", "26.8", "LibreOffice 26.8")]
        assert [b.fetcher.settings["ref"] for b in blocks] == ["release-26.2", "libreoffice-26-8"]
        # {version} is filled inside lists and tables; {lang} is left for the filemask.
        assert blocks[0].selection.mask_texts == ["source/{lang}/26.2/*.po"]
        assert blocks[1].languages == {"xx": "x-26.8"}
        assert [b.refetch_days for b in blocks] == [7, 7]

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
