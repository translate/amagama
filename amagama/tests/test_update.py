#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import http.server
import os
import threading

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.tmdb import BUILD_PREFIX
from amagama.cli import manage
from amagama.updater import compressed
from amagama.updater import pipeline
from amagama.tests.test_updater import QuietHandler, block, write_stats, write_tarball
from amagama.tests.test_updater import git_repo  # noqa: F401 - fixture


def po(lang, *pairs):
    units = "".join('\nmsgid "%s"\nmsgstr "%s"\n' % pair for pair in pairs)
    return ('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
            '"Language: %s\\n"\n%s' % (lang, units))


@pytest.fixture
def site(tmp_path):
    """Local stand-ins for l10n.gnome.org and the Translation Project."""
    root = tmp_path / "site"
    (root / "api/v1/releases/gnome-51").mkdir(parents=True)
    write_stats(root, af=1, zu=1)
    write_gnome(root, "af", ("Open file", "Maak lêer oop"))
    write_gnome(root, "zu", ("Open file", "Vula ifayela"))
    write_tarball(str(root / "backups/last/af.tgz"),
                  {"af/bash-5.2.af.po": po("af", ("Save file", "Stoor lêer"))})
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, "http://127.0.0.1:%d" % server.server_address[1]
    server.shutdown()
    server.server_close()


def write_gnome(root, lang, *pairs, extra=None):
    members = {"glib.glib-2-90.%s.po" % lang: po(lang, *pairs),
               "glib.glib-2-90.pot": po("", ("Open file", ""))}
    members.update(extra or {})
    write_tarball(str(root / "languages" / lang / "gnome-51/ui.tar.gz"), members)


@pytest.fixture
def run(amagama_config, tmp_path, site):
    root, url = site
    sources = tmp_path / "sources.toml"
    sources.write_text(
        block("legacy-2019", "snapshot", snapshot="legacy")
        + block("gnome-51", "damned-lies", release="gnome-51", base_url=url, max_age_days=0,
                licence="GPL-2.0-or-later")
        + block("tp", "translationproject", base_url=url + "/backups/last/", max_age_days=0))
    settings = {"UPDATER_SOURCES": str(sources), "UPDATER_CACHE_DIR": str(tmp_path / "cache"),
                "UPDATER_WORKERS": 1}
    amagama_config(**settings)

    def run(*args, input=None, **extra):
        settings.update(extra)
        amagama_config(**settings)
        return CliRunner().invoke(manage, args, input=input)
    return run


def lookup(lang, source):
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/%s/unit/%s" % (lang, source))
        return [c["target"] for c in response.get_json()] if response.status_code == 200 else []


def query(pg_connection, sql):
    with pg_connection.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    pg_connection.commit()
    return rows


def schemas(pg_connection):
    return {row[0] for row in query(pg_connection, "SELECT nspname FROM pg_namespace")
            if row[0] in ("en", "_build_en", "_old_en", "_legacy_en")}


