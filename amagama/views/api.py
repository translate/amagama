#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""JSON based public APIs for the amaGama translation memory server"""

import re
from json import dumps

from flask import Blueprint, abort, current_app, request
from werkzeug.datastructures import Headers
from werkzeug.routing import PathConverter

from amagama import stats, titles
from amagama.source_options import SourceOptions
from amagama.tmdb import lang_to_table
from amagama.vendored import abstraction


# Let's encourage caching for an hour. A reply keeps the Headers it is
# given and adds its own to them, so each gets a copy.
cache_headers = Headers()
cache_headers['Cache-Control'] = "max-age=3600, public"


class SourceConverter(PathConverter):
    """A source in the path, as sent: it may start with a slash. Its rules
    don't merge slashes (see application.unit_path_as_sent)."""
    regex = '.+?'
    part_isolating = False


# Create the blueprints.
read_api = Blueprint('read_api', __name__)
write_api = Blueprint('write_api', __name__)
# API v2: lookups by a string's abstract (#3603).
read_api_v2 = Blueprint('read_api_v2', __name__)

# A JSONP callback must be a JavaScript name, like "f" or "Obj.f", so it
# can't add code of its own to the reply. ASCII only, as callbacks are.
CALLBACK_RE = re.compile(r'[A-Za-z_$][\w$]*(\.[A-Za-z_$][\w$]*)*', re.ASCII)


@write_api.before_request
def check_languages():
    """Refuse a write whose source or target language isn't a valid code."""
    args = request.view_args or {}
    if not all(lang_to_table(args.get(k)) for k in ('slang', 'tlang')):
        abort(400)


@read_api.after_request
@read_api_v2.after_request
@write_api.after_request
def set_content_type(response):
    """Don't let a browser take a reply for another type; serve JSONP as script."""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    if response.mimetype == 'application/json' and jsoncallback():
        response.mimetype = 'text/javascript'
    return response


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


@read_api_v2.route('/<slang>/<tlang>/unit/', methods=('GET', ))
def translate_abstract(slang, tlang):
    """Return the translations for an abstract (q), made by the client with
    abstraction version av: sources are scored on their abstracts. Another
    version is a 409 giving the server's, for the client to use v1."""
    query = request.args.get('q')
    if not query:
        abort(400)
    version = get_int_arg(request, 'av')
    if version is None:
        abort(400)
    if version != abstraction.ABSTRACTION_VERSION:
        return current_app.response_class(
            jsonwrapper({'error': "This server makes abstracts with another version.",
                         'version': abstraction.ABSTRACTION_VERSION}),
            status=409, mimetype='application/json')
    return _suggestions(slang, tlang, query, abstracted=True)


@read_api.route('/<slang>/<tlang>/unit/<source:uid>', methods=('GET', ),
                merge_slashes=False)
def translate_unit(slang, tlang, uid):
    """Return the translations for the provided unit."""
    return _suggestions(slang, tlang, uid)


def _suggestions(slang, tlang, query, abstracted=False):
    """The reply with the suggestions for query, by the request's options."""
    min_similarity = get_int_arg(request, 'min_similarity')
    max_candidates = get_int_arg(request, 'max_candidates')
    project_style = request.args.get('style', None)
    try:
        source_options = SourceOptions.from_args(request.args)
    except ValueError:
        abort(400)

    candidates = current_app.tmdb.translate_unit(query, slang, tlang,
                                                 project_style, min_similarity,
                                                 max_candidates, source_options,
                                                 abstracted=abstracted)
    response = jsonwrapper(candidates)
    return current_app.response_class(response, mimetype='application/json',
                                      headers=Headers(cache_headers))


@write_api.route('/<slang>/<tlang>/unit/<source:uid>', methods=('POST', ),
                merge_slashes=False)
def update_unit(slang, tlang, uid):
    """Update an existing unit."""
    from translate.storage import base

    # FIXME: This is exactly the same code as in the add_unit() view, which
    # only adds units, but doesn't update them.
    data = json_units()[0]
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.recorded_write(slang, tlang, 'unit', 1,
        lambda iid: current_app.tmdb.add_unit(unit, slang, tlang, iid=iid))
    return ""


