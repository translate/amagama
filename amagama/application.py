#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2008-2009 Zuza Software Foundation
#
# This file is part of translate.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

"""A translation memory server using tmdb for storage, communicates
with clients using JSON over HTTP."""

from flask import Flask

from amagama import webapi, tmdb, webui

class AmagamaServer(Flask):
    def __init__(self, settings, *args, **kwargs):
        super(AmagamaServer, self).__init__(*args, **kwargs)
        self.config.from_pyfile(settings)
        self.tmdb = tmdb.TMDB(self)

def amagama_server_factory():
    app = AmagamaServer("settings.py", __name__)
    app.register_module(webapi.module, url_prefix='/tmserver')
    app.secret_key = "foobar"
    app.register_module(webui.module, url_prefix='')
    return app