class TestUpdate(object):

    def test_first_update(self, run, pg_connection):
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Skipped legacy-2019: no snapshot _legacy_en." in result.output
        assert "Imported 3 files: 3 units (0 skipped)." in result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]
        assert lookup("zu", "Open file") == ["Vula ifayela"]
        assert lookup("af", "Save file") == ["Stoor lêer"]
        assert query(pg_connection, """SELECT block, licence, COUNT(*) FROM en.imports
            GROUP BY block, licence ORDER BY block""") == [
            ("gnome-51", "GPL-2.0-or-later", 2), ("tp", None, 1)]
        assert query(pg_connection, "SELECT name, title, version FROM en.blocks ORDER BY 1") == [
            ("gnome-51", "gnome-51", None), ("legacy-2019", "legacy-2019", None),
            ("tp", "tp", None)]
        assert schemas(pg_connection) == {"en"}

    def test_nothing_changed(self, run, pg_connection):
        run("update", "-s", "en")
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert result.output == "Nothing changed.\n"
        assert schemas(pg_connection) == {"en"}

    def test_change_rebuilds(self, run, site, pg_connection):
        root, url = site
        run("update", "-s", "en")
        write_gnome(root, "af", ("Open file", "Open lêer"))
        write_stats(root, af=2, zu=1)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Fetched gnome-51: 2 files." in result.output
        assert "Fetched tp" not in result.output
        assert lookup("af", "Open file") == ["Open lêer"]
        assert lookup("af", "Save file") == ["Stoor lêer"]
        assert schemas(pg_connection) == {"en", "_old_en"}
        # Only the last copy is kept for rollback.
        write_gnome(root, "af", ("Open file", "Lêer oopmaak"))
        write_stats(root, af=3, zu=1)
        run("update", "-s", "en")
        assert lookup("af", "Open file") == ["Lêer oopmaak"]
        assert schemas(pg_connection) == {"en", "_old_en"}
        assert run("swap_db", "-s", "en", "--rollback").exit_code == 0
        assert lookup("af", "Open file") == ["Open lêer"]

    def test_failed_import_rebuilds_next_time(self, run, site, pg_connection, monkeypatch):
        root, url = site
        run("update", "-s", "en")
        write_gnome(root, "af", ("Open file", "Open lêer"))
        write_stats(root, af=2, zu=1)
        import_files, calls = pipeline.import_files, []

        def killed_once(tasks, *args):
            calls.append(1)
            if len(calls) == 1:
                raise MemoryError
            return import_files(tasks, *args)
        monkeypatch.setattr(pipeline, "import_files", killed_once)
        assert run("update", "-s", "en").exit_code == 1
        assert lookup("af", "Open file") == ["Maak lêer oop"]
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        # tp is as the live data was built from: copied, not imported.
        assert "Copied 1 unchanged blocks" in result.output
        assert "Imported 2 files" in result.output
        assert lookup("af", "Open file") == ["Open lêer"]
        assert lookup("af", "Save file") == ["Stoor lêer"]
        assert run("update", "-s", "en").output == "Nothing changed.\n"

    def test_tasks_built_a_chunk_at_a_time(self, run, monkeypatch):
        """Each block's tasks are imported as they're built, not all first."""
        import_files, chunks = pipeline.import_files, []

        def recorded(tasks, *args):
            chunks.append(sorted({task[3] for task in tasks}))
            return import_files(tasks, *args)
        monkeypatch.setattr(pipeline, "import_files", recorded)
        monkeypatch.setattr(pipeline, "IMPORT_CHUNK", 1, raising=False)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert chunks == [["gnome-51"], ["tp"]]
        assert "Imported 3 files" in result.output

    def test_dead_worker_names_its_file(self, run, tmp_path, monkeypatch):
        def worker_killed(tasks, workers, progress):
            os.makedirs(progress)
            with open(os.path.join(progress, "123"), "w") as f:
                f.write(tasks[0][0])
            raise RuntimeError("An import worker died, perhaps for lack of memory")
        monkeypatch.setattr(pipeline, "import_files", worker_killed)
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert str(result.exception) == ("An import worker died, perhaps for lack of memory, "
                                         "while importing gnome-51/af/glib.glib-2-90.af.po")
        # Named once: the next update doesn't report it again.
        monkeypatch.undo()
        assert "stopped while importing" not in run("update", "-s", "en").output

    def test_other_runtime_error_unchanged(self, run, monkeypatch):
        def failed(*args):
            raise RuntimeError("something else")
        monkeypatch.setattr(pipeline, "import_files", failed)
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert str(result.exception) == "something else"

    def test_killed_update_reported_next_time(self, run, tmp_path):
        progress = tmp_path / "cache" / ".importing-en"
        progress.mkdir(parents=True)
        (progress / "123").write_text(str(tmp_path / "cache" / "tp" / "af.po"))
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "The last update stopped while importing tp/af.po." in result.output
        assert list(progress.iterdir()) == []

    def test_force(self, run, pg_connection):
        run("update", "-s", "en")
        result = run("update", "-s", "en", "--force")
        assert result.exit_code == 0, result.output
        assert "Imported 3 files" in result.output
        assert schemas(pg_connection) == {"en", "_old_en"}

    def test_uncompressed_cache_imports(self, run, tmp_path, pg_connection):
        """A cache fetched before files were kept compressed still imports."""
        run("update", "-s", "en")
        for packed in (tmp_path / "cache").rglob("*" + compressed.SUFFIX):
            compressed.decompress(str(packed), str(packed)[:-len(compressed.SUFFIX)])
            packed.unlink()
        result = run("update", "-s", "en", "--force")
        assert result.exit_code == 0, result.output
        assert "Imported 3 files: 3 units (0 skipped)." in result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_corrupt_cache_file_reported(self, run, tmp_path, pg_connection):
        run("update", "-s", "en")
        packed = next((tmp_path / "cache/tp").rglob("*" + compressed.SUFFIX))
        packed.write_bytes(b"not zstd")
        result = run("update", "-s", "en", "--force")
        assert result.exit_code == 0, result.output
        assert "Imported 2 files" in result.output
        assert "Couldn't read 1 files:" in result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_compression_failure_leaves_no_fetch(self, run, site, tmp_path, monkeypatch):
        root, url = site
        run("update", "-s", "en")

        def full_disk(path):
            raise OSError("No space left on device")
        monkeypatch.setattr(compressed, "compress", full_disk)
        write_gnome(root, "af", ("Open file", "Maak die lêer oop"))
        write_stats(root, af=2, zu=1)
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert not list((tmp_path / "cache").glob("*.new"))
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_ts_plural_without_forms(self, run, git_repo, tmp_path, pg_connection):  # noqa: F811 - fixture
        """Translate Toolkit can't read the target of a TS plural with no forms;
        that message is skipped, not the update."""
        repo, git = git_repo
        (repo / "ts").mkdir()
        (repo / "ts/af.ts").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS><TS version="2.1" language="af">'
            '<context><name>Main</name><message><source>Open file</source>'
            '<translation>Maak lêer oop</translation></message><message numerus="yes">'
            '<source>%n file(s)</source><translation></translation></message></context></TS>\n')
        git("add", ".")
        git("commit", "-q", "-m", "ts")
        sources = tmp_path / "git.toml"
        sources.write_text(block("x", "git", url=repo.as_uri(), files=["ts/{lang}.ts"]))
        result = run("update", "-s", "en", UPDATER_SOURCES=str(sources))
        assert result.exit_code == 0, result.output
        assert "Imported 1 files: 1 units (1 skipped)." in result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_symlink_outside_reported(self, run, git_repo, tmp_path, pg_connection):  # noqa: F811 - fixture
        repo, git = git_repo
        (tmp_path / "secret").write_text('msgid "Open file"\nmsgstr "secret"\n')
        os.symlink(str(tmp_path / "secret"), repo / "po/xh.po")
        git("add", ".")
        git("commit", "-q", "-m", "link")
        sources = tmp_path / "git.toml"
        sources.write_text(block("x", "git", url=repo.as_uri(), files=["po/{lang}.po"]))
        result = run("update", "-s", "en", UPDATER_SOURCES=str(sources))
        assert result.exit_code == 0, result.output
        assert "Symlinks leading outside x, skipped: po/xh.po" in result.output
        assert lookup("xh", "Open file") == []

    def test_snapshot_loaded_first(self, run, tmp_path, pg_connection):
        legacy = tmp_path / "legacy.po"
        legacy.write_text(po("af", ("Close file", "Maak lêer toe")))
        run("initdb", "-s", "en")
        run("build_tmdb", "-s", "en", "-t", "af", "-i", str(legacy))
        # The 2019 database stored some directory names as languages.
        with pg_connection.cursor() as cursor:
            cursor.execute("INSERT INTO en.targets (sid, text, lang) SELECT sid, 'x', 'po' "
                           "FROM en.sources")
        pg_connection.commit()
        run("snapshot_db", "-s", "en", "--name", "legacy")
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Loaded legacy-2019: 1 targets (1 skipped)." in result.output
        assert ("Not language codes in legacy-2019, skipped: po (not an ISO 639 code; "
                "1 targets)") in result.output
        assert lookup("af", "Close file") == ["Maak lêer toe"]
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_snapshot_foreign_letters(self, run, tmp_path, pg_connection):
        """The 2019 database stored Crimean Tatar and Azerbaijani as Turkish:
        a tr target with a letter Turkish never writes is left out (#3202)."""
        legacy = tmp_path / "legacy.po"
        legacy.write_text(po("tr", ("Close file", "Dosyayı kapat"), ("Next", "Soñraki"),
                             ("Add", "Əlavə Et"), ("Ñ", "Ñ")))
        run("initdb", "-s", "en")
        run("build_tmdb", "-s", "en", "-t", "tr", "-i", str(legacy))
        run("snapshot_db", "-s", "en", "--name", "legacy")
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert lookup("tr", "Close file") == ["Dosyayı kapat"]
        assert lookup("tr", "Next") == []
        assert lookup("tr", "Add") == []
        # The letter itself, which its source has too, stays.
        assert query(pg_connection, "SELECT text FROM en.targets WHERE lang = 'tr' "
                                    "AND text = 'Ñ'") == [("Ñ",)]

    def test_missing_snapshot_with_live_data(self, run, tmp_path, pg_connection):
        legacy = tmp_path / "legacy.po"
        legacy.write_text(po("af", ("Close file", "Maak lêer toe")))
        run("initdb", "-s", "en")
        run("build_tmdb", "-s", "en", "-t", "af", "-i", str(legacy))
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert ("Snapshot _legacy_en doesn't exist: run snapshot_db -s en --name legacy first."
                in result.output)
        assert schemas(pg_connection) == {"en"}
        assert lookup("af", "Close file") == ["Maak lêer toe"]

    def test_empty_live_schema(self, run, pg_connection):
        run("initdb", "-s", "en")
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_unreadable_file(self, run, site, pg_connection):
        root, url = site
        write_gnome(root, "zu", ("Open file", "Vula ifayela"),
                    extra={"broken.zu.po": 'msgid "unterminated\n'})
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Couldn't read 1 files:" in result.output
        assert "gnome-51/zu/broken.zu.po" in result.output
        assert lookup("zu", "Open file") == ["Vula ifayela"]

    def refuse(self, monkeypatch, source, error=None):
        """Make storing a unit with source fail as the database would."""
        from amagama.tmdb import TMDB
        add_list = TMDB.add_list

        def refusing(tmdb, units, *args, **kwargs):
            if any(unit["source"] == source for unit in units):
                raise error or ValueError("A string literal cannot contain NUL (0x00) characters.")
            return add_list(tmdb, units, *args, **kwargs)
        monkeypatch.setattr(TMDB, "add_list", refusing)

    def test_unstorable_file(self, run, site, pg_connection, monkeypatch):
        """A file whose units can't be stored is left out like one that
        can't be read, and the update goes on (#3848)."""
        root, url = site
        self.refuse(monkeypatch, "Delete file")
        write_gnome(root, "zu", ("Open file", "Vula ifayela"),
                    extra={"refused.zu.po": po("zu", ("Delete file", "Susa ifayela"))})
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Couldn't read 1 files:" in result.output
        assert "refused.zu.po: ValueError: A string literal cannot contain NUL" in result.output
        assert "Swapped in 'en'" in result.output
        assert lookup("zu", "Open file") == ["Vula ifayela"]
        assert lookup("zu", "Delete file") == []
        assert query(pg_connection, "SELECT count(*) FROM en.imports "
                                    "WHERE filename LIKE '%refused%'") == [(0,)]

    def test_unstorable_piece(self, run, site, pg_connection, monkeypatch):
        """A file imported in pieces that fails on a later piece leaves
        nothing of its earlier ones."""
        from amagama.updater import formats
        root, url = site
        monkeypatch.setattr(formats, "PIECE_SIZE", 40)
        self.refuse(monkeypatch, "Delete file")
        write_gnome(root, "zu", ("Open file", "Vula ifayela"), extra={
            "pieces.zu.po": po("zu", ("Rename the folder", "Qamba kabusha ifolda"),
                               ("Move the folder", "Hambisa ifolda"),
                               ("Delete file", "Susa ifayela"))})
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "pieces.zu.po: ValueError" in result.output
        assert lookup("zu", "Open file") == ["Vula ifayela"]
        assert query(pg_connection, "SELECT count(*) FROM en.targets "
                                    "WHERE text LIKE '%ifolda%'") == [(0,)]
        # Nor the English its targets were the only translations of.
        assert query(pg_connection, "SELECT count(*) FROM en.sources "
                                    "WHERE text LIKE '%the folder'") == [(0,)]

    def test_unstorable_value(self, run, site, pg_connection, monkeypatch):
        """A value PostgreSQL won't take (DataError) is left out the same way."""
        import psycopg2
        root, url = site
        self.refuse(monkeypatch, "Delete file", psycopg2.DataError("value too long"))
        write_gnome(root, "zu", ("Open file", "Vula ifayela"),
                    extra={"refused.zu.po": po("zu", ("Delete file", "Susa ifayela"))})
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "refused.zu.po: DataError: value too long" in result.output

    def test_lost_connection_stops(self, run, site, pg_connection, monkeypatch):
        """A database that goes away still stops the update, before the swap."""
        import psycopg2
        root, url = site
        self.refuse(monkeypatch, "Delete file", psycopg2.OperationalError("server closed"))
        write_gnome(root, "zu", ("Open file", "Vula ifayela"),
                    extra={"refused.zu.po": po("zu", ("Delete file", "Susa ifayela"))})
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert isinstance(result.exception, psycopg2.OperationalError)
        assert "Swapped in" not in result.output
        assert schemas(pg_connection) == {"_build_en"}

    def test_unknown_language(self, run, site, pg_connection):
        root, url = site
        write_tarball(str(root / "backups/last/x!.tgz"),
                      {"x!/bash-5.2.x!.po": po("x!", ("Save file", "?"))})
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Not language codes in tp, skipped: x! (not an ISO 639 code)" in result.output
        assert lookup("af", "Save file") == ["Stoor lêer"]

    def test_fetch_failure_leaves_live_data(self, run, site, pg_connection):
        root, url = site
        run("update", "-s", "en")
        (root / "languages/zu/gnome-51/ui.tar.gz").unlink()
        write_stats(root, af=2, zu=1)
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert "404" in result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]
        assert schemas(pg_connection) == {"en"}

    def test_failed_block_rebuilt_from_last_fetch(self, run, site, pg_connection):
        root, url = site
        run("update", "-s", "en")
        (root / "languages/zu/gnome-51/ui.tar.gz").unlink()
        write_stats(root, af=2, zu=1)
        tarball = root / "backups/last/af.tgz"
        write_tarball(str(tarball), {"af/bash-5.2.af.po": po("af", ("Save file", "Stoor die lêer"))})
        mtime = os.stat(tarball).st_mtime + 10
        os.utime(tarball, (mtime, mtime))
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert "Couldn't fetch 1 blocks:\n  gnome-51: " in result.output
        # GNOME from its last fetch; the Translation Project's change is in.
        assert lookup("af", "Open file") == ["Maak lêer oop"]
        assert lookup("af", "Save file") == ["Stoor die lêer"]
        assert schemas(pg_connection) == {"en", "_old_en"}

    def test_change_check_failure(self, run, site, pg_connection):
        """A block whose server can't say whether it changed is skipped too."""
        root, url = site
        run("update", "-s", "en")
        (root / "api/v1/releases/gnome-51/stats").unlink()
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert "Nothing changed.\nCouldn't fetch 1 blocks:\n  gnome-51: " in result.output
        assert lookup("af", "Open file") == ["Maak lêer oop"]

    def test_never_fetched_block(self, run, tmp_path, site, pg_connection):
        root, url = site
        sources = tmp_path / "sources.toml"
        sources.write_text(sources.read_text()
                           + block("gone", "translationproject", base_url=url + "/missing/"))
        result = run("update", "-s", "en")
        assert result.exit_code == 1
        assert "Nothing to import from gone: it has never been fetched." in result.output
        assert lookup("af", "Save file") == ["Stoor lêer"]
        for _ in range(3):
            result = run("update", "-s", "en")
        assert "gone (4 runs in a row, since " in result.output

    def test_workers(self, run, pg_connection):
        result = run("update", "-s", "en", UPDATER_WORKERS=2)
        assert result.exit_code == 0, result.output
        assert "Imported 3 files: 3 units (0 skipped)." in result.output
        assert lookup("zu", "Open file") == ["Vula ifayela"]


