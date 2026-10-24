import functools
import http.server
import threading

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_updater import QuietHandler, block, write_stats, write_tarball


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
        block("legacy-2019", "snapshot", 1, snapshot="legacy")
        + block("gnome-51", "damned-lies", 2, release="gnome-51", base_url=url,
                licence="GPL-2.0-or-later")
        + block("tp", "translationproject", 3, base_url=url + "/backups/last/"))
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
        write_stats(root, af=3, zu=1)
        run("update", "-s", "en")
        assert schemas(pg_connection) == {"en", "_old_en"}
        assert run("swap_db", "-s", "en", "--rollback").exit_code == 0
        assert lookup("af", "Open file") == ["Open lêer"]

    def test_force(self, run, pg_connection):
        run("update", "-s", "en")
        result = run("update", "-s", "en", "--force")
        assert result.exit_code == 0, result.output
        assert "Imported 3 files" in result.output
        assert schemas(pg_connection) == {"en", "_old_en"}

    def test_snapshot_loaded_first(self, run, tmp_path, pg_connection):
        legacy = tmp_path / "legacy.po"
        legacy.write_text(po("af", ("Close file", "Maak lêer toe")))
        run("initdb", "-s", "en")
        run("build_tmdb", "-s", "en", "-t", "af", "-i", str(legacy))
        run("snapshot_db", "-s", "en", "--name", "legacy")
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Loaded legacy-2019: 1 targets (0 skipped)." in result.output
        assert lookup("af", "Close file") == ["Maak lêer toe"]
        assert lookup("af", "Open file") == ["Maak lêer oop"]

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

    def test_unknown_language(self, run, site, pg_connection):
        root, url = site
        write_tarball(str(root / "backups/last/x!.tgz"),
                      {"x!/bash-5.2.x!.po": po("x!", ("Save file", "?"))})
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "tp/x!/bash-5.2.x!.po: unknown language x!" in result.output
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

    def test_workers(self, run, pg_connection):
        result = run("update", "-s", "en", UPDATER_WORKERS=2)
        assert result.exit_code == 0, result.output
        assert "Imported 3 files: 3 units (0 skipped)." in result.output
        assert lookup("zu", "Open file") == ["Vula ifayela"]
