"""pytest fixtures"""

import pytest

from pytest_postgresql import factories

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

    def add_test_unit(self, source, target, slang=None, tlang=None):
        lang_config = tmdb.lang_to_config('en')
        po = pofile()
        u = po.addsourceunit(source)
        u.target = target
        self.add_store(po, slang or 'en', tlang or 'af')
        # avoid cached language lists:
        self._available_langs = {}


pg_server= factories.postgresql_proc(port=None, logsdir='/tmp')
pg_connection = factories.postgresql('pg_server', db_name='amagama_test')


@pytest.fixture
def amagama(pg_connection):
    """Returns an amagama app already connected to a database."""
    app = amagama_server_factory()
    app.testing = True
    app.tmdb = TempTMDB(connection=pg_connection, app=app)
    app.tmdb.init_db(['en'])
    from flask_caching import Cache
    cache = Cache(app, config={
        'CACHE_TYPE': 'simple',
        'CACHE_THRESHOLD': 100000,
    })
    app.cache = cache
    return app