def test_import_file_unknown_language(amagama, tmp_path):
    path = tmp_path / "x.po"
    path.write_text(po("x!", ("Save file", "?")))
    with amagama.app_context():
        assert pipeline.import_file(str(path), "en", "x!", "tp", None) == "unknown language x!"


def block_rows(pg_connection, schema, block):
    """(source, target, lang, checks, file) of what block supplied in schema."""
    return sorted(query(pg_connection, """SELECT s.text, t.text, t.lang, t.checks, i.filename
        FROM {0}.targets t JOIN {0}.sources s ON s.sid = t.sid
        JOIN {0}.target_imports l ON l.tid = t.tid JOIN {0}.imports i ON i.iid = l.iid
        WHERE i.block = '{1}'""".format(schema, block)))


class TestCopyBlocks(object):

    def test_copied_as_imported(self, run, pg_connection):
        """A block copied from live into a new build is what importing its
        files again stored (#3599)."""
        assert run("update", "-s", "en").exit_code == 0
        live = block_rows(pg_connection, "en", "gnome-51")
        assert live
        app = amagama_server_factory()
        with app.app_context():
            app.tmdb.use_schemas(BUILD_PREFIX)
            app.tmdb.drop_db(["en"])
            app.tmdb.init_db(["en"])
            copied = app.tmdb.copy_blocks("en", ["gnome-51"])
        assert copied[0] == len({row[4] for row in live})
        assert block_rows(pg_connection, "_build_en", "gnome-51") == live
        # Only what was asked for.
        assert block_rows(pg_connection, "_build_en", "tp") == []

    def test_shared_with_the_build(self, run, pg_connection):
        """A target the build has already gets the copied block's link."""
        assert run("update", "-s", "en").exit_code == 0
        app = amagama_server_factory()
        with app.app_context():
            app.tmdb.use_schemas(BUILD_PREFIX)
            app.tmdb.drop_db(["en"])
            app.tmdb.init_db(["en"])
            app.tmdb.copy_blocks("en", ["gnome-51"])
            # Again: nothing new, but each import's links are there once.
            again = app.tmdb.copy_blocks("en", ["gnome-51"])
        assert again[1:3] == (0, 0)
        assert len(block_rows(pg_connection, "_build_en", "gnome-51")) == \
            2 * len(block_rows(pg_connection, "en", "gnome-51"))


