#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""A translation memory server using tmdb for storage, communicates
with clients using JSON over HTTP."""

import logging

from flask import Flask
from flask_caching import Cache

from amagama import poparser_compat  # noqa: F401 - patches the PO parser on import
from amagama import tmdb
from amagama.views import api

# The placeholder in settings.py.
DEFAULT_SECRET_KEY = "foobar"


class AmagamaServer(Flask):
    def __init__(self, settings, *args, **kwargs):
        super(AmagamaServer, self).__init__(*args, **kwargs)
        self.config.from_pyfile(settings)
        self.config.from_envvar('AMAGAMA_CONFIG', silent=True)
        if self.config.get('SECRET_KEY') == DEFAULT_SECRET_KEY:
            logging.warning("SECRET_KEY is the placeholder from settings.py; "
                            "set a long random one in your AMAGAMA_CONFIG file.")
        # tmdb.py caches source-id lookups on current_app.cache; without
        # this, every write-API request (add_unit/add_store/upload_store/
        # etc.) crashes with AttributeError. Defaults to a simple
        # per-process cache; set CACHE_TYPE (and friends) to use a shared
        # backend like Redis across multiple worker processes.
        self.config.setdefault('CACHE_TYPE', 'SimpleCache')
        self.cache = Cache(self)
        self.tmdb = tmdb.TMDB(self)


def amagama_server_factory():
    app = AmagamaServer("settings.py", __name__)
    app.register_blueprint(api.read_api, url_prefix='/tmserver',
                           name='read_api_tmserver')
    app.register_blueprint(api.read_api, url_prefix='/api/v1',
                           name='read_api_v1')

    if app.config['ENABLE_DATA_ALTERING_API']:
        app.register_blueprint(api.write_api, url_prefix='/tmserver',
                               name='write_api_tmserver')
        app.register_blueprint(api.write_api, url_prefix='/api/v1',
                               name='write_api_v1')

    if app.config['ENABLE_WEB_UI']:
        from amagama.views import web
        app.register_blueprint(web.web_ui, url_prefix='')

    return app
