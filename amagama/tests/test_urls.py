
# fixtures:
# from amagama.tests.conftest import amagama

class TestURLs(object):

    def test_index(self, amagama):
        client = amagama.test_client()

        # web UI disabled:
        response = client.get('/')
        assert response.status_code == 404

        # web UI enabled:
        from amagama.views import web
        amagama.register_blueprint(web.web_ui, url_prefix='')
        response = client.get('/')
        assert response.status_code == 200
        assert b"Search" in response.data

    def test_languages(self, amagama):
        client = amagama.test_client()
        response = client.get('/tmserver/languages/')
        response_v1 = client.get('/api/v1/languages/')
        data = response.json
        assert 'sourceLanguages' in data
        assert 'targetLanguages' in data
        assert data == response_v1.json

        source_langs = data['sourceLanguages']
        assert 'en' in source_langs

        target_langs = data['targetLanguages']
        assert len(target_langs) == 0

        with amagama.app_context():
            amagama.tmdb.add_test_unit('Network', 'Netwerk')
        response = client.get('/tmserver/languages/')
        response_v1 = client.get('/api/v1/languages/')
        data = response.json
        target_langs = data['targetLanguages']
        assert target_langs == ['af']
        assert data == response_v1.json

    def test_translate_unit(self, amagama):
        with amagama.app_context():
            amagama.tmdb.add_test_unit('Network', 'Netwerk')
        client = amagama.test_client()

        # no response:
        response = client.get('/tmserver/en/af/unit/xyz-')
        response_v1 = client.get('/api/v1/en/af/unit/xyz-')
        assert response.status_code == 200 == response_v1.status_code
        assert len(response.json) == 0
        assert response.json == response_v1.json

        # some response:
        response = client.get('/tmserver/en/af/unit/Network')
        response_v1 = client.get('/api/v1/en/af/unit/Network')
        assert response.status_code == 200
        data = response.json
        assert len(data) > 0
        entry0 = data[0]
        for key in ('quality', 'source', 'target'):
            assert key in entry0
        assert float(entry0['quality']) == 100.0
        assert data == response_v1.json

        for url in ('/tmserver/en/af/unit/?source=Network',
                    '/api/v1/en/af/unit/?source=Network'):
            response = client.get(url)
            assert response.status_code == 200
            assert data == response.json

        response = client.get('/tmserver/en/af/unit/Network?jsoncallback=xyz123456')
        response_v1 = client.get('/api/v1/en/af/unit/Network?jsoncallback=xyz123456')
        assert response.status_code == 200 == response_v1.status_code
        assert b"xyz123456(" in response.data
        assert response.data == response_v1.data

    def test_404s(self, amagama):
        client = amagama.test_client()

        response = client.get('/tmserver/en/en/unit/File')
        response_v1 = client.get('/api/v1/en/en/unit/File')
        assert response.status_code == 404 == response_v1.status_code

        response = client.get('/tmserver/xx/en/unit/File')
        response_v1 = client.get('/api/v1/xx/en/unit/File')
        assert response.status_code == 404 == response_v1.status_code