class TestUnchangedCopied(object):
    """A rebuild copies the blocks unchanged since the live data was built
    from it, and imports the others (#3599)."""

    def changed_gnome(self, root):
        write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Close file", "Maak lêer toe"))
        write_stats(root, af=2, zu=1)

    def test_unchanged_copied(self, run, site, pg_connection):
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        before = block_rows(pg_connection, "en", "tp")
        self.changed_gnome(root)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied 1 unchanged blocks from the live data: 1 files" in result.output
        assert block_rows(pg_connection, "en", "tp") == before
        assert lookup("af", "Save file") == ["Stoor lêer"]
        assert lookup("af", "Close file") == ["Maak lêer toe"]
        # The next one copies from this build too.
        write_gnome(root, "af", ("Open file", "Maak lêer oop"))
        write_stats(root, af=1, zu=1)
        result = run("update", "-s", "en")
        assert "Copied 1 unchanged blocks" in result.output, result.output
        assert lookup("af", "Save file") == ["Stoor lêer"]

    def test_forced_imports_all(self, run, site):
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        result = run("update", "-s", "en", "--force")
        assert result.exit_code == 0, result.output
        assert "Copied" not in result.output

    def test_rollback_imports_all(self, run, site):
        """After a rollback the live data isn't what the record describes."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        self.changed_gnome(root)
        assert run("update", "-s", "en").exit_code == 0
        assert run("swap_db", "-s", "en", "--rollback").exit_code == 0
        write_gnome(root, "af", ("Open file", "Maak lêer oop"))
        write_stats(root, af=1, zu=1)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Swapped in 'en'" in result.output
        assert "Copied" not in result.output
        assert "Imported 3 files" in result.output

    def test_changed_exclude_imported(self, run, site, tmp_path):
        """So is one whose exclude changed, though its files didn't."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        sources = tmp_path / "sources.toml"
        sources.write_text(sources.read_text().replace(
            'name = "tp"', 'name = "tp"\nexclude = ["*/nothing.po"]'))
        self.changed_gnome(root)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied" not in result.output

    def test_long_text_copied(self, run, site, pg_connection):
        """Live, without unique indexes, can hold a target too long for the
        build's as it is; it's copied as an import would store it."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        long = "Lêer " * 2000
        with pg_connection.cursor() as cursor:
            cursor.execute("""INSERT INTO en.targets (sid, text, lang)
                SELECT t.sid, %s, 'af' FROM en.targets t JOIN en.target_imports l
                ON l.tid = t.tid JOIN en.imports i ON i.iid = l.iid
                WHERE i.block = 'tp' LIMIT 1 RETURNING tid""", (long,))
            tid = cursor.fetchone()[0]
            cursor.execute("""INSERT INTO en.target_imports (tid, iid)
                SELECT %s, iid FROM en.imports WHERE block = 'tp' LIMIT 1""", (tid,))
        pg_connection.commit()
        self.changed_gnome(root)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        # tp's file, and its two targets: the short one and the long one.
        assert "Copied 1 unchanged blocks from the live data: 1 files, 2 targets." in result.output
        assert query(pg_connection, "SELECT count(*) FROM en.targets WHERE text = '%s'"
                     % long) == [(1,)]

    def test_unindexable_text_left_out(self, run, site, pg_connection):
        """Long text that doesn't compress enough for the index is left out of
        the copy, as an import leaves it out, instead of stopping it."""
        import random
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        letters = random.Random(1)
        noise = "".join(letters.choice("abcdefghijklmnopqrstuvwxyz ") for _ in range(9000))
        with pg_connection.cursor() as cursor:
            cursor.execute("""INSERT INTO en.targets (sid, text, lang)
                SELECT t.sid, %s, 'af' FROM en.targets t JOIN en.target_imports l
                ON l.tid = t.tid JOIN en.imports i ON i.iid = l.iid
                WHERE i.block = 'tp' LIMIT 1 RETURNING tid""", (noise,))
            tid = cursor.fetchone()[0]
            cursor.execute("""INSERT INTO en.target_imports (tid, iid)
                SELECT %s, iid FROM en.imports WHERE block = 'tp' LIMIT 1""", (tid,))
        pg_connection.commit()
        self.changed_gnome(root)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied 1 unchanged blocks" in result.output
        assert query(pg_connection, "SELECT count(*) FROM en.targets WHERE length(text) = 9000") \
            == [(0,)]
        assert lookup("af", "Save file") == ["Stoor lêer"]

    def test_changed_importer_imports_all(self, run, site, monkeypatch):
        """Other code reading or checking files may store a block differently:
        nothing is copied from data built before it."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        monkeypatch.setattr(pipeline, "importer", lambda: "another version")
        self.changed_gnome(root)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied" not in result.output

    def test_changed_max_length_imports_all(self, run, site):
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        self.changed_gnome(root)
        result = run("update", "-s", "en", MAX_LENGTH=500)
        assert result.exit_code == 0, result.output
        assert "Copied" not in result.output

    def test_changed_settings_rebuild(self, run, site, tmp_path):
        """A setting changed with no upstream change rebuilds, fetching or not."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        sources = tmp_path / "sources.toml"
        sources.write_text(sources.read_text().replace(
            'name = "tp"', 'name = "tp"\nlicence = "GPL-3.0-or-later"'))
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Swapped in 'en'" in result.output
        assert "Copied 1 unchanged blocks" in result.output

    def test_changed_settings_imported(self, run, site, tmp_path):
        """A block whose settings changed (its licence here) is imported."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        sources = tmp_path / "sources.toml"
        sources.write_text(sources.read_text().replace(
            'name = "tp"', 'name = "tp"\nlicence = "GPL-3.0-or-later"'))
        self.changed_gnome(root)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied" not in result.output


