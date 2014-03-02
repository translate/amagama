#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2014 Zuza Software Foundation
#
# This file is part of amaGama.
#
# amaGama is free software: you can redistribute it and/or modify it under the
# terms of the GNU General Public License as published by the Free Software
# Foundation, either version 3 of the License, or (at your option) any later
# version.
#
# amaGama is distributed in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE. See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along with
# amaGama. If not, see <http://www.gnu.org/licenses/>.

"""JSON based public APIs for the translation memory server"""

from flask import Blueprint, abort, current_app, json, request
from werkzeug import Headers


dumps = json.dumps


# Let's encourage caching for an hour:
cache_headers = Headers()
cache_headers['Cache-Control'] = "max-age=3600, public"

module = Blueprint('webapi', __name__)


def jsonwrapper(data):
    callback = request.args.get('jsoncallback')
    #FIXME: put indent only if DEBUG=True
    #dump = dumps(data, indent=4)
    dump = dumps(data)
    if callback:
        return '%s(%s)' % (callback, dump)
    else:
        return dump


@module.route('/<slang>/<tlang>/unit/', methods=('GET', 'POST', 'PUT'))
def unit_dispatch_get(slang, tlang):
    uid = request.args.get('source', None)
    if uid:
        return unit_dispatch(slang, tlang, uid)
    abort(404)


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
    try:
        min_similarity = int(request.args.get('min_similarity', ''))
    except ValueError:
        min_similarity = None

    try:
        max_candidates = int(request.args.get('max_candidates', ''))
    except ValueError:
        max_candidates = None

    project_style = request.args.get('style', None)
    candidates = current_app.tmdb.translate_unit(uid, slang, tlang, project_style,
                                                 min_similarity, max_candidates)
    response = jsonwrapper(candidates)
    return current_app.response_class(response, mimetype='application/json', headers=cache_headers)


def add_unit(uid, slang, tlang):
    from translate.storage import base
    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.add_unit(unit, slang, tlang)
    return ""


def update_unit(uid, slang, tlang):
    from translate.storage import base
    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.add_unit(unit, slang, tlang)
    return ""


def upload_store(sid, slang, tlang):
    """add units from uploaded file to tmdb"""
    import StringIO
    data = StringIO.StringIO(request.data)
    data.name = sid
    from translate.storage import factory
    store = factory.getobject(data)
    project_style = request.args.get('style', None)
    count = current_app.tmdb.add_store(store, slang, tlang, project_style)
    response = "added %d units from %s" % (count, sid)
    return response


def add_store(sid, slang, tlang):
    """Add unit from POST data to tmdb."""
    units = request.JSON
    project_style = request.args.get('style', None)
    count = current_app.tmdb.add_list(units, slang, tlang, project_style)
    response = "added %d units from %s" % (count, sid)
    return response
