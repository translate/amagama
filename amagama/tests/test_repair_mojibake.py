import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage


def mangle(text, codec='latin-1'):
    out = []
    for byte in text.encode('utf-8'):
        try:
            out.append(bytes([byte]).decode(codec))
        except UnicodeDecodeError:
            out.append(chr(byte))
    return ''.join(out)


# (source, lang, target) as stored, mangled where the import went wrong.
ROWS = [
    ('File', 'af', 'Lêer'),
    ('File', 'af', mangle('Lêer')),
    ('Create file', 'fr', mangle('Fichier créé')),
    ("Don't", 'af', mangle('Moenie’t', 'cp1252')),
    ('Age', 'fr', 'âge'),
    ('Mixed', 'af', mangle('Lêer') + ' ạ'),
    (mangle('Café'), 'af', 'Kafee'),
    ('Résumé', 'af', 'Opsomming'),
    (mangle('Résumé'), 'af', 'Opsomming'),
    (mangle('Résumé'), 'fr', 'CV'),
]


def run(*args, input=None):
    return CliRunner().invoke(manage, args, input=input)


@pytest.fixture
def mangled_database(amagama_config, pg_connection):
    assert run('initdb', '-s', 'en').exit_code == 0
    with pg_connection.cursor() as cursor:
        for source, lang, target in ROWS:
            cursor.execute("""
                INSERT INTO en.sources (text, vector, length)
                VALUES (%(s)s, TO_TSVECTOR('english', %(s)s), LENGTH(%(s)s))
                ON CONFLICT (text) DO NOTHING""", {'s': source})
            cursor.execute("""
                INSERT INTO en.targets (sid, text, lang)
                SELECT sid, %s, %s FROM en.sources WHERE text = %s""",
                (target, lang, source))
    pg_connection.commit()
    return pg_connection


def targets(connection, source):
    with connection.cursor() as cursor:
        cursor.execute("""SELECT t.lang, t.text FROM en.targets t
            JOIN en.sources s ON s.sid = t.sid WHERE s.text = %s
            ORDER BY t.lang, t.text""", (source,))
        return cursor.fetchall()


def lookup(tlang, source):
    app = amagama_server_factory()
    response = app.test_client().get('/api/v1/en/%s/unit/%s' % (tlang, source))
    assert response.status_code == 200
    return [(c['source'], c['target']) for c in response.get_json()]


class TestRepairMojibake(object):

    def test_dry_run(self, mangled_database):
        result = run('repair_mojibake')
        assert result.exit_code == 0, result.output
        assert "Would fix 'en': 2 sources, 3 targets." in result.output
        assert "Not converted (targets)" in result.output
        assert "Nothing was changed." in result.output
        assert sorted(targets(mangled_database, 'File')) == sorted(
            [('af', 'Lêer'), ('af', mangle('Lêer'))])

    def test_apply(self, mangled_database):
        result = run('repair_mojibake', '--apply')
        assert result.exit_code == 0, result.output
        assert "Fixed 'en': 2 sources, 3 targets." in result.output
        # A broken copy beside a correct one is dropped.
        assert targets(mangled_database, 'File') == [('af', 'Lêer')]
        assert targets(mangled_database, 'Create file') == [('fr', 'Fichier créé')]
        assert targets(mangled_database, "Don't") == [('af', 'Moenie’t')]
        assert targets(mangled_database, 'Age') == [('fr', 'âge')]
        assert targets(mangled_database, 'Mixed') == [('af', mangle('Lêer') + ' ạ')]

    def test_apply_sources(self, mangled_database):
        run('repair_mojibake', '--apply')
        assert targets(mangled_database, 'Café') == [('af', 'Kafee')]
        assert lookup('af', 'Café') == [('Café', 'Kafee')]
        # A broken source whose fixed text exists merges into it.
        assert targets(mangled_database, 'Résumé') == [('af', 'Opsomming'), ('fr', 'CV')]
        assert targets(mangled_database, mangle('Résumé')) == []

    def test_apply_twice(self, mangled_database):
        run('repair_mojibake', '--apply')
        result = run('repair_mojibake', '--apply')
        assert "Fixed 'en': 0 sources, 0 targets." in result.output

    def test_apply_deployed(self, mangled_database):
        assert run('deploy_db', input='y\n').exit_code == 0
        result = run('repair_mojibake', '--apply')
        assert result.exit_code == 0, result.output
        assert targets(mangled_database, 'File') == [('af', 'Lêer')]
        assert lookup('fr', 'Create file') == [('Create file', 'Fichier créé')]
