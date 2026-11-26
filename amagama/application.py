#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""A translation memory server using tmdb for storage, communicates
with clients using JSON over HTTP."""

import logging
from urllib.parse import unquote_to_bytes

from flask import Flask
from flask_caching import Cache

from amagama import poparser_compat  # noqa: F401 - patches the PO parser on import
from amagama import tmdb
from amagama.updater import languages, retirements
from amagama.views import api

# The placeholder in settings.py.
DEFAULT_SECRET_KEY = "foobar"


class AmagamaServer(Flask):
    def __init__(self, settings, *args, **kwargs):
        super(AmagamaServer, self).__init__(*args, **kwargs)
        self.url_map.converters['source'] = api.SourceConverter
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
        languages.use_retirements(retirements.load(retirements.path(self.config)))
        tmdb._table_name_cache.clear()
        self.tmdb = tmdb.TMDB(self)

    def wsgi_app(self, environ, start_response):
        unit_path_as_sent(environ)
        return super(AmagamaServer, self).wsgi_app(environ, start_response)


def unit_path_as_sent(environ):
    """Give PATH_INFO a lookup's source as the client sent it.

    nginx passes its normalised URI as PATH_INFO: decoded, with slashes
    merged and "." and ".." segments resolved, so a source such as
    "https://example.org" or "." in the path changes. The source is taken
    from REQUEST_URI instead; the path before it is normalised as nginx does.
    """
    # Like PATH_INFO, REQUEST_URI is bytes decoded as Latin-1.
    try:
        raw = environ.get('REQUEST_URI', '').encode('latin-1')
    except UnicodeEncodeError:
        return
    if not raw.startswith(b'/'):
        return
    head, sep, source = raw.split(b'?', 1)[0].partition(b'/unit/')
    if not sep:
        return
    segments = []
    for segment in unquote_to_bytes(head).decode('latin-1').split('/'):
        if segment == '..':
            segments = segments[:-1]
        elif segment not in ('', '.'):
            segments.append(segment)
    environ['PATH_INFO'] = ''.join('/' + segment for segment in segments) + \
        '/unit/' + unquote_to_bytes(source).decode('latin-1')


def amagama_server_factory():
    app = AmagamaServer("settings.py", __name__)
    app.register_blueprint(api.read_api, url_prefix='/tmserver',
                           name='read_api_tmserver')
    app.register_blueprint(api.read_api, url_prefix='/api/v1',
                           name='read_api_v1')
    app.register_blueprint(api.read_api_v2, url_prefix='/api/v2')

    if app.config['ENABLE_DATA_ALTERING_API']:
        app.register_blueprint(api.write_api, url_prefix='/tmserver',
                               name='write_api_tmserver')
        app.register_blueprint(api.write_api, url_prefix='/api/v1',
                               name='write_api_v1')

    if app.config['ENABLE_WEB_UI']:
        from amagama.views import web
        app.register_blueprint(web.web_ui, url_prefix='')

    return app
