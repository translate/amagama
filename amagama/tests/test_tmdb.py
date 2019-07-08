import pytest

from . import PY2

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

    def test_length_bounds(self):
        assert tmdb.min_levenshtein_length(100, 70) == 70
        assert tmdb.max_levenshtein_length(100, 70, 1000) == 142
        # We want these functions to return integers.
        assert type(tmdb.min_levenshtein_length(100, 70)) == int
        assert type(tmdb.max_levenshtein_length(100, 70, 1000)) == int

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
        if PY2:
            long_str = base64.encodestring(bytes(getrandbits(10000)))
        else:
            long_str = str(base64.encodebytes(bytes(getrandbits(10000).to_bytes(13000, 'big'))))
        with amagama.app_context():
            amagama.tmdb.add_test_unit('The long one', long_str)
