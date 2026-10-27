#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import random
import string

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage


PO_FILE = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "%s"
msgstr "%s"
'''


@pytest.fixture
def run(amagama_config):
    runner = CliRunner()

    def run(*args, input=None):
        return runner.invoke(manage, args, input=input)
    return run


@pytest.fixture
def load(run, tmp_path):
    def load(lang, source, target, *options):
        path = tmp_path / ('%s.po' % lang)
        path.write_text(PO_FILE % (source, target), encoding='utf-8')
        result = run('build_tmdb', '-s', 'en', '-t', lang, '-i', str(path), *options)
        assert result.exit_code == 0, result.output
    return load


@pytest.fixture
def live(run, load):
    """A deployed live schema, as the migrated server has."""
    run('initdb', '-s', 'en')
    load('af', 'Open file', 'Maak lêer oop')
    load('zu', 'Open file', 'Vula ifayela')
    run('deploy_db', input='y\n')


def query(pg_connection, sql):
    with pg_connection.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchall()
    pg_connection.commit()
    return rows


def lookup(client, lang, source):
    response = client.get('/api/v1/en/%s/unit/%s' % (lang, source))
    return [c['target'] for c in response.get_json()]


class TestSnapshot(object):

    def test_snapshot_is_not_served(self, run, live):
        result = run('snapshot_db', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 0, result.output
        assert "Saved 'en' as _legacy_en: 1 sources, 2 targets." in result.output
        app = amagama_server_factory()
        with app.test_client() as client:
            languages = client.get('/api/v1/languages/').get_json()
        assert languages['sourceLanguages'] == ['en']

    def test_snapshot_exists(self, run, live):
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        result = run('snapshot_db', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 1
        assert "Schema _legacy_en already exists." in result.output

    def test_snapshot_needs_live_schema(self, run):
        result = run('snapshot_db', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 1
        assert "Schema en doesn't exist." in result.output

    @pytest.mark.parametrize('name', ['build', 'old', 'Legacy', 'legacy-2019'])
    def test_snapshot_name(self, run, name):
        result = run('snapshot_db', '-s', 'en', '--name', name)
        assert result.exit_code == 2
        assert "use lower-case letters and digits" in result.output


class TestLoadSnapshot(object):

    def test_rebuild_with_snapshot(self, run, load, live, pg_connection):
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        run('initdb', '-s', 'en', '--build')
        result = run('load_snapshot', '-s', 'en', '--name', 'legacy', '--block', 'legacy-2019')
        assert result.exit_code == 0, result.output
        assert ("Loaded _legacy_en into _build_en: 1 sources (0 skipped), "
                "2 targets (0 skipped).") in result.output
        assert query(pg_connection, "SELECT sid, text FROM _build_en.sources") == \
            query(pg_connection, "SELECT sid, text FROM en.sources")
        assert query(pg_connection, """SELECT block, filename, checksum, lang, units, skipped
            FROM _build_en.imports ORDER BY lang""") == [
            ('legacy-2019', '_legacy_en', None, 'af', 1, 0),
            ('legacy-2019', '_legacy_en', None, 'zu', 1, 0)]

        # New imports add to it and dedupe against it.
        load('af', 'Open file', 'Maak lêer oop', '--build')
        load('af', 'Save file', 'Stoor lêer', '--build')
        assert query(pg_connection, "SELECT COUNT(*) FROM _build_en.targets") == [(3,)]
        run('deploy_db', '--build', input='y\n')
        run('swap_db', '-s', 'en')
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client, 'zu', 'Open file') == ['Vula ifayela']
            assert lookup(client, 'af', 'Save file') == ['Stoor lêer']

    def test_rebuild_keeps_import_order(self, run, load):
        # Imported longest first; deploy_db clusters the live schema, and so
        # the snapshot, shortest first.
        targets = ['Löschen', 'Entfernen', 'Ausradieren', 'Tilgen', 'Lösche', 'Slet', 'Del']
        run('initdb', '-s', 'en')
        for target in targets:
            load('de', 'Delete', target)
        run('deploy_db', input='y\n')
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        run('initdb', '-s', 'en', '--build')
        result = run('load_snapshot', '-s', 'en', '--name', 'legacy', '--block', 'legacy-2019')
        assert result.exit_code == 0, result.output
        load('de', 'Delete', 'Entferne', '--build')
        run('deploy_db', '--build', input='y\n')
        run('swap_db', '-s', 'en')
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client, 'de', 'Delete') == targets[:5]

    def test_skips_text_imports_refuse(self, run, live, pg_connection):
        # Random text can't compress into an index entry.
        def random_text(length):
            return ''.join(random.choice(string.ascii_letters) for _ in range(length))
        insert_source = """INSERT INTO en.sources (text, vector, length)
            VALUES (%(t)s, TO_TSVECTOR('english', %(t)s), LENGTH(%(t)s)) RETURNING sid"""
        with pg_connection.cursor() as cursor:
            # Over MAX_LENGTH: skipped with its target.
            cursor.execute(insert_source, {'t': 'Short' + ' word' * 999})
            cursor.execute("INSERT INTO en.targets (sid, text, lang) VALUES (%s, 'Kort', 'af')",
                           (cursor.fetchone()[0],))
            # Long but indexable: kept.
            cursor.execute(insert_source, {'t': random_text(1500)})
            cursor.execute("INSERT INTO en.targets (sid, text, lang) VALUES (%s, 'Lank', 'af')",
                           (cursor.fetchone()[0],))
            # Too long to index: skipped.
            cursor.execute("""INSERT INTO en.targets (sid, text, lang)
                SELECT sid, %s, 'zu' FROM en.sources WHERE text = 'Open file'""",
                           (random_text(9000),))
            # Long but compressible: kept.
            cursor.execute("""INSERT INTO en.targets (sid, text, lang)
                SELECT sid, %s, 'xh' FROM en.sources WHERE text = 'Open file'""",
                           ('Vula ' * 300,))
        pg_connection.commit()
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        run('initdb', '-s', 'en', '--build')
        result = run('load_snapshot', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 0, result.output
        assert ("Loaded _legacy_en into _build_en: 2 sources (1 skipped), "
                "4 targets (2 skipped).") in result.output
        assert query(pg_connection, """SELECT lang, units, skipped FROM _build_en.imports
            ORDER BY lang""") == [('af', 2, 1), ('xh', 1, 0), ('zu', 1, 1)]

    def test_needs_snapshot(self, run, live):
        run('initdb', '-s', 'en', '--build')
        result = run('load_snapshot', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 1
        assert "Schema _legacy_en doesn't exist." in result.output

    def test_needs_empty_build(self, run, load, live):
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        run('initdb', '-s', 'en', '--build')
        load('af', 'Save file', 'Stoor lêer', '--build')
        result = run('load_snapshot', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 1
        assert "Schema _build_en isn't empty." in result.output

    def test_not_after_deploy(self, run, live):
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        run('initdb', '-s', 'en', '--build')
        run('deploy_db', '--build', input='y\n')
        result = run('load_snapshot', '-s', 'en', '--name', 'legacy')
        assert result.exit_code == 1
        assert "Schema _build_en was deployed; load snapshots before deploy_db." in result.output
