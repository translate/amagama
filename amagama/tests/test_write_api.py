import pytest

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


def lookup(client, source):
    response = client.get('/api/v1/en/af/unit/%s' % source)
    assert response.status_code == 200
    return [(c['source'], c['target']) for c in response.get_json()]


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

    def test_put_store(self, client):
        response = client.put('/api/v1/en/af/store/x.po', data=PO_FILE)
        assert response.status_code == 200
        assert response.data == b'added 1 units from x.po'
        assert lookup(client, 'Open file') == [('Open file', 'Maak lêer oop')]

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

    def test_failed_store_is_rolled_back(self, client):
        client.application.testing = False
        response = client.post('/api/v1/en/af/store/x.po', json=[
            {'source': 'Open file', 'target': 'Maak lêer oop'},
            {'source': 'Save file'},
        ])
        assert response.status_code == 500
        assert lookup(client, 'Open file') == []
