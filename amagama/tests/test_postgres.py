#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest

from amagama.application import amagama_server_factory
from amagama.tests.test_write_api import PO_FILE


@pytest.fixture
def app(amagama_config):
    """An app built as deployed, with the write API on and one unit."""
    amagama_config(ENABLE_DATA_ALTERING_API=True)
    app = amagama_server_factory()
    app.testing = True
    app.tmdb.init_db(['en'])
    response = app.test_client().put('/api/v1/en/af/store/x.po', data=PO_FILE)
    assert response.status_code == 200
    return app


def close_app_connections(pg_connection):
    """Have the server close every connection but the fixture's own."""
    with pg_connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = 'amagama_test' AND pid <> pg_backend_pid()")
    pg_connection.commit()


def lookup(client):
    response = client.get('/api/v1/en/af/unit/Open file')
    assert response.status_code == 200
    return [(c['source'], c['target']) for c in response.get_json()]


class TestClosedConnections(object):

    def test_lookup(self, app, pg_connection):
        client = app.test_client()
        for _ in range(2):
            close_app_connections(pg_connection)
            assert lookup(client) == [('Open file', 'Maak lêer oop')]

    def test_write(self, app, pg_connection):
        client = app.test_client()
        close_app_connections(pg_connection)
        response = client.put('/api/v1/en/af/unit/Save file',
                              json={'source': 'Save file', 'target': 'Stoor lêer'})
        assert response.status_code == 200
        response = client.get('/api/v1/en/af/unit/Save file')
        assert response.get_json()[0]['target'] == 'Stoor lêer'

    def test_replaced_connection_is_forgotten(self, app, pg_connection):
        # A new connection can get a closed one's id(); it must not inherit
        # its prepared statements or schema.
        lookup(app.test_client())
        tmdb = app.tmdb
        conn = tmdb.pool.getconn()
        assert id(conn) in tmdb._prepared_statements
        assert id(conn) in tmdb._last_schema
        close_app_connections(pg_connection)
        lookup(app.test_client())
        assert conn.closed
        assert id(conn) not in tmdb._prepared_statements
        assert id(conn) not in tmdb._last_schema
