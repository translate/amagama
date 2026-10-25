#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest


# fixtures:
# from amagama.tests.conftest import amagama

class TestURLs(object):

    def test_index_disabled(self, amagama):
        # web UI disabled (the default):
        client = amagama.test_client()
        response = client.get('/')
        assert response.status_code == 404

    def test_index_enabled(self, amagama):
        # Blueprints must be registered before the app handles its first
        # request, so this needs its own app instance/client rather than
        # reusing one that has already served a request.
        from amagama.views import web
        amagama.register_blueprint(web.web_ui, url_prefix='')
        client = amagama.test_client()
        response = client.get('/')
        assert response.status_code == 200
        assert b"Search" in response.data

    def test_debug_json_is_indented(self, amagama):
        amagama.config['DEBUG'] = True
        response = amagama.test_client().get('/api/v1/languages/')
        assert response.status_code == 200
        assert b'\n    "' in response.data

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

    def test_limits(self, amagama):
        with amagama.app_context():
            for i in range(100):
                amagama.tmdb.add_test_unit('String number %d' % i, str(i))
        client = amagama.test_client()

        response = client.get('/tmserver/en/af/unit/?source=String%20number%201&max_candidates=100')
        assert response.status_code == 200
        data = response.json
        assert len(data) <= 50  # assuming default setting of max_candidates=30

    @pytest.mark.parametrize('tlang', ['en', 'en_US', 'en-US'])
    def test_same_language(self, amagama, tlang):
        client = amagama.test_client()
        response = client.get('/tmserver/en/%s/unit/File' % tlang)
        response_v1 = client.get('/api/v1/en/%s/unit/File' % tlang)
        assert response.status_code == 400 == response_v1.status_code

    def test_english_region(self, amagama):
        assert amagama.test_client().get('/api/v1/en/en_GB/unit/File').status_code == 200

    def test_404s(self, amagama):
        client = amagama.test_client()

        response = client.get('/tmserver/xx/en/unit/File')
        response_v1 = client.get('/api/v1/xx/en/unit/File')
        assert response.status_code == 404 == response_v1.status_code
