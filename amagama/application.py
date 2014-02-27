#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2014 Zuza Software Foundation
#
# This file is part of amaGama.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

"""A translation memory server using tmdb for storage, communicates
with clients using JSON over HTTP."""

import logging

from flask import Flask

from amagama import webapi, tmdb


class AmagamaServer(Flask):
    def __init__(self, settings, *args, **kwargs):
        super(AmagamaServer, self).__init__(*args, **kwargs)
        self.config.from_pyfile(settings)
        self.tmdb = tmdb.TMDB(self)


def amagama_server_factory():
    app = AmagamaServer("settings.py", __name__)
    app.register_blueprint(webapi.module, url_prefix='/tmserver')
    app.secret_key = "foobar"
    try:
        import webui
        app.register_blueprint(webui.module, url_prefix='')
    except ImportError, e:
        logging.debug("The webui module could not be imported. The web interface is not enabled.")
    return app
