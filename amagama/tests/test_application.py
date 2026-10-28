#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import hashlib
import logging
import os

from flask import url_for

from amagama.application import amagama_server_factory


def test_warns_about_the_default_secret_key(amagama_config, caplog):
    with caplog.at_level(logging.WARNING):
        amagama_server_factory()
    assert any('SECRET_KEY' in r.getMessage() for r in caplog.records)


def test_no_warning_with_a_real_secret_key(amagama_config, caplog):
    amagama_config(SECRET_KEY='a long random string for this test')
    with caplog.at_level(logging.WARNING):
        amagama_server_factory()
    assert not any('SECRET_KEY' in r.getMessage() for r in caplog.records)


def test_warns_about_a_high_max_length(amagama_config, caplog):
    amagama_config(MAX_LENGTH=5000)
    with caplog.at_level(logging.WARNING):
        amagama_server_factory()
    assert any('MAX_LENGTH' in r.getMessage() for r in caplog.records)


def test_web_ui(amagama_config):
    amagama_config(ENABLE_WEB_UI=True)
    response = amagama_server_factory().test_client().get('/')
    assert response.status_code == 200
    # The data sources dialog fills its list from /api/v1/sources/, keeping
    # the general description when there's none.
    page = response.get_data(as_text=True)
    assert 'MGM.sources.init();' in page
    assert 'id="js-sources-results"' in page
    assert 'id="js-sources-fallback"' in page
    # Each suggestion's sources, titled from the same list.
    assert '<th>Sources</th>' in page
    # A filter for each language list.
    assert 'data-language-list="#js-source-language"' in page
    assert 'data-language-list="#js-target-language"' in page


def test_web_ui_address_and_year(amagama_config):
    amagama_config(ENABLE_WEB_UI=True)
    client = amagama_server_factory().test_client()
    page = client.get('/', base_url='https://amagama.example.org').get_data(as_text=True)
    assert '<title>amagama.example.org - Translation memory lookup</title>' in page
    assert '</span> amagama.example.org</h1>' in page
    assert '<li>%d © Translate House</li>' % datetime.date.today().year in page
    assert 'locamotion' not in page


def test_web_ui_favicon(amagama_config):
    amagama_config(ENABLE_WEB_UI=True)
    client = amagama_server_factory().test_client()
    page = client.get('/').get_data(as_text=True)
    assert '<link rel="icon" href="/static/favicon.ico?v=' in page
    response = client.get('/static/favicon.ico')
    assert response.status_code == 200
    assert response.data[:4] == b"\x00\x00\x01\x00"


def test_web_ui_static_versions(amagama_config):
    amagama_config(ENABLE_WEB_UI=True)
    app = amagama_server_factory()
    client = app.test_client()
    page = client.get('/').get_data(as_text=True)
    with open(os.path.join(app.static_folder, 'js', 'amagama.js'), 'rb') as f:
        version = hashlib.sha256(f.read()).hexdigest()[:12]
    # A changed file gets a new address, so browsers don't keep the old one.
    assert '<script src="/static/js/amagama.js?v=%s"></script>' % version in page
    assert client.get('/static/js/amagama.js?v=%s' % version).status_code == 200
    with app.test_request_context():
        assert url_for('web_ui.static', filename='missing.js') == '/static/missing.js'
        assert url_for('web_ui.translate') == '/'
