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

"""JSON based public APIs for the translation memory server"""

import StringIO

from translate.storage import base, factory

from flask import Module, json, request, current_app

module = Module(__name__)


def jsonwrapper(data):
    callback = request.args.get('jsoncallback')
    if callback:
        return callback + '(' + json.dumps(data) + ')'
    else:
        #FIXME: put indent only if DEBUG=True
        return json.dumps(data, indent=4)


@module.route('/<slang>/<tlang>/unit/<path:uid>', methods=('GET', 'POST', 'PUT'))
def unit_dispatch(slang, tlang, uid):
    if request.method == 'GET':
        return translate_unit(uid, slang, tlang)
    elif request.method == 'POST':
        return update_unit(uid, slang, tlang)
    elif request.method == 'PUT':
        return add_unit(uid, slang, tlang)
    else:
        #FIXME: raise exception?
        pass

@module.route('/<slang>/<tlang>/store/<path:sid>', methods=('POST', 'PUT'))
def store_dispatch(slang, tlang, sid):
    if request.method == 'POST':
        return add_store(sid, slang, tlang)
    elif request.method == 'PUT':
        return upload_store(sid, slang, tlang)
    else:
        #FIXME: raise exception?
        pass

def translate_unit(uid, slang, tlang):
    candidates = current_app.tmdb.translate_unit(uid, slang, tlang)
    response = jsonwrapper(candidates)
    return current_app.response_class(response, mimetype='application/json')

def add_unit(uid, slang, tlang):
    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.add_unit(unit, slang, tlang)
    return ""

def update_unit(uid, slang, tlang):
    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.add_unit(unit, slang, tlang)
    return ""

def upload_store(sid, slang, tlang):
    """add units from uploaded file to tmdb"""
    data = StringIO.StringIO(request.data)
    data.name = sid
    store = factory.getobject(data)
    count = current_app.tmdb.add_store(store, slang, tlang)
    response = "added %d units from %s" % (count, sid)
    return response

def add_store(sid, slang, tlang):
    """Add unit from POST data to tmdb."""
    units = request.JSON
    count = current_app.tmdb.add_list(units, slang, tlang)
    response = "added %d units from %s" % (count, sid)
    return response
