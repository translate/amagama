import logging

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
