"""pytest fixtures"""

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
