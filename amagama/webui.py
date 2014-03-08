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

"""JSON based public APIs for the translation memory server"""

from flask import Blueprint, current_app, render_template, request
from flask.ext.wtf import Form
from wtforms import TextField
from wtforms.validators import Required


module = Blueprint('webui', __name__)


class TranslateForm(Form):
    uid = TextField('Text', validators=[Required()])


@module.route('/<slang>/<tlang>/unit', methods=('GET', 'POST'))
def translate(slang, tlang):
    form = TranslateForm()
    if form.validate_on_submit():
        uid = form.uid.data
        try:
            min_similarity = int(request.args.get('min_similarity', ''))
        except ValueError:
            min_similarity = None

        try:
            max_candidates = int(request.args.get('max_candidates', ''))
        except ValueError:
            max_candidates = None

        candidates = current_app.tmdb.translate_unit(uid, slang, tlang, min_similarity, max_candidates)
    else:
        uid = None
        candidates = None

    return render_template("translate.html", slang=slang, tlang=tlang, form=form, uid=uid, candidates=candidates)
