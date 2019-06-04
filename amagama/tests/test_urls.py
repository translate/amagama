import pytest
from pytest_postgresql import factories
from werkzeug.contrib.cache import SimpleCache

from translate.storage.po import pofile

from amagama.application import amagama_server_factory
from amagama import tmdb


class TempTMDB(tmdb.TMDB):
    """A TMDB with simpler connections to the DB."""
    def __init__(self, connection, *args, **kwargs):
        self._connection = connection
        super(TempTMDB, self).__init__(*args, **kwargs)

    @property
    def connection(self):
        """Simple connection instead of TMDB's pool."""
        return self._connection

    def add_test_unit(self, source, target):
        lang_config = tmdb.lang_to_config('en')
        po = pofile()
        u = po.addsourceunit(source)
        u.target = target
        self.add_store(po, 'en', 'af')
        # avoid cached language lists:
        self._available_langs = {}


pg_server= factories.postgresql_proc(port=None, logsdir='/tmp')
pg_connection = factories.postgresql('pg_server', db_name='amagama_test')


@pytest.fixture
def amagama(pg_connection):
    """Returns an amagama app already connected to a database."""
    app = amagama_server_factory()
    app.testing = True
    app.tmdb = TempTMDB(connection=pg_connection)
    app.tmdb.init_db(['en'])
    app.cache = SimpleCache()
    return app


class TestURLs(object):

    def test_languages(self, amagama):
        client = amagama.test_client()
        response = client.get('/tmserver/languages/')
        data = response.json
        assert 'sourceLanguages' in data
        assert 'targetLanguages' in data

        source_langs = data['sourceLanguages']
        assert 'en' in source_langs

        target_langs = data['targetLanguages']
        assert len(target_langs) == 0

        with amagama.app_context():
            amagama.tmdb.add_test_unit('Network', 'Netwerk')
        response = client.get('/tmserver/languages/')
        data = response.json
        target_langs = data['targetLanguages']
        assert target_langs == ['af']

    def test_translate_unit(self, amagama):
        with amagama.app_context():
            amagama.tmdb.add_test_unit('Network', 'Netwerk')
        client = amagama.test_client()

        # no response:
        response = client.get('/tmserver/en/af/unit/xyz-')
        assert response.status_code == 200
        assert len(response.json) == 0

        # some response:
        response = client.get('/tmserver/en/af/unit/Network')
        assert response.status_code == 200
        data = response.json
        assert len(data) > 0
        entry0 = data[0]
        for key in ('quality', 'source', 'target'):
            assert key in entry0
        assert float(entry0['quality']) == 100.0

    def test_404s(self, amagama):
        client = amagama.test_client()

        response = client.get('/tmserver/en/en/unit/File')
        assert response.status_code == 404

        response = client.get('/tmserver/xx/en/unit/File')
        assert response.status_code == 404
