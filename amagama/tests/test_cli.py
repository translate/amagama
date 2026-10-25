#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import itertools
import logging
import random
import string
import sys
import types

import pytest
from click.testing import CliRunner
from translate.storage import factory

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.updater import formats


PO_FILE = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open file"
msgstr "Maak lêer oop"
'''


@pytest.fixture
def run(amagama_config):
    """Run amagama-manage against the test database."""
    runner = CliRunner()

    def run(*args, input=None):
        return runner.invoke(manage, args, input=input)
    return run


@pytest.fixture
def po_dir(tmp_path):
    (tmp_path / 'af.po').write_text(PO_FILE, encoding='utf-8')
    return tmp_path


def lookup(source, tlang='af'):
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get('/api/v1/en/%s/unit/%s' % (tlang, source))
        return [(c['source'], c['target']) for c in response.get_json()]


NO_LANGUAGE_PO_FILE = PO_FILE.replace('Open file', 'Save file').replace(
    'Maak lêer oop', 'Stoor lêer')


def errors(caplog):
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


class TestCLI(object):

    def test_initdb(self, run):
        result = run('initdb', '-s', 'en')
        assert result.exit_code == 0, result.output
        assert "Successfully initialized the database for 'en'." in result.output

    def test_needs_initdb(self, run):
        result = run('tmdb_stats')
        assert result.exit_code == 1
        assert "No source language is configured" in result.output

    def test_build_tmdb(self, run, po_dir):
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-t', 'af',
                     '-i', str(po_dir / 'af.po'))
        assert result.exit_code == 0, result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_xliff_in_pieces(self, run, tmp_path, monkeypatch):
        monkeypatch.setattr(formats, 'XLIFF_PIECE_SIZE', 200)
        units = ''.join('<trans-unit id="%d"><source>Open file %d</source>'
                        '<target state="translated">Maak lêer %d oop</target></trans-unit>'
                        % (i, i, i) for i in range(10))
        path = tmp_path / 'corpus.xlf'
        path.write_text('<?xml version="1.0" encoding="UTF-8"?><xliff version="1.2" '
                        'xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="x" '
                        'source-language="en" target-language="af" datatype="plaintext">'
                        '<body>%s</body></file></xliff>' % units, encoding='utf-8')
        assert len(list(formats.xliff_pieces(str(path)))) > 1

        def read_whole(*args):
            raise AssertionError("read whole")
        monkeypatch.setattr(factory, 'getobject', read_whole)
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(path))
        assert result.exit_code == 0, result.output
        for i in (0, 9):
            assert ('Open file %d' % i, 'Maak lêer %d oop' % i) in lookup('Open file %d' % i)

    def test_build_tmdb_language_from_file_name(self, run, po_dir):
        # Without -t, the target language comes from the file name.
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(po_dir))
        assert result.exit_code == 0, result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_language_from_directory(self, run, tmp_path):
        (tmp_path / 'af').mkdir()
        (tmp_path / 'af' / 'messages.po').write_text(PO_FILE, encoding='utf-8')
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(tmp_path))
        assert result.exit_code == 0, result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_po_directory_is_not_a_language(self, run, tmp_path, caplog):
        (tmp_path / 'po').mkdir()
        (tmp_path / 'po' / 'messages.po').write_text(PO_FILE, encoding='utf-8')
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(tmp_path))
        assert result.exit_code == 1, result.output
        assert any("Missing source or target language" in e for e in errors(caplog))
        assert "messages.po: missing source or target language" in result.output
        assert lookup('Open file') == []

    def test_build_tmdb_skips(self, run, po_dir, caplog):
        # Templates, other file types and version control directories.
        (po_dir / 'af.pot').write_text(NO_LANGUAGE_PO_FILE, encoding='utf-8')
        (po_dir / 'notes.txt').write_text('Save file', encoding='utf-8')
        (po_dir / 'notes.xyz').write_text('Save file', encoding='utf-8')
        (po_dir / '.git').mkdir()
        (po_dir / '.git' / 'af.po').write_text(NO_LANGUAGE_PO_FILE, encoding='utf-8')
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(po_dir))
        assert result.exit_code == 0, result.output
        assert errors(caplog) == []
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]
        assert lookup('Save file') == []

    def test_build_tmdb_broken_file(self, run, po_dir, caplog):
        (po_dir / 'nl.po').write_text('msgid "Open', encoding='utf-8')
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(po_dir))
        # The other files are still imported.
        assert result.exit_code == 1, result.output
        assert any("nl.po" in e for e in errors(caplog))
        assert "Imported 1 files: 1 units (0 skipped).\nCouldn't import 1 files:\n  %s: " \
            % (po_dir / 'nl.po') in result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_read_error(self, run, po_dir, monkeypatch):
        def bad(*args):
            raise OSError("bad header")
        monkeypatch.setattr(factory, 'getobject', bad)
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'))
        assert result.exit_code == 1, result.output
        assert "af.po: OSError: bad header" in result.output

    def test_build_tmdb_missing_input(self, run, tmp_path, caplog):
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(tmp_path / 'af.po'))
        assert result.exit_code == 1, result.output
        assert any("does not exist" in e for e in errors(caplog))
        assert "af.po: does not exist" in result.output

    def test_build_tmdb_verbose(self, run, po_dir):
        run('initdb', '-s', 'en')
        po = str(po_dir / 'af.po')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', po, '--verbose')
        assert result.exit_code == 0, result.output
        assert result.output == "Importing %s\nImported 1 files: 1 units (0 skipped).\n" % po

    def test_build_tmdb_profile(self, run, po_dir, tmp_path):
        run('initdb', '-s', 'en')
        profile = tmp_path / 'build.profile'
        result = run('build_tmdb', '-s', 'en', '-t', 'af',
                     '-i', str(po_dir / 'af.po'), '-p', str(profile))
        assert result.exit_code == 0, result.output
        assert profile.read_text().startswith('events: Ticks\nsummary: ')
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_needs_input(self, run):
        result = run('build_tmdb', '-s', 'en', '-t', 'af')
        assert result.exit_code == 2
        assert "Missing option '--input'" in result.output

    def test_tmdb_stats(self, run):
        run('initdb', '-s', 'en')
        result = run('tmdb_stats')
        assert result.exit_code == 0, result.output
        assert "Source language: en" in result.output

    def test_deploy_db(self, run):
        run('initdb', '-s', 'en')
        result = run('deploy_db', input='y\n')
        assert result.exit_code == 0, result.output
        assert "Successfully altered the database for deployment." in result.output

    def test_deploy_db_long_strings(self, run, po_dir, pg_connection):
        # Longer than any btree index entry, and random so it can't compress.
        # Such strings only fit once the unique indexes are gone, as in a
        # database that was deployed before.
        long_text = ''.join(random.choice(string.ascii_letters) for _ in range(9000))
        run('initdb', '-s', 'en')
        run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir / 'af.po'))
        with pg_connection.cursor() as cursor:
            cursor.execute("""
                ALTER TABLE en.sources DROP CONSTRAINT sources_pkey CASCADE;
                DROP INDEX en.sources_text_unique_idx;
                DROP INDEX en.targets_unique_idx;""")
            cursor.execute("""
                INSERT INTO en.sources (text, vector, length)
                VALUES (%(text)s, TO_TSVECTOR('english', %(text)s), LENGTH(%(text)s))
                RETURNING sid""", {'text': long_text})
            sid = cursor.fetchone()[0]
            cursor.execute("INSERT INTO en.targets (sid, text, lang) VALUES (%s, %s, 'af')",
                           (sid, long_text))
        pg_connection.commit()
        result = run('deploy_db', input='y\n')
        assert result.exit_code == 0, result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_dropdb(self, run):
        run('initdb', '-s', 'en')
        result = run('dropdb', '-s', 'en', input='y\n')
        assert result.exit_code == 0, result.output
        assert "Successfully dropped the database for 'en'." in result.output
        assert run('tmdb_stats').exit_code == 1

    def test_dropdb_declined(self, run):
        run('initdb', '-s', 'en')
        result = run('dropdb', '-s', 'en', input='n\n')
        assert result.exit_code == 0, result.output
        assert "Successfully dropped" not in result.output
        assert run('tmdb_stats').exit_code == 0

    def test_dropdb_needs_source_language(self, run):
        result = run('dropdb')
        assert result.exit_code == 2
        assert "Missing option '--source-language'" in result.output

    def test_benchmark_tmdb(self, run, po_dir):
        run('initdb', '-s', 'en')
        po = str(po_dir / 'af.po')
        run('build_tmdb', '-s', 'en', '-t', 'af', '-i', po)
        result = run('benchmark_tmdb', '-s', 'en', '-t', 'af', '-i', po)
        assert result.exit_code == 0, result.output
        assert "Percentiles:" in result.output

    def test_benchmark_tmdb_directory(self, run, po_dir):
        (po_dir / '.git').mkdir()
        (po_dir / '.git' / 'af.po').write_text(PO_FILE, encoding='utf-8')
        run('initdb', '-s', 'en')
        result = run('benchmark_tmdb', '-s', 'en', '-t', 'af', '-i', str(po_dir))
        assert result.exit_code == 0, result.output
        assert result.output.count("Percentiles:") == 1

    @pytest.mark.parametrize('name, message', [
        ('', "Please specify a file or directory"),
        ('missing.po', "does not exist"),
        ('messages.po', "Missing source or target language"),
        ('broken.po', "Error while processing"),
    ])
    def test_benchmark_tmdb_errors(self, run, tmp_path, caplog, name, message):
        (tmp_path / 'messages.po').write_text(PO_FILE, encoding='utf-8')
        (tmp_path / 'broken.po').write_text('msgid "Open', encoding='utf-8')
        run('initdb', '-s', 'en')
        path = str(tmp_path / name) if name else ''
        result = run('benchmark_tmdb', '-s', 'en', '-i', path)
        assert result.exit_code == 0, result.output
        assert any(message in e for e in errors(caplog))

    def test_benchmark_tmdb_slow_without_numpy(self, run, po_dir, monkeypatch):
        run('initdb', '-s', 'en')
        clock = itertools.count()
        monkeypatch.setattr('amagama.benchmark.time',
                            types.SimpleNamespace(monotonic=lambda: next(clock)))
        monkeypatch.setitem(sys.modules, 'numpy', None)
        result = run('benchmark_tmdb', '-s', 'en', '-t', 'af',
                     '-i', str(po_dir / 'af.po'))
        assert result.exit_code == 0, result.output
        assert "Slow (1.000s): 'Open file'" in result.output
        assert "Install numpy for an analysis of the timings" in result.output