@write_api.route('/<slang>/<tlang>/unit/<source:uid>', methods=('PUT', ),
                merge_slashes=False)
def add_unit(slang, tlang, uid):
    """Add a new unit."""
    from translate.storage import base

    data = json_units()[0]
    unit = base.TranslationUnit(data['source'])
    unit.target = data['target']
    current_app.tmdb.recorded_write(slang, tlang, 'unit', 1,
        lambda iid: current_app.tmdb.add_unit(unit, slang, tlang, iid=iid))
    return ""


@write_api.route('/<slang>/<tlang>/store/<path:sid>', methods=('POST', ))
def add_store(slang, tlang, sid):
    """Add unit from POST data to tmdb."""
    units = json_units(many=True)
    project_style = request.args.get('style', None)
    count = current_app.tmdb.recorded_write(slang, tlang, sid, len(units),
        lambda iid: current_app.tmdb.add_list(units, slang, tlang, project_style, iid=iid))
    response = "added %d units from %s" % (count, sid)
    return response


@write_api.route('/<slang>/<tlang>/store/<path:sid>', methods=('PUT', ))
def upload_store(slang, tlang, sid):
    """Add units from the uploaded file to tmdb, read as the updater reads
    the same file (its extension picks the reader)."""
    import os
    import tempfile

    from amagama.updater.formats import open_store

    name = os.path.basename(sid)
    if not name:
        abort(400)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, name)
            with open(path, "wb") as f:
                f.write(request.data)
            store = open_store(path)
        total = sum(1 for u in store.units if u.istranslatable())
    except Exception:
        # An unknown file type, or a file its reader can't parse.
        abort(400)
    project_style = request.args.get('style', None)
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
                                      headers=Headers(cache_headers))


@read_api.route('/sources/', methods=('GET', ))
def get_sources():
    """Return the blocks of translations in the database, titled in the request's language."""
    lang = titles.negotiate(request.args.get('lang'), request.accept_languages)
    direction, sources = titles.localise(current_app.tmdb.sources, lang)
    response = jsonwrapper({'lang': lang, 'dir': direction, 'sources': sources})
    headers = Headers(cache_headers)
    headers['Vary'] = 'Accept-Language'
    return current_app.response_class(response, mimetype='application/json',
                                      headers=headers)


@read_api.route('/stats/', methods=('GET', ))
def get_stats():
    """Return the statistics page's figures; 404 until they are computed."""
    figures = stats.cached(stats.path(current_app.config))
    if figures is None:
        return current_app.response_class(
            jsonwrapper({'error': "The statistics haven't been computed yet."}),
            status=404, mimetype='application/json')
    return current_app.response_class(jsonwrapper(figures), mimetype='application/json',
                                      headers=Headers(cache_headers))


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


def json_units(many=False):
    """Return the request's JSON units as a list, or send a HTTP 400.

    A unit is an object with a string source and target that the database can
    hold (JSON can give an unpaired surrogate or a NUL); with many, the body
    is a list of them, otherwise a single one.
    """
    def text(value):
        if not isinstance(value, str) or "\x00" in value:
            return False
        try:
            value.encode('utf-8')
        except UnicodeEncodeError:
            return False
        return True

    data = request.json
    units = data if many else [data]
    if not (isinstance(units, list) and
            all(isinstance(unit, dict) and
                text(unit.get('source')) and
                text(unit.get('target')) for unit in units)):
        abort(400)
    return units


def jsonwrapper(data):
    """Do some custom actions when exporting JSON."""
    if current_app.config['DEBUG']:
        dump = dumps(data, ensure_ascii=False, sort_keys=True, indent=4)
    else:
        dump = dumps(data, ensure_ascii=False, sort_keys=True)

    # This is used by Pootle.
    callback = jsoncallback()
    if callback:
        return '%s(%s)' % (callback, dump)

    return dump


def jsoncallback():
    """Return the request's JSONP callback, or None if it isn't a JavaScript name."""
    callback = request.args.get('jsoncallback', '')
    if CALLBACK_RE.fullmatch(callback):
        return callback
    return None
