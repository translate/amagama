#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The lookup page translated from amagama/po/web/."""

import jinja2
import pytest
from click.testing import CliRunner
from markupsafe import Markup

from amagama import webtext
from amagama.application import amagama_server_factory
from amagama.cli import manage


AF = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Search"
msgstr "Soek"

msgid "Suggestions: {count}"
msgstr "Voorstelle: {count}"

msgid "You can contact us by sending an email to {address}."
msgstr "Stuur <b>e-pos</b> na {address}."

msgid "{year} © Translate House"
msgstr "© {jaar} Translate House"

msgctxt "language with detail"
msgid "{language} ({detail})"
msgstr "{language} in {detail}"
'''

AR = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgctxt "direction"
msgid "ltr"
msgstr "rtl"
'''


@pytest.fixture
def catalogs(tmp_path, monkeypatch):
    for lang, text in (('af', AF), ('ar', AR)):
        (tmp_path / ('%s.po' % lang)).write_text(text, encoding='utf-8')
    monkeypatch.setattr(webtext, 'CATALOG_DIR', str(tmp_path))
    webtext.catalogs.cache_clear()
    yield
    webtext.catalogs.cache_clear()


class TestTranslator(object):

    def test_english(self, catalogs):
        _ = webtext.Translator('en')
        assert (_.tag, _.dir) == ('en', 'ltr')
        assert _('Search') == 'Search'

    def test_translated(self, catalogs):
        _ = webtext.Translator('af')
        assert _('Search') == 'Soek'
        assert _.gettext('Suggestions: {count}') == 'Voorstelle: {count}'

    def test_markup(self, catalogs):
        # The translation is text; a value may be markup.
        link = Markup('<a href="mailto:x@example.org">x@example.org</a>')
        assert webtext.Translator('af')(
            'You can contact us by sending an email to {address}.', address=link) == \
            'Stuur &lt;b&gt;e-pos&lt;/b&gt; na <a href="mailto:x@example.org">x@example.org</a>.'
        assert webtext.Translator('en')('{year} © Translate House', year='<2026>') == \
            '&lt;2026&gt; © Translate House'

    def test_placeholders_differ(self, catalogs):
        assert webtext.Translator('af')('{year} © Translate House', year=2026) == \
            '2026 © Translate House'

    def test_direction(self, catalogs):
        assert webtext.Translator('ar').dir == 'rtl'


class TestPage(object):

    @pytest.fixture
    def client(self, amagama_config, catalogs):
        amagama_config(ENABLE_WEB_UI=True)
        return amagama_server_factory().test_client()

    def test_accept_language(self, client):
        response = client.get('/', headers={'Accept-Language': 'af-ZA, af;q=0.9'})
        assert 'Accept-Language' in response.headers['Vary']
        page = response.get_data(as_text=True)
        assert '<html lang="af" dir="ltr">' in page
        assert '</span> Soek\n' in page
        assert '"suggestions": "Voorstelle: {count}"' in page
        assert 'MGM.lang = "af";' in page

    def test_languages(self, client):
        client.application.tmdb._available_langs = {
            'sourceLanguages': ['en'], 'targetLanguages': ['af', 'pt_br']}
        page = client.get('/', headers={'Accept-Language': 'af'}).get_data(as_text=True)
        assert 'MGM.languages = {"described": {' \
            '"af": {"name": "Afrikaans", "tag": "af"}, ' \
            '"en": {"name": "Engels", "tag": "en"}, ' \
            '"pt_br": {"name": "Portugees in Brasili\\u00eb", "tag": "pt-BR"}}, ' \
            '"source": ["en"], "target": ["af", "pt_br"]};' in page

    def test_lang_argument(self, client):
        page = client.get('/?lang=ar', headers={'Accept-Language': 'af'}).get_data(as_text=True)
        assert '<html lang="ar" dir="rtl">' in page
        assert '</span> Search\n' in page

    def test_english(self, client):
        page = client.get('/', headers={'Accept-Language': 'de'}).get_data(as_text=True)
        assert '<html lang="en" dir="ltr">' in page


class TestTemplate(object):

    def test_is_current(self):
        result = CliRunner().invoke(manage, ('web_pot', '--check'))
        assert result.exit_code == 0, result.output

    def test_check_finds_stale(self, tmp_path, monkeypatch):
        monkeypatch.setattr(webtext, 'TEMPLATE', str(tmp_path / 'web.pot'))
        result = CliRunner().invoke(manage, ('web_pot', '--check'))
        assert result.exit_code == 1
        assert 'is out of date: run amagama-manage web_pot.' in result.output
        assert CliRunner().invoke(manage, ('web_pot',)).exit_code == 0
        assert CliRunner().invoke(manage, ('web_pot', '--check')).exit_code == 0

    def test_shipped_catalogs_parse(self):
        webtext.catalogs.cache_clear()
        webtext.catalogs()

    def test_extract(self, tmp_path):
        (tmp_path / 'a.html').write_text(
            '{# Translators: A note. #}\n<p>{{ _("One {x}", x=y) }} {{ gettext("Two") }}'
            ' {{ _("Two") }}</p>\n', encoding='utf-8')
        (tmp_path / 'notes.txt').write_text('{{ _("Not a template") }}', encoding='utf-8')
        assert webtext.extract(str(tmp_path)) == [((None, 'One {x}'), 'A note.'),
                                                  ((None, 'Two'), None)]

    def test_extract_syntax_error(self, tmp_path):
        (tmp_path / 'a.html').write_text('{% set x = {# no #} 1 %}', encoding='utf-8')
        with pytest.raises(jinja2.TemplateSyntaxError):
            webtext.extract(str(tmp_path))
