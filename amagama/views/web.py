#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Public web query for the amaGama translation memory server"""

import datetime
import functools
import hashlib
import os

from flask import Blueprint, current_app, make_response, render_template, request

from amagama import languagenames, webtext


web_ui = Blueprint('web_ui', __name__, static_folder='static')


@functools.lru_cache(maxsize=64)
def _file_version(path, mtime):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


@web_ui.url_defaults
def _versioned_static(endpoint, values):
    """Give each static file's URL a hash of the file, so a changed file has
    a new address and browsers don't keep the old one."""
    if endpoint != 'web_ui.static' or 'filename' not in values:
        return
    path = os.path.join(current_app.static_folder, values['filename'])
    try:
        values['v'] = _file_version(path, os.stat(path).st_mtime_ns)
    except OSError:
        pass


@web_ui.route('/', methods=('GET', ))
def translate():
    """Serve the query web page, in the language of ?lang= or Accept-Language."""
    _ = webtext.Translator(webtext.negotiate(request.args.get('lang'), request.accept_languages))
    available = current_app.tmdb.available_languages
    codes = tuple(sorted(set(available['sourceLanguages']) | set(available['targetLanguages'])))
    context, pattern = languagenames.PATTERN
    languages = {
        'source': available['sourceLanguages'],
        'target': available['targetLanguages'],
        'described': languagenames.describe_all(codes, _.lang, _.gettext(pattern, context)),
    }
    response = make_response(render_template(
        "translate.html", _=_, gettext=_.gettext, year=datetime.date.today().year,
        languages=languages))
    response.vary.add('Accept-Language')
    return response
