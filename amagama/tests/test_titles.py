#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Block titles translated for /api/v1/sources/ (#3315)."""

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from amagama import titles
from amagama.application import amagama_server_factory
from amagama.cli import manage


AF = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgctxt "title with version"
msgid "{title} {version}"
msgstr "{title}-weergawe {version}"

msgid "GNOME"
msgstr "GNOOM"

msgid "Untranslated"
msgstr ""

#, fuzzy
msgid "KDE"
msgstr "KDEE"
'''

AR = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgctxt "direction"
msgid "ltr"
msgstr "rtl"

msgctxt "title with version"
msgid "{title} {version}"
msgstr "{title}"
'''

PT_BR = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "GNOME"
msgstr "GNOME-BR"
'''

SOURCES = [
    {'name': 'gnome-51', 'title': 'GNOME', 'version': '51'},
    {'name': 'kde', 'title': 'KDE', 'version': None},
]


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    for lang, text in (('af', AF), ('ar', AR), ('pt_BR', PT_BR)):
        (tmp_path / ('%s.po' % lang)).write_text(text, encoding='utf-8')
    (tmp_path / 'sources.pot').write_text('', encoding='utf-8')
    monkeypatch.setattr(titles, 'CATALOG_DIR', str(tmp_path))
    titles.catalogs.cache_clear()
    yield
    titles.catalogs.cache_clear()


def titled(lang):
    return [(s['title'], s['displayTitle']) for s in titles.localise(SOURCES, lang)[1]]


class TestLocalise(object):

    def test_english(self, catalogs):
        assert titles.localise(SOURCES, 'en')[0] == 'ltr'
        assert titled('en') == [('GNOME', 'GNOME 51'), ('KDE', 'KDE')]

    def test_translated(self, catalogs):
        # A fuzzy translation isn't used.
        assert titled('af') == [('GNOOM', 'GNOOM-weergawe 51'), ('KDE', 'KDE')]

    def test_pattern_missing_a_placeholder(self, catalogs):
        assert titles.localise(SOURCES, 'ar') == ('rtl', [
            dict(SOURCES[0], displayTitle='GNOME 51'), dict(SOURCES[1], displayTitle='KDE')])

    def test_unchanged_input(self, catalogs):
        titles.localise(SOURCES, 'af')
        assert SOURCES[0]['title'] == 'GNOME'


class TestNegotiate(object):

    @pytest.fixture
    def accept(self):
        from werkzeug.datastructures import LanguageAccept
        from werkzeug.http import parse_accept_header
        return lambda value: parse_accept_header(value, LanguageAccept)

    @pytest.mark.parametrize('lang, header, expected', [
        ('af', '', 'af'),
        ('pt-br', '', 'pt_BR'),
        ('xx', 'ar', 'ar'),
        (None, 'ar;q=0.5, af', 'af'),
        (None, 'pt-BR', 'pt_BR'),
        (None, 'de, fr', 'en'),
        (None, '', 'en'),
    ])
    def test_negotiate(self, catalogs, accept, lang, header, expected):
        assert titles.negotiate(lang, accept(header)) == expected


class TestAPI(object):

    @pytest.fixture
    def client(self, amagama_config, tmp_path, catalogs):
        po = tmp_path / 'af-gnome.po'
        po.write_text('msgid "Open file"\nmsgstr "Maak lêer oop"\n', encoding='utf-8')
        runner = CliRunner()
        assert runner.invoke(manage, ('initdb', '-s', 'en')).exit_code == 0
        result = runner.invoke(manage, ('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(po),
                                        '--block', 'gnome-51'))
        assert result.exit_code == 0, result.output
        app = amagama_server_factory()
        with app.app_context():
            app.tmdb.record_blocks('en', [('gnome-51', 'GNOME', '51')])
        with app.test_client() as client:
            yield client

    def test_accept_language(self, client):
        response = client.get('/api/v1/sources/', headers={'Accept-Language': 'af'})
        assert response.headers['Vary'] == 'Accept-Language'
        data = response.get_json()
        assert (data['lang'], data['dir']) == ('af', 'ltr')
        [source] = data['sources']
        assert (source['name'], source['title'], source['version'], source['displayTitle']) == \
            ('gnome-51', 'GNOOM', '51', 'GNOOM-weergawe 51')

    def test_lang_argument(self, client):
        data = client.get('/api/v1/sources/?lang=ar',
                          headers={'Accept-Language': 'af'}).get_json()
        assert (data['lang'], data['dir']) == ('ar', 'rtl')
        assert data['sources'][0]['displayTitle'] == 'GNOME 51'

    def test_default_english(self, client):
        data = client.get('/api/v1/sources/').get_json()
        assert (data['lang'], data['dir']) == ('en', 'ltr')
        assert data['sources'][0]['displayTitle'] == 'GNOME 51'


class TestTemplate(object):

    def test_is_current(self):
        result = CliRunner().invoke(manage, ('sources_pot', '--check'))
        assert result.exit_code == 0, result.output

    def test_check_finds_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(titles, 'TEMPLATE', str(tmp_path / 'sources.pot'))
        result = CliRunner().invoke(manage, ('sources_pot', '--check'))
        assert result.exit_code == 1
        assert 'is out of date: run amagama-manage sources_pot.' in result.output
        assert CliRunner().invoke(manage, ('sources_pot',)).exit_code == 0
        assert CliRunner().invoke(manage, ('sources_pot', '--check')).exit_code == 0

    def test_shipped_catalogs_parse(self):
        titles.catalogs.cache_clear()
        titles.catalogs()

    def test_contents(self):
        text = titles.template([SimpleNamespace(title=title) for title in ('GNOME', 'GNOME', 'KDE')])
        assert 'POT-Creation-Date' not in text
        assert text.count('msgid "GNOME"') == 1
        assert text.index('msgctxt "title with version"') < text.index('msgid "GNOME"') \
            < text.index('msgid "KDE"')
