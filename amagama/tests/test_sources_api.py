#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""GET /api/v1/sources/ (#3315)."""

import re

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage


def run(*args):
    result = CliRunner().invoke(manage, args)
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def load(amagama_config, tmp_path):
    """Import one unit for lang as block, with any build_tmdb options."""
    def load(block, lang, *options):
        path = tmp_path / ('%s-%s.po' % (block, lang))
        path.write_text('msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                        'msgid "Open file"\nmsgstr "%s %s"\n' % (block, lang), encoding='utf-8')
        run('build_tmdb', '-s', 'en', '-t', lang, '-i', str(path), '--block', block, *options)
    return load


def sources():
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get('/api/v1/sources/')
        assert response.status_code == 200
        assert response.headers['Cache-Control'] == 'max-age=3600, public'
        return response.get_json()['sources']


def record_blocks(blocks):
    app = amagama_server_factory()
    with app.app_context():
        app.tmdb.record_blocks('en', blocks)


def query(pg_connection, sql):
    with pg_connection.cursor() as cursor:
        cursor.execute(sql)
    pg_connection.commit()


class TestSources(object):

    def test_blocks(self, load):
        run('initdb', '-s', 'en')
        load('gnome-51', 'zu', '--licence', 'GPL-2.0-or-later')
        load('gnome-51', 'af', '--licence', 'GPL-2.0-or-later')
        load('kde', 'af')
        record_blocks([('gnome-51', 'GNOME', '51'), ('kde', 'KDE', None)])
        listed = sources()
        for block in listed:
            assert re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', block.pop('imported'))
        assert listed == [
            {'name': 'gnome-51', 'title': 'GNOME', 'version': '51', 'displayTitle': 'GNOME 51',
             'licence': 'GPL-2.0-or-later', 'sourceLanguage': 'en',
             'targetLanguages': ['af', 'zu'], 'units': 2},
            {'name': 'kde', 'title': 'KDE', 'version': None, 'displayTitle': 'KDE', 'licence': None,
             'sourceLanguage': 'en', 'targetLanguages': ['af'], 'units': 1}]

    def test_untitled_block_shows_its_name(self, load):
        run('initdb', '-s', 'en')
        load('debian', 'af')
        [block] = sources()
        assert (block['name'], block['title'], block['version']) == ('debian', 'debian', None)

    def test_mixed_licences_are_no_licence(self, load):
        run('initdb', '-s', 'en')
        load('mixed', 'af', '--licence', 'MIT')
        load('mixed', 'zu', '--licence', 'GPL-3.0-only')
        [block] = sources()
        assert block['licence'] is None

    def test_import_without_block_isnt_listed(self, amagama_config, tmp_path):
        run('initdb', '-s', 'en')
        path = tmp_path / 'af.po'
        path.write_text('msgid "Open file"\nmsgstr "Maak lêer oop"\n', encoding='utf-8')
        run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(path))
        assert sources() == []

    def test_retitled_block(self, load):
        run('initdb', '-s', 'en')
        load('kde', 'af')
        record_blocks([('kde', 'KDE', None)])
        record_blocks([('kde', 'KDE Plasma', '6')])
        [block] = sources()
        assert (block['title'], block['version']) == ('KDE Plasma', '6')

    def test_schema_without_blocks_table(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('kde', 'af')
        query(pg_connection, "DROP TABLE en.blocks")
        [block] = sources()
        assert block['title'] == 'kde'

    def test_schema_without_imports_table(self, load, pg_connection):
        run('initdb', '-s', 'en')
        query(pg_connection, "DROP TABLE en.imports")
        assert sources() == []

    def test_no_database(self, amagama_config):
        assert sources() == []
