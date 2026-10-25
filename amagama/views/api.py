#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""JSON based public APIs for the amaGama translation memory server"""

from json import dumps

from flask import Blueprint, abort, current_app, request
from werkzeug.datastructures import Headers

from amagama.tmdb import lang_to_table


# Let's encourage caching for an hour:
cache_headers = Headers()
cache_headers['Cache-Control'] = "max-age=3600, public"

# Create the blueprints.
read_api = Blueprint('read_api', __name__)
write_api = Blueprint('write_api', __name__)


@write_api.before_request
def check_languages():
    """Refuse a write whose source or target language isn't a valid code."""
    args = request.view_args or {}
    if not all(lang_to_table(args.get(k)) for k in ('slang', 'tlang')):
        abort(400)


@read_api.route('/<slang>/<tlang>/unit/', methods=('GET', ))
def translate_unit_get(slang, tlang):
    """Return the translations for the specified unit.

    This actually internally redirects to the view that handles
    /<slang>/<tlang>/unit/<path:uid> GET requests.
    """
    return get_uid_and_call(translate_unit, slang, tlang)


@write_api.route('/<slang>/<tlang>/unit/', methods=('POST', ))
def update_unit_get(slang, tlang):
    """Alter the unit on the DB using the provided data.

    This actually internally redirects to the view that handles
    /<slang>/<tlang>/unit/<path:uid> POST requests.
    """
    return get_uid_and_call(update_unit, slang, tlang)


@write_api.route('/<slang>/<tlang>/unit/', methods=('PUT', ))
def add_unit_get(slang, tlang):
    """Add the unit to the DB using the provided data.

    This actually internally redirects to the view that handles
    /<slang>/<tlang>/unit/<path:uid> PUT requests.
    """
    return get_uid_and_call(add_unit, slang, tlang)


@read_api.route('/<slang>/<tlang>/unit/<path:uid>', methods=('GET', ))
def translate_unit(slang, tlang, uid):
    """Return the translations for the provided unit."""
    min_similarity = get_int_arg(request, 'min_similarity')
    max_candidates = get_int_arg(request, 'max_candidates')
    project_style = request.args.get('style', None)

    candidates = current_app.tmdb.translate_unit(uid, slang, tlang,
                                                 project_style, min_similarity,
                                                 max_candidates)
    response = jsonwrapper(candidates)
    return current_app.response_class(response, mimetype='application/json',
                                      headers=cache_headers)


@write_api.route('/<slang>/<tlang>/unit/<path:uid>', methods=('POST', ))
def update_unit(slang, tlang, uid):
    """Update an existing unit."""
    from translate.storage import base

    # FIXME: This is exactly the same code as in the add_unit() view, which
    # only adds units, but doesn't update them.
    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.recorded_write(slang, tlang, 'unit', 1,
        lambda iid: current_app.tmdb.add_unit(unit, slang, tlang, iid=iid))
    return ""


@write_api.route('/<slang>/<tlang>/unit/<path:uid>', methods=('PUT', ))
def add_unit(slang, tlang, uid):
    """Add a new unit."""
    from translate.storage import base

    data = request.json
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.recorded_write(slang, tlang, 'unit', 1,
        lambda iid: current_app.tmdb.add_unit(unit, slang, tlang, iid=iid))
    return ""


@write_api.route('/<slang>/<tlang>/store/<path:sid>', methods=('POST', ))
def add_store(slang, tlang, sid):
    """Add unit from POST data to tmdb."""
    units = request.json
    project_style = request.args.get('style', None)
    count = current_app.tmdb.recorded_write(slang, tlang, sid, len(units),
        lambda iid: current_app.tmdb.add_list(units, slang, tlang, project_style, iid=iid))
    response = "added %d units from %s" % (count, sid)
    return response


@write_api.route('/<slang>/<tlang>/store/<path:sid>', methods=('PUT', ))
def upload_store(slang, tlang, sid):
    """Add units from the uploaded file to tmdb."""
    from io import BytesIO
    from translate.storage import factory

    data = BytesIO(request.data)
    data.name = sid
    store = factory.getobject(data)
    project_style = request.args.get('style', None)
    total = sum(1 for u in store.units if u.istranslatable())
    count = current_app.tmdb.recorded_write(slang, tlang, sid, total,
        lambda iid: current_app.tmdb.add_store(store, slang, tlang, project_style, iid=iid))
    response = "added %d units from %s" % (count, sid)
    return response


@read_api.route('/languages/', methods=('GET', ))
def get_languages():
    """Return all the available source and target languages in the database."""
    languages = current_app.tmdb.available_languages
    response = jsonwrapper(languages)
    return current_app.response_class(response, mimetype='application/json',
                                      headers=cache_headers)


@read_api.route('/sources/', methods=('GET', ))
def get_sources():
    """Return the blocks of translations in the database."""
    response = jsonwrapper({'sources': current_app.tmdb.sources})
    return current_app.response_class(response, mimetype='application/json',
                                      headers=cache_headers)


###############################################################################
# View helpers                                                                #
###############################################################################

def get_int_arg(request, arg_name):
    """Return the specified integer argument from request, or None."""
    try:
        return int(request.args.get(arg_name, ''))
    except Exception:
        return None


def get_uid_and_call(func, slang, tlang):
    """Get the uid from the query string and call the passed function.

    If the uid cannot be retrieved a HTTP 404 is sent.
    """
    uid = request.args.get('source', '')
    if uid:
        return func(slang, tlang, uid)

    abort(404)


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
