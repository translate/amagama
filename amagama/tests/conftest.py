"""pytest fixtures"""

import psycopg2
import pytest

from pytest_postgresql import factories
from pytest_postgresql.janitor import DatabaseJanitor

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
        po = pofile()
        u = po.addsourceunit(source)
        u.target = target
        self.add_store(po, slang or 'en', tlang or 'af')
        # avoid cached language lists:
        self._available_langs = {}


pg_server = factories.postgresql_proc(port=None)


@pytest.fixture
def pg_connection(pg_server):
    """A psycopg2 connection to a freshly created, empty test database.

    amaGama's postgres.py is built on psycopg2, so unlike
    pytest_postgresql's own `factories.postgresql()` client fixture (which
    connects with psycopg3), this connects with psycopg2 to match.
    """
    janitor = DatabaseJanitor(
        user=pg_server.user,
        host=pg_server.host,
        port=pg_server.port,
        dbname="amagama_test",
        version=pg_server.version,
        password=pg_server.password,
    )
    with janitor:
        connection = psycopg2.connect(
            dbname="amagama_test",
            user=pg_server.user,
            password=pg_server.password,
            host=pg_server.host,
            port=pg_server.port,
        )
        yield connection
        connection.close()


@pytest.fixture
def amagama(pg_connection):
    """Returns an amagama app already connected to a database."""
    app = amagama_server_factory()
    app.testing = True
    app.tmdb = TempTMDB(connection=pg_connection, app=app)
    app.tmdb.init_db(['en'])
    from flask_caching import Cache
    cache = Cache(app, config={
        'CACHE_TYPE': 'SimpleCache',
        'CACHE_THRESHOLD': 100000,
    })
    app.cache = cache
    return app
