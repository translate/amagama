import hashlib

import pytest
from click.testing import CliRunner

from amagama.cli import manage


PO_FILE = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open file"
msgstr "Maak lêer oop"

msgid "Save file"
msgstr ""

#, fuzzy
msgid "Close file"
msgstr "Maak lêer toe"
'''


@pytest.fixture
def run(amagama_config):
    runner = CliRunner()

    def run(*args, input=None):
        return runner.invoke(manage, args, input=input)
    return run


@pytest.fixture
def po_dir(tmp_path):
    for lang in ('af', 'zu'):
        (tmp_path / ('%s.po' % lang)).write_text(PO_FILE, encoding='utf-8')
    return tmp_path


def imports(pg_connection, schema='en'):
    with pg_connection.cursor() as cursor:
        cursor.execute("""SELECT block, filename, checksum, lang, licence, units, skipped
                          FROM %s.imports ORDER BY lang""" % schema)
        rows = cursor.fetchall()
    pg_connection.commit()
    return rows


class TestImports(object):

    def test_records_file(self, run, po_dir, pg_connection):
        run('initdb', '-s', 'en')
        po = str(po_dir / 'af.po')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', po,
                     '--block', 'gnome-51', '--licence', 'GPL-2.0-or-later')
        assert result.exit_code == 0, result.output
        checksum = hashlib.sha256(PO_FILE.encode('utf-8')).hexdigest()
        # Untranslated and fuzzy units are skipped.
        assert imports(pg_connection) == [
            ('gnome-51', po, checksum, 'af', 'GPL-2.0-or-later', 1, 2)]

    def test_licence_is_normalised(self, run, po_dir, pg_connection):
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'),
                     '--licence', 'mpl-2.0 or gpl-2.0-or-later')
        assert result.exit_code == 0, result.output
        assert imports(pg_connection)[0][4] == 'MPL-2.0 OR GPL-2.0-or-later'

    @pytest.mark.parametrize('licence', [
        'BOLA-1.1',  # added in SPDX 3.27
        'GPL-2.0-only WITH rsync-linking-exception',
    ])
    def test_recent_licence(self, run, po_dir, licence):
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'),
                     '--licence', licence)
        assert result.exit_code == 0, result.output

    @pytest.mark.parametrize('licence', ['GPL2', 'GPL-2.0', 'GPL-2.0+', 'MIT WITH GPL-2.0-only'])
    def test_bad_licence_imports_nothing(self, run, po_dir, pg_connection, licence):
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'),
                     '--licence', licence)
        assert result.exit_code == 2
        assert "not a current SPDX licence expression" in result.output
        assert imports(pg_connection) == []

    def test_records_each_file_in_directory(self, run, po_dir, pg_connection):
        run('initdb', '-s', 'en')
        run('build_tmdb', '-s', 'en', '-i', str(po_dir))
        rows = imports(pg_connection)
        assert [(row[0], row[3], row[5]) for row in rows] == [(None, 'af', 1), (None, 'zu', 1)]

    def test_stats_summarise_blocks(self, run, po_dir):
        run('initdb', '-s', 'en')
        run('build_tmdb', '-s', 'en', '-i', str(po_dir), '--block', 'gnome-51')
        result = run('tmdb_stats')
        assert result.exit_code == 0, result.output
        assert "Imports (block, files, languages, units, skipped, last):" in result.output
        assert "gnome-51\t2\t2\t2\t4\t" in result.output

    def test_moves_with_swap(self, run, po_dir, pg_connection):
        run('initdb', '-s', 'en')
        run('initdb', '-s', 'en', '--build')
        run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'),
            '--block', 'tp-2026-10', '--build')
        assert imports(pg_connection) == []
        run('swap_db', '-s', 'en')
        assert [row[0] for row in imports(pg_connection)] == ['tp-2026-10']

    def test_schema_made_before_imports_table(self, run, po_dir, pg_connection):
        run('initdb', '-s', 'en')
        with pg_connection.cursor() as cursor:
            cursor.execute("DROP TABLE en.imports")
        pg_connection.commit()
        assert run('tmdb_stats').exit_code == 0
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'))
        assert result.exit_code == 0, result.output
        assert len(imports(pg_connection)) == 1
