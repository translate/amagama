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
from amagama.updater.fetchers import combine
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


def block(name, source, block_id=1, **settings):
    """One [[block]] of a sources file."""
    lines = ['[[block]]', 'id = %d' % block_id, 'title = "%s"' % name,
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
id = 1
title = "T"
name = "gnome-51"
source = "damned-lies"
release = "gnome-51"
base_url = "%s"
'''


class TestSourcesFile(object):

    def test_shipped_sources(self):
        blocks = load_sources()
        assert [block.id for block in blocks] == list(range(1, 11))
        assert (blocks[0].name, blocks[0].title, blocks[0].source) == \
            ("legacy-2019", "Legacy OSS", "snapshot")
        assert [block.name for block in blocks[1:9]] == ["gnome-%d" % n for n in range(44, 52)]
        assert {block.title for block in blocks[1:9]} == {"GNOME"}
        assert blocks[8].display_title == "GNOME 51"
        assert blocks[0].display_title == "Legacy OSS"

    @pytest.mark.parametrize("text, error", [
        ('[[block]]\nsource = "git"', "block 1: no name"),
        (block("a/b", "translationproject"), "can't contain /"),
        (block("a", "svn"), "source must be one of"),
        (block("a", "translationproject") + block("a", "translationproject", 2),
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
        (block("a", "translationproject", licence="GPL2"), "not a current SPDX licence expression"),
        ('[[block]\n', "sources.toml"),
        ('[[block]]\nname = "a"\ntitle = "A"\nsource = "translationproject"',
         "block a: id must be a positive integer"),
        ('[[block]]\nname = "a"\nid = 0\ntitle = "A"\nsource = "translationproject"',
         "id must be a positive integer"),
        ('[[block]]\nname = "a"\nid = true\ntitle = "A"\nsource = "translationproject"',
         "id must be a positive integer"),
        ('[[block]]\nname = "a"\nid = 1\ntitle = "A"\nsource = "translationproject"\n'
         '[[block]]\nname = "b"\nid = 1\ntitle = "B"\nsource = "translationproject"',
         "block b: duplicate id 1"),
        ('[[block]]\nname = "a"\nid = 1\ntitle = " "\nsource = "translationproject"',
         "block a: no title"),
        ('[[block]]\nname = "a"\nid = 1\ntitle = "A"\nsource = "snapshot"',
         "block a: missing snapshot"),
        ('[[block]]\nname = "a"\nid = 1\ntitle = "A"\nversion = 51\nsource = "translationproject"',
         "block a: version must be a non-empty string"),
    ])
    def test_invalid(self, tmp_path, text, error):
        path = tmp_path / "sources.toml"
        path.write_text(text)
        with pytest.raises(ValueError, match=error):
            load_sources(str(path))

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
        text = '[[block]]\nid = 1\ntitle = "Legacy OSS"\nname = "legacy-2019"\n' \
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
        text = GNOME % url + block("tp", "translationproject", 2, base_url=url + "/backups/last/")
        result = run(text, "--block", "tp")
        assert result.exit_code == 0, result.output
        assert os.listdir(run.cache) == ["tp"]

    def test_unknown_block(self, run, site):
        root, url = site
        result = run(GNOME % url, "--block", "kde")
        assert result.exit_code == 2
        assert "No block named kde." in result.output

    def test_bad_sources_file(self, run):
        result = run(block("a", "svn"))
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
