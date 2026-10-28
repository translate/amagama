#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Public web query for the amaGama translation memory server"""

import datetime

from flask import Blueprint, make_response, render_template, request

from amagama import webtext


web_ui = Blueprint('web_ui', __name__, static_folder='static')


@web_ui.route('/', methods=('GET', ))
def translate():
    """Serve the query web page, in the language of ?lang= or Accept-Language."""
    _ = webtext.Translator(webtext.negotiate(request.args.get('lang'), request.accept_languages))
    response = make_response(render_template(
        "translate.html", _=_, gettext=_.gettext, year=datetime.date.today().year))
    response.vary.add('Accept-Language')
    return response
