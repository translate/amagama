#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Public web query for the amaGama translation memory server"""

import datetime

from flask import Blueprint, render_template


web_ui = Blueprint('web_ui', __name__, static_folder='static')


@web_ui.route('/', methods=('GET', ))
def translate():
    """Serve the query web page."""
    return render_template("translate.html", year=datetime.date.today().year)