class TestCheckedOnce(object):
    """A rebuild checks only the targets it didn't copy with their check
    bits, and copies an unchanged snapshot instead of loading it (#3599)."""

    def checked_texts(self, monkeypatch):
        """The targets the checks' second pass reads, as they're read."""
        from amagama import unit_checks
        rows, seen = unit_checks._rows, []

        def spy(connection, schema, langs, styled=False, unchecked=False):
            for row in rows(connection, schema, langs, styled, unchecked):
                if unchecked:
                    seen.append(row[3])
                yield row
        monkeypatch.setattr(unit_checks, "_rows", spy)
        return seen

    def test_only_new_checked(self, run, site, pg_connection, monkeypatch):
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Close file", "Maak lêer toe"))
        write_stats(root, af=2, zu=1)
        checked = self.checked_texts(monkeypatch)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied 1 unchanged blocks" in result.output
        assert "Maak lêer toe" in checked
        assert "Stoor lêer" not in checked
        assert query(pg_connection, "SELECT to_regclass('en.checked_before') IS NULL") == [(True,)]

    def test_shifted_measures_check_again(self, run, site, monkeypatch):
        """A language whose rare scripts or usual length moved has its copied
        targets checked again: their measure bits may no longer hold."""
        from amagama import unit_checks
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Close file", "Maak lêer toe"))
        write_stats(root, af=2, zu=1)
        checked = self.checked_texts(monkeypatch)
        monkeypatch.setattr(unit_checks, "_shifted", lambda cursor, schema, checks: ["af"])
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Copied 1 unchanged blocks" in result.output
        assert "Stoor lêer" in checked

    def test_weights_check_all(self, run, site, monkeypatch):
        """Suggesting weights needs every block's failures: all are checked."""
        root, url = site
        assert run("update", "-s", "en").exit_code == 0
        write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Close file", "Maak lêer toe"))
        write_stats(root, af=2, zu=1)
        checked = self.checked_texts(monkeypatch)
        result = run("update", "-s", "en", UNIT_CHECKS_SUGGEST_WEIGHTS=True)
        assert result.exit_code == 0, result.output
        assert "Copied 1 unchanged blocks" in result.output
        assert checked == []

    def test_snapshot_copied(self, run, tmp_path, site, pg_connection):
        root, url = site
        legacy = tmp_path / "legacy.po"
        legacy.write_text(po("af", ("Close the window", "Maak die venster toe")))
        run("initdb", "-s", "en")
        run("build_tmdb", "-s", "en", "-t", "af", "-i", str(legacy))
        run("snapshot_db", "-s", "en", "--name", "legacy")
        assert "Loaded legacy-2019" in run("update", "-s", "en").output
        write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Close file", "Maak lêer toe"))
        write_stats(root, af=2, zu=1)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Loaded legacy-2019" not in result.output
        assert "Copied 2 unchanged blocks" in result.output
        assert lookup("af", "Close the window") == ["Maak die venster toe"]


    def test_snapshot_made_again_loaded(self, run, tmp_path, site, pg_connection):
        """A snapshot made again, even with as many rows, is loaded again."""
        root, url = site
        legacy = tmp_path / "legacy.po"
        legacy.write_text(po("af", ("Close the window", "Maak die venster toe")))
        run("initdb", "-s", "en")
        run("build_tmdb", "-s", "en", "-t", "af", "-i", str(legacy))
        run("snapshot_db", "-s", "en", "--name", "legacy")
        assert run("update", "-s", "en").exit_code == 0
        with pg_connection.cursor() as cursor:
            cursor.execute("ALTER SCHEMA _legacy_en RENAME TO _legacy_en_was")
            cursor.execute("CREATE SCHEMA _legacy_en")
            for table in ("sources", "targets"):
                cursor.execute("CREATE TABLE _legacy_en.%s AS TABLE _legacy_en_was.%s"
                               % (table, table))
            cursor.execute("UPDATE _legacy_en.targets SET text = 'Sluit die venster'")
        pg_connection.commit()
        write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Close file", "Maak lêer toe"))
        write_stats(root, af=2, zu=1)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Loaded legacy-2019" in result.output
        assert lookup("af", "Close the window") == ["Sluit die venster"]
