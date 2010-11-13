#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010 Zuza Software Foundation
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

"""Module to provide a translation memory database."""

import math
import re

from flask import current_app

from translate.lang import data, factory as lang_factory
from translate.search.lshtein import LevenshteinComparer

from amagama.postgres import PostGres

def lang_to_table(code):
    # normalize to simplest form
    result = data.simplify_to_common(code)
    if data.langcode_ire.match(result):
        # normalize to legal table name
        return result.replace("-", "_").replace("@", "_").lower()
    # illegal language name
    return None

code_config_map = {
    'da': 'danish',
    'nl': 'dutch',
    'en': 'english',
    'fi': 'finnish',
    'fr': 'french',
    'de': 'german',
    'hu': 'hungarian',
    'it': 'italian',
    'nb': 'norwegian',
    'nn': 'norwegian',
    'no': 'norwegian',
    'pt': 'portuguese',
    'pt_BR': 'portuguese',
    'ro': 'romanian',
    'ru': 'russian',
    'es': 'spanish',
    'sv': 'swedish',
    'tr': 'turkish',
    }

def lang_to_config(code):
    return code_config_map.get(code, 'simple')

_nonword_re = re.compile(r"[^\w' ]+", re.UNICODE)

class TMDB(PostGres):
    INIT_SOURCE = """
CREATE TABLE sources_%(slang)s (
    sid SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    vector TSVECTOR NOT NULL,
    hash VARCHAR(32) NOT NULL,
    length INTEGER NOT NULL,
    UNIQUE(hash)
);
CREATE INDEX sources_%(slang)s_length_idx ON sources_%(slang)s (length);
CREATE INDEX sources_%(slang)s_text_idx ON sources_%(slang)s USING gin(vector);
"""
    INIT_TARGET = """
CREATE TABLE targets_%(slang)s (
    tid SERIAL PRIMARY KEY,
    sid INTEGER NOT NULL,
    text TEXT NOT NULL,
    hash VARCHAR(32) NOT NULL,
    lang VARCHAR(32) NOT NULL,
    FOREIGN KEY (sid) references sources_%(slang)s(sid),
    UNIQUE(sid, lang, hash)
);
CREATE INDEX targets_%(slang)s_sid_idx ON targets_%(slang)s (sid);
CREATE INDEX targets_%(slang)s_lang_idx ON targets_%(slang)s (lang);
"""

    def init_db(self, source_langs):
        cursor = self.get_cursor()
        for slang in source_langs:
            slang = lang_to_table(slang)
            if not self.table_exists('sources_%s' % slang):
                query = self.INIT_SOURCE % {'slang': slang}
                cursor.execute(query)
            if not self.table_exists('targets_%s' % slang):
                query = self.INIT_TARGET % {'slang': slang}
                cursor.execute(query)
        cursor.connection.commit()

    def add_unit(self, unit, source_lang, target_lang, commit=True, cursor=None):
        """inserts unit in the database"""
        #TODO: is that really the best way to handle unspecified
        # source and target languages? what about conflicts between
        # unit attributes and passed arguments
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)

        source = unicode(unit.source)
        unitdict = {'source': source,
                    'length': len(source),
                    'target': unicode(unit.target),
                    'source_lang': slang,
                    'target_lang': tlang,
                    'lang_config': lang_config,
                   }

        self.add_dict(unitdict, commit)

    def add_dict(self, unit, commit=True, cursor=None):
        """inserts units represented as dictionaries in database"""
        slang = unit['source_lang']
        try:
            if cursor is None:
                cursor = self.get_cursor()
            query = """SELECT sid FROM sources_%s WHERE hash=MD5(%%(source)s)""" % slang
            cursor.execute(query, unit)
            result = cursor.fetchone()
            if result:
                unit['sid'] = result['sid']
            else:
                query = """INSERT INTO sources_%s (text, vector, hash, length) VALUES(
                %%(source)s, TO_TSVECTOR(%%(lang_config)s, %%(source)s), MD5(%%(source)s), %%(length)s)
                RETURNING sid""" % slang
                cursor.execute(query, unit)
                unit['sid'] = cursor.fetchone()['sid']

            query = """SELECT COUNT(*) FROM targets_%s WHERE
            sid=%%(sid)s AND lang=%%(target_lang)s AND hash=MD5(%%(target)s)""" % slang
            cursor.execute(query, unit)
            if not cursor.fetchone()[0]:
                query = """INSERT INTO targets_%s (sid, text, hash, lang) VALUES (
                %%(sid)s, %%(target)s, MD5(%%(target)s), %%(target_lang)s)""" % slang
                cursor.execute(query, unit)

            if commit:
                self.connection.commit()
        except:
            self.connection.rollback()
            raise

    def add_store(self, store, source_lang, target_lang, commit=True):
        """insert all units in store in database"""
        cursor = self.get_cursor()
        count = 0
        for unit in store.units:
            if unit.istranslatable() and unit.istranslated():
                self.add_unit(unit, source_lang, target_lang, commit=False, cursor=cursor)
                count += 1
        if commit:
            self.connection.commit()
        return count

    def add_list(self, units, source_lang, target_lang, commit=True):
        """insert all units in list into the database, units are
        represented as dictionaries"""
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)

        count = 0
        try:
            cursor = self.get_cursor()
            for unit in units:
                unit['source_lang'] = slang
                unit['target_lang'] = tlang
                unit['lang_config'] = lang_config
                unit['length'] = len(unit['source'])
                self.add_dict(unit, commit=False, cursor=cursor)
                count += 1
            if commit:
                self.connection.commit()
        except:
            self.connection.rollback()
            raise

        return count

    def get_comparer(self):
        if not hasattr(self, '_comparer'):
            max_length = current_app.config.get('MAX_LENGTH', 1000)
            self._comparer = LevenshteinComparer(max_length)
        return self._comparer
    comparer = property(get_comparer)

    def translate_unit(self, unit_source, source_lang, target_lang, min_similarity=None, max_candidates=None):
        """return TM suggestions for unit_source"""
        if isinstance(unit_source, str):
            unit_source = unicode(unit_source, "utf-8")
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)
        langmodel = lang_factory.getlanguage(source_lang)

        max_length = current_app.config.get('MAX_LENGTH', 1000)
        min_similarity = max(min_similarity or current_app.config.get('MIN_SIMILARITY', 70), 30)
        max_candidates = max_candidates or current_app.config.get('MAX_CANDIDATES', 5)

        minlen = min_levenshtein_length(len(unit_source), min_similarity)
        maxlen = max_levenshtein_length(len(unit_source), min_similarity, max_length)

        minrank = max(min_similarity / 2, 30)

        search_str = u' | '.join(langmodel.words(_nonword_re.sub(u"", unit_source)))
        cursor = self.get_cursor()
        query = """
SELECT * from (SELECT s.text AS source, t.text AS target, TS_RANK(s.vector, query, 32) * 1744.93406073519 AS rank
    FROM sources_%s s JOIN targets_%s t ON s.sid = t.sid,
    TO_TSQUERY(%%(lang_config)s, %%(search_str)s) query
    WHERE t.lang = %%(tlang)s AND s.length BETWEEN %%(minlen)s AND %%(maxlen)s
    AND s.vector @@ query) sub WHERE rank > %%(minrank)s
    ORDER BY rank DESC
""" % (slang, slang)
        cursor.execute(query, {'search_str': search_str, 'source': unit_source,
                               'tlang': tlang, 'lang_config': lang_config,
                               'minrank': minrank, 'minlen': minlen, 'maxlen': maxlen})
        results = []
        for row in cursor:
            result = {}
            result['source'] = row['source']
            result['target'] = row['target']
            result['rank'] = row['rank']
            result['quality'] = self.comparer.similarity(unit_source, result['source'], min_similarity)
            if result['quality'] >= min_similarity:
                results.append(result)
        results.sort(key=lambda match: match['quality'], reverse=True)
        results = results[:max_candidates]
        return results


def min_levenshtein_length(length, min_similarity):
    return math.ceil(max(length * (min_similarity/100.0), 2))

def max_levenshtein_length(length, min_similarity, max_length):
    return math.floor(min(length / (min_similarity/100.0), max_length))
