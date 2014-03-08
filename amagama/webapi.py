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

"""JSON based public APIs for the amaGama translation memory server"""

from json import dumps

from flask import Blueprint, abort, current_app, request
from werkzeug import Headers


# Let's encourage caching for an hour:
cache_headers = Headers()
cache_headers['Cache-Control'] = "max-age=3600, public"

module = Blueprint('webapi', __name__)


def jsonwrapper(data):
    """Do some custom actions when exporting JSON."""
    if current_app.config['DEBUG']:
        dump = dumps(data, ensure_ascii=False, sort_keys=True, indent=4)
    else:
        dump = dumps(data, ensure_ascii=False, sort_keys=True)

    # This is used by Pootle.
    callback = request.args.get('jsoncallback')
    if callback:
        return '%s(%s)' % (callback, dump)

    return dump


@module.route('/<slang>/<tlang>/unit/', methods=('GET', 'POST', 'PUT'))
def unit_dispatch_get(slang, tlang):
    uid = request.args.get('source', '')
    if uid:
        return unit_dispatch(slang, tlang, uid)
    abort(404)


@module.route('/<slang>/<tlang>/unit/<path:uid>', methods=('GET', 'POST',
                                                           'PUT'))
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
    """Return the translations for the provided unit."""
    try:
        min_similarity = int(request.args.get('min_similarity', ''))
    except ValueError:
        min_similarity = None

    try:
        max_candidates = int(request.args.get('max_candidates', ''))
    except ValueError:
        max_candidates = None

    project_style = request.args.get('style', None)
    candidates = current_app.tmdb.translate_unit(uid, slang, tlang,
                                                 project_style, min_similarity,
                                                 max_candidates)
    response = jsonwrapper(candidates)
    return current_app.response_class(response, mimetype='application/json',
                                      headers=cache_headers)


def add_unit(uid, slang, tlang):
    """Add a new unit."""
    from translate.storage import base

    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.add_unit(unit, slang, tlang)
    return ""


def update_unit(uid, slang, tlang):
    """Update an existing unit."""
    from translate.storage import base

    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.add_unit(unit, slang, tlang)
    return ""


def upload_store(sid, slang, tlang):
    """Add units from the uploaded file to tmdb."""
    import StringIO
    from translate.storage import factory

    data = StringIO.StringIO(request.data)
    data.name = sid
    store = factory.getobject(data)
    project_style = request.args.get('style', None)
    count = current_app.tmdb.add_store(store, slang, tlang, project_style)
    response = "added %d units from %s" % (count, sid)
    return response


def add_store(sid, slang, tlang):
    """Add unit from POST data to tmdb."""
    units = request.json
    project_style = request.args.get('style', None)
    count = current_app.tmdb.add_list(units, slang, tlang, project_style)
    response = "added %d units from %s" % (count, sid)
    return response
