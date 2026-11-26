#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import random

import pytest

from amagama import tmdb as tmdb_module
from amagama.application import amagama_server_factory


PO_FILE = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open file"
msgstr "Maak lêer oop"
'''.encode('utf-8')


@pytest.fixture
def client(amagama_config):
    """A client for an app built as deployed, with the write API on."""
    amagama_config(ENABLE_DATA_ALTERING_API=True)
    app = amagama_server_factory()
    app.testing = True
    app.tmdb.init_db(['en'])
    return app.test_client()


def lookup(client, source, tlang='af'):
    response = client.get('/api/v1/en/%s/unit/%s' % (tlang, source))
    assert response.status_code == 200
    return [(c['source'], c['target']) for c in response.get_json()]


def stored_sources(client):
    tmdb = client.application.tmdb
    with client.application.app_context():
        cursor = tmdb.get_cursor(tmdb.schema('en'))
        cursor.execute("SELECT text FROM sources")
        return cursor.fetchall()


class TestWriteAPI(object):

    def test_disabled_by_default(self, amagama):
        client = amagama.test_client()
        response = client.put('/api/v1/en/af/unit/File',
                              json={'source': 'File', 'target': 'Lêer'})
        assert response.status_code == 405

    @pytest.mark.parametrize('prefix', ['/tmserver', '/api/v1'])
    @pytest.mark.parametrize('method', ['put', 'post'])
    def test_unit_with_new_source(self, client, prefix, method):
        # add_unit() has to add the source string itself; nothing else has
        # put it in the sources table.
        send = getattr(client, method)
        response = send('%s/en/af/unit/Save file' % prefix,
                        json={'source': 'Save file', 'target': 'Stoor lêer'})
        assert response.status_code == 200
        assert lookup(client, 'Save file') == [('Save file', 'Stoor lêer')]

    def test_post_store(self, client):
        response = client.post('/api/v1/en/af/store/x.po', json=[
            {'source': 'Open file', 'target': 'Maak lêer oop'},
        ])
        assert response.status_code == 200
        assert response.data == b'added 1 units from x.po'
        assert lookup(client, 'Open file') == [('Open file', 'Maak lêer oop')]

    @pytest.mark.parametrize('method, path, body', [
        ('put', 'unit/File', {}),
        ('post', 'unit/File', []),
        ('put', 'unit/File', {'source': 'File', 'target': 5}),
        ('post', 'store/x.po', 5),
        ('post', 'store/x.po', {'source': 'File', 'target': 'Lêer'}),
        ('post', 'store/x.po', [{'source': 'File'}]),
        # An unpaired surrogate, which UTF-8 can't hold.
        ('put', 'unit/File', '{"source": "File", "target": "L\\ud800er"}'),
        ('post', 'store/x.po', '[{"source": "\\udc00", "target": "Leer"}]'),
        # A NUL, which psycopg2 refuses.
        ('put', 'unit/File', '{"source": "File", "target": "L\\u0000er"}'),
        ('post', 'store/x.po', '[{"source": "File\\u0000", "target": "Leer"}]'),
    ])
    def test_wrong_json(self, client, method, path, body):
        kwargs = {'data': body, 'content_type': 'application/json'} \
            if isinstance(body, str) else {'json': body}
        response = getattr(client, method)('/api/v1/en/af/' + path, **kwargs)
        assert response.status_code == 400
        assert stored_sources(client) == []

    @pytest.mark.parametrize('name, data', [
        ('x.xyz', PO_FILE),
        ('x.po', b'msgid "Open file"\nmsgstr'),
        ('x.xlf', b'<xliff'),
    ])
    def test_unreadable_store(self, client, name, data):
        response = client.put('/api/v1/en/af/store/' + name, data=data)
        assert response.status_code == 400
        assert stored_sources(client) == []

    def test_put_store(self, client):
        response = client.put('/api/v1/en/af/store/x.po', data=PO_FILE)
        assert response.status_code == 200
        assert response.data == b'added 1 units from x.po'
        assert lookup(client, 'Open file') == [('Open file', 'Maak lêer oop')]

    def test_put_store_read_as_the_updater_does(self, client):
        """An uploaded plural pairs the singular with the form for n=1, and
        the plural with the form for most counts (#3351)."""
        po = ('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
              '"Plural-Forms: nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : '
              'n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;\\n"\n\n'
              'msgid "Open %d file"\nmsgid_plural "Open %d files"\n'
              + ''.join('msgstr[%d] "<form %d>"\n' % (i, i) for i in range(6)))
        response = client.put('/api/v1/en/ar/store/a/b/ar.po', data=po.encode('utf-8'))
        assert response.status_code == 200, response.data
        assert lookup(client, 'Open %d file', 'ar') == [('Open %d file', '<form 1>'),
                                                        ('Open %d files', '<form 4>')]

    @pytest.mark.parametrize('method', ['put', 'post'])
    def test_unit_source_in_query(self, client, method):
        send = getattr(client, method)
        response = send('/api/v1/en/af/unit/?source=Save file',
                        json={'source': 'Save file', 'target': 'Stoor lêer'})
        assert response.status_code == 200
        assert lookup(client, 'Save file') == [('Save file', 'Stoor lêer')]

    @pytest.mark.parametrize('method', ['get', 'put', 'post'])
    def test_unit_source_missing_from_query(self, client, method):
        send = getattr(client, method)
        response = send('/api/v1/en/af/unit/',
                        json={'source': 'Save file', 'target': 'Stoor lêer'})
        assert response.status_code == 404

    def test_failed_unit_is_rolled_back(self, client, monkeypatch):
        def fail(*args):
            raise RuntimeError("add_dict failed")
        client.application.testing = False
        monkeypatch.setattr(client.application.tmdb, 'add_dict', fail)
        response = client.put('/api/v1/en/af/unit/Save file',
                              json={'source': 'Save file', 'target': 'Stoor lêer'})
        assert response.status_code == 500
        monkeypatch.undo()
        assert lookup(client, 'Save file') == []
        response = client.put('/api/v1/en/af/unit/Open file',
                              json={'source': 'Open file', 'target': 'Maak lêer oop'})
        assert response.status_code == 200
        assert lookup(client, 'Open file') == [('Open file', 'Maak lêer oop')]

    def test_failed_store_is_rolled_back(self, client, monkeypatch):
        # Fails once the targets are in, as their links to the import go in.
        client.application.testing = False
        execute_values = tmdb_module.execute_values

        def fail_on_links(cursor, query, *args, **kwargs):
            if 'target_imports' in query:
                raise RuntimeError("failed mid-store")
            return execute_values(cursor, query, *args, **kwargs)

        monkeypatch.setattr(tmdb_module, 'execute_values', fail_on_links)
        response = client.post('/api/v1/en/af/store/x.po', json=[
            {'source': 'Open file', 'target': 'Maak lêer oop'},
            {'source': 'Save file', 'target': 'Stoor lêer'},
        ])
        assert response.status_code == 500
        assert lookup(client, 'Open file') == []

    @pytest.mark.parametrize('pair', ['en/French', 'English/af'])
    @pytest.mark.parametrize('method, path, kwargs', [
        ('put', 'unit/Open file', {'json': {'source': 'Open file', 'target': 'Ouvrir'}}),
        ('post', 'unit/Open file', {'json': {'source': 'Open file', 'target': 'Ouvrir'}}),
        ('post', 'store/x.po', {'json': [{'source': 'Open file', 'target': 'Ouvrir'}]}),
        ('put', 'store/x.po', {'data': PO_FILE}),
    ])
    def test_invalid_language(self, client, pair, method, path, kwargs):
        response = getattr(client, method)('/api/v1/%s/%s' % (pair, path), **kwargs)
        assert response.status_code == 400
        assert stored_sources(client) == []

    def test_unit_too_long_to_index(self, client):
        # Random CJK text, so that the index's compression can't shrink it.
        random.seed(1)
        source = ''.join(chr(random.randint(0x4e00, 0x9fff)) for _ in range(6000))
        response = client.put('/api/v1/en/af/unit/', query_string={'source': source},
                              json={'source': source, 'target': 'Lêer'})
        assert response.status_code == 200
        assert stored_sources(client) == []
