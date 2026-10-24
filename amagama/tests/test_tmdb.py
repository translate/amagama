import pytest

from amagama import tmdb
# fixtures:
# from amagama.tests.conftest import amagama


class TestTMDB(object):

    def test_initdb(self, amagama):
        with amagama.app_context():
            amagama.tmdb.init_db(['en'])
        with amagama.app_context():
            amagama.tmdb.init_db(['fr'])
        with amagama.app_context():
            amagama.tmdb.init_db(['en', 'de'])
        assert sorted(amagama.tmdb.source_langs) == ['de', 'en', 'fr']

    def test_dropdb(self, amagama):
        with amagama.app_context():
            amagama.tmdb.init_db(['en'])
            amagama.tmdb.drop_db(['en'])
        assert sorted(amagama.tmdb.source_langs) == []

    def test_add_store(self, amagama):
        with amagama.app_context():
            # Empty store:
            amagama.tmdb.add_test_unit('Network', 'Netwerk', "en", "en")
            targets = amagama.tmdb.available_languages['targetLanguages']
            assert targets == []

    def test_add_empty_store(self, amagama):
        from translate.storage.po import pofile
        with amagama.app_context():
            assert amagama.tmdb.add_store(pofile(), 'en', 'af') == 0

    def test_get_sid_not_cached(self, amagama):
        with amagama.app_context():
            amagama.tmdb.add_test_unit('Network', 'Netwerk')
            amagama.cache.clear()
            cursor = amagama.tmdb.get_cursor(amagama.tmdb.schema('en'))
            unit = {'source': 'Network', 'source_lang': 'en'}
            sid = amagama.tmdb.get_sid(unit, cursor)
            key = tmdb.build_cache_key('Network', amagama.tmdb.schema('en'))
            assert amagama.cache.get(key) == sid

    def test_get_sid_missing(self, amagama):
        with amagama.app_context():
            cursor = amagama.tmdb.get_cursor(amagama.tmdb.schema('en'))
            with pytest.raises(Exception, match="sid not found"):
                amagama.tmdb.get_sid({'source': 'Network', 'source_lang': 'en'}, cursor)

    def test_translate_bytes(self, amagama):
        with amagama.app_context():
            amagama.tmdb.add_test_unit('Café', 'Kafee')
            result0 = amagama.tmdb.translate_unit('Café'.encode('utf-8'), 'en', 'af')[0]
            assert result0['target'] == 'Kafee'

    def test_translate_unit(self, amagama):
        with amagama.app_context():
            amagama.tmdb.add_test_unit('Network', 'Netwerk')
            result0 = amagama.tmdb.translate_unit("Network", "en", "af")[0]
            assert result0["source"] == "Network"
            assert result0["target"] == "Netwerk"
            assert result0["quality"] == 100

            result0 = amagama.tmdb.translate_unit("Network:", "en", "af")[0]
            assert result0["source"] == "Network"
            assert result0["quality"] < 100

            result0 = amagama.tmdb.translate_unit("Net_work", "en", "af", project_style="gnome")[0]
            assert result0["source"] == "Network"
            assert result0["quality"] < 100

            # Was giving traceback psycopg2.ProgrammingError:
            amagama.tmdb.translate_unit('<a "\\b">', "en", "af")

    def test_source_lang_with_region_code(self, amagama):
        with amagama.app_context():
            amagama.tmdb.init_db(['pt_BR'])
            amagama.tmdb.add_test_unit('Hello', 'Ola', 'pt_BR', 'fr')
            result0 = amagama.tmdb.translate_unit("Hello", "pt_BR", "fr")[0]
            assert result0["source"] == "Hello"
            assert result0["target"] == "Ola"
            assert result0["quality"] == 100

    @pytest.mark.parametrize('code, config', [
        ('pt_BR', 'portuguese'), ('pt_br', 'portuguese'), ('pt-BR', 'portuguese'),
        ('en', 'english'), ('en_US', 'english'), ('zu', 'simple'),
    ])
    def test_lang_to_config(self, code, config):
        # A code and its schema name give the same configuration.
        assert tmdb.lang_to_config(code) == config

    def test_drop_table(self, amagama):
        with amagama.app_context():
            cursor = amagama.tmdb.get_cursor()
            cursor.execute("CREATE TABLE scratch (id integer)")
            cursor.connection.commit()
            amagama.tmdb.drop_table('scratch')
            assert not amagama.tmdb.table_exists('scratch')

    def test_length_bounds(self):
        assert tmdb.min_levenshtein_length(100, 70) == 70
        assert tmdb.max_levenshtein_length(100, 70, 1000) == 142
        # We want these functions to return integers.
        assert type(tmdb.min_levenshtein_length(100, 70)) is int
        assert type(tmdb.max_levenshtein_length(100, 70, 1000)) is int

    @pytest.mark.xfail
    def test_ca_valencia(self, amagama):
        with amagama.app_context():
            targets = amagama.tmdb.available_languages['targetLanguages']
            assert 'ca' not in targets
            assert 'ca_valencia' not in targets
            amagama.tmdb.add_test_unit('Network', 'Netwerk', 'en', 'ca-valencia')
            targets = amagama.tmdb.available_languages['targetLanguages']
            assert 'ca' not in targets
            assert 'ca_valencia' in targets

    def test_too_long(self, amagama):
        # https://github.com/translate/amagama/issues/3184
        import base64
        from random import getrandbits
        # a long string that won't compress:
        long_str = str(base64.encodebytes(bytes(getrandbits(10000).to_bytes(13000, 'big'))))
        with amagama.app_context():
            amagama.tmdb.add_test_unit('The long one', long_str)
