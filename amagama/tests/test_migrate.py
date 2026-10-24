import random
import string

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage


# The layout created by releases before per-language schemas.
OLD_LAYOUT = """
CREATE FUNCTION prepare_ortsquery(text) RETURNS text AS $$
    SELECT ARRAY_TO_STRING(
        (SELECT ARRAY_AGG(quote_literal(token)) FROM TS_PARSE('default', $1) WHERE tokid != 12),
        '|');
$$ LANGUAGE SQL;
CREATE TABLE sources_en (
    sid SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    vector TSVECTOR NOT NULL,
    length INTEGER NOT NULL
);
CREATE UNIQUE INDEX sources_en_text_unique_idx ON sources_en (text);
CREATE INDEX sources_en_text_idx ON sources_en USING gin(vector);
CREATE TABLE targets_en (
    tid SERIAL PRIMARY KEY,
    sid INTEGER NOT NULL,
    text TEXT NOT NULL,
    lang VARCHAR(32) NOT NULL,
    FOREIGN KEY (sid) references sources_en(sid)
);
CREATE UNIQUE INDEX targets_en_unique_idx ON targets_en (sid, text, lang);
"""

# What deploy_db did to that layout.
OLD_DEPLOY = """
ALTER TABLE sources_en DROP CONSTRAINT sources_en_pkey CASCADE;
ALTER TABLE targets_en DROP COLUMN tid;
DROP INDEX sources_en_text_unique_idx;
DROP INDEX targets_en_unique_idx;
CREATE INDEX targets_en_sid_lang_idx ON targets_en (sid, lang);
"""

UNITS = [
    ('File', 'af', 'Lêer'),
    ('File', 'fr', 'Fichier'),
    ('Open file', 'af', 'Maak lêer oop'),
    ('Could not open the file', 'de', 'Die Datei konnte nicht geöffnet werden'),
]


@pytest.fixture
def old_database(amagama_config, pg_connection):
    """A test database in the old layout, holding UNITS."""
    with pg_connection.cursor() as cursor:
        cursor.execute(OLD_LAYOUT)
        for source, lang, target in UNITS:
            cursor.execute("""
                INSERT INTO sources_en (text, vector, length)
                VALUES (%(source)s, TO_TSVECTOR('english', %(source)s), LENGTH(%(source)s))
                ON CONFLICT (text) DO NOTHING""", {'source': source})
            cursor.execute("""
                INSERT INTO targets_en (sid, text, lang)
                SELECT sid, %(target)s, %(lang)s FROM sources_en WHERE text = %(source)s""",
                {'source': source, 'target': target, 'lang': lang})
    pg_connection.commit()
    return pg_connection


@pytest.fixture
def deployed_old_database(old_database):
    """The old layout after deploy_db, with a string too long for a btree."""
    long_text = ''.join(random.choice(string.ascii_letters) for _ in range(9000))
    with old_database.cursor() as cursor:
        cursor.execute(OLD_DEPLOY)
        cursor.execute("""
            INSERT INTO sources_en (sid, text, vector, length)
            VALUES (100, %(text)s, TO_TSVECTOR('english', %(text)s), LENGTH(%(text)s))""",
            {'text': long_text})
        cursor.execute("INSERT INTO targets_en (sid, text, lang) VALUES (100, %s, 'af')",
                       (long_text,))
    old_database.commit()
    return old_database


def run(*args, input=None):
    return CliRunner().invoke(manage, args, input=input)


def lookup(tlang, source):
    app = amagama_server_factory()
    response = app.test_client().get('/api/v1/en/%s/unit/%s' % (tlang, source))
    assert response.status_code == 200
    return [(c['source'], c['target']) for c in response.get_json()]


def count(connection, table):
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) FROM %s' % table)
        return cursor.fetchone()[0]


class TestMigrateDB(object):

    def test_unmigrated_database_has_no_languages(self, old_database):
        assert amagama_server_factory().tmdb.source_langs == set()

    def test_migrate(self, old_database):
        result = run('migrate_db')
        assert result.exit_code == 0, result.output
        assert "Copied 'en': 3 sources, 4 targets." in result.output
        assert lookup('af', 'File') == [('File', 'Lêer')]
        assert lookup('fr', 'File') == [('File', 'Fichier')]
        assert lookup('de', 'Could not open the file') == [
            ('Could not open the file', 'Die Datei konnte nicht geöffnet werden')]

    def test_old_tables_are_kept(self, old_database):
        run('migrate_db')
        assert count(old_database, 'public.sources_en') == 3
        assert count(old_database, 'public.targets_en') == 4

    def test_migrate_twice(self, old_database):
        run('migrate_db')
        result = run('migrate_db')
        assert result.exit_code == 0, result.output
        assert "Skipping 'en': already migrated." in result.output
        assert count(old_database, 'en.sources') == 3

    def test_deploy_after_migrate(self, old_database):
        run('migrate_db')
        result = run('deploy_db', input='y\n')
        assert result.exit_code == 0, result.output
        assert lookup('af', 'Open file') == [('Open file', 'Maak lêer oop')]

    def test_import_after_migrate(self, old_database):
        # New sources must get a sid after the copied ones. Imports retry on a
        # duplicate sid, which hides a collision with only a few rows.
        run('migrate_db')
        app = amagama_server_factory()
        with app.app_context():
            from translate.storage.po import pofile
            po = pofile()
            unit = po.addsourceunit('Save file')
            unit.target = 'Stoor lêer'
            assert app.tmdb.add_store(po, 'en', 'af') == 1
        with old_database.cursor() as cursor:
            cursor.execute("SELECT sid FROM en.sources WHERE text = 'Save file'")
            new_sid = cursor.fetchone()[0]
            cursor.execute("SELECT MAX(sid) FROM public.sources_en")
            assert new_sid > cursor.fetchone()[0]
        assert lookup('af', 'Save file') == [('Save file', 'Stoor lêer')]

    def test_migrate_deployed(self, deployed_old_database):
        result = run('migrate_db')
        assert result.exit_code == 0, result.output
        assert "Copied 'en': 4 sources, 5 targets." in result.output
        assert lookup('af', 'File') == [('File', 'Lêer')]

    def test_deploy_after_migrate_deployed(self, deployed_old_database):
        run('migrate_db')
        result = run('deploy_db', input='y\n')
        assert result.exit_code == 0, result.output
        assert lookup('fr', 'File') == [('File', 'Fichier')]

    def test_import_after_migrate_deployed(self, deployed_old_database):
        run('migrate_db')
        app = amagama_server_factory()
        with app.app_context():
            from translate.storage.po import pofile
            po = pofile()
            unit = po.addsourceunit('Save file')
            unit.target = 'Stoor lêer'
            assert app.tmdb.add_store(po, 'en', 'af') == 1
        with deployed_old_database.cursor() as cursor:
            cursor.execute("SELECT sid FROM en.sources WHERE text = 'Save file'")
            assert cursor.fetchone()[0] > 100

    def test_nothing_to_migrate(self, amagama_config, pg_connection):
        result = run('migrate_db')
        assert result.exit_code == 0, result.output
        assert "No old sources_<lang> tables found." in result.output
