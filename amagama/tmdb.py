#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2009 Zuza Software Foundation
#
# This file is part of translate.
#
# translate is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# translate is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with translate; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA

"""Module to provide a translation memory database."""

import math

from flask import current_app

from translate.lang import data
from translate.search.lshtein import LevenshteinComparer

from amagama.postgres import PostGres

class TMDB(PostGres):
    INIT_SQL = """
CREATE TABLE sources (
    sid SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    vector TSVECTOR NOT NULL,
    lang VARCHAR(32) NOT NULL,
    length INTEGER NOT NULL,
    UNIQUE(lang, text)
);
CREATE INDEX sources_lang_idx ON sources (lang);
CREATE INDEX sources_length_idx ON sources (length);
CREATE INDEX sources_text_idx ON sources USING gin(vector);

CREATE TABLE targets (
       tid SERIAL PRIMARY KEY,
       sid INTEGER NOT NULL,
       text TEXT NOT NULL,
       lang VARCHAR(32) NOT NULL,
       FOREIGN KEY (sid) references sources(sid),
       UNIQUE(sid, lang, text)
);
CREATE INDEX targets_sid_idx ON targets (sid);
CREATE INDEX targets_lang_idx ON targets (lang);
"""

    def add_unit(self, unit, source_lang=None, target_lang=None, commit=True, cursor=None):
        """inserts unit in the database"""
        #TODO: is that really the best way to handle unspecified
        # source and target languages? what about conflicts between
        # unit attributes and passed arguments
        if unit.getsourcelanguage():
            source_lang = unit.getsourcelanguage()
        if unit.gettargetlanguage():
            target_lang = unit.gettargetlanguage()

        if not source_lang:
            raise ValueError("undefined source language")
        if not target_lang:
            raise ValueError("undefined target language")

        source_lang = data.normalize_code(source_lang)
        target_lang = data.normalize_code(target_lang)

        source = unicode(unit.source)
        unitdict = {'source': source,
                    'length': len(source),
                    'target': unicode(unit.target),
                    'source_lang': source_lang,
                    'target_lang': target_lang,
                   }

        self.add_dict(unitdict, commit)

    def add_dict(self, unit, commit=True, cursor=None):
        """inserts units represented as dictionaries in database"""
        try:
            if cursor is None:
                cursor = self.get_cursor()

            query = """SELECT sid FROM sources WHERE lang=%(source_lang)s and text=%(source)s"""
            cursor.execute(query, unit)
            result = cursor.fetchone()
            if result:
                unit['sid'] = result['sid']
            else:
                query = """INSERT INTO sources (text, vector, lang, length) VALUES(
                %(source)s, TO_TSVECTOR('simple', %(source)s), %(source_lang)s, %(length)s) RETURNING sid"""
                cursor.execute(query, unit)
                unit['sid'] = cursor.fetchone()['sid']

            query = """SELECT COUNT(*) FROM targets WHERE sid=%(sid)s AND lang=%(target_lang)s AND text=%(target)s"""
            cursor.execute(query, unit)
            if not cursor.fetchone()[0]:
                query = """INSERT INTO targets (sid, text, lang) VALUES (%(sid)s, %(target)s, %(target_lang)s)"""
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
        source_lang = data.normalize_code(source_lang)
        target_lang = data.normalize_code(target_lang)

        count = 0
        try:
            cursor = self.get_cursor()
            for unit in units:
                unit['source_lang'] = source_lang
                unit['target_lang'] = target_lang
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

    def translate_unit(self, unit_source, source_lang, target_lang):
        """return TM suggestions for unit_source"""
        if isinstance(unit_source, str):
            unit_source = unicode(unit_source, "utf-8")
        source_lang = data.normalize_code(source_lang)
        target_lang = data.normalize_code(target_lang)

        min_similarity = current_app.config.get('MIN_SIMILARITY', 70)
        max_length = current_app.config.get('MAX_LENGTH', 1000)
        max_candidates = current_app.config.get('MAX_CANDIDATES', 5)

        minlen = min_levenshtein_length(len(unit_source), min_similarity)
        maxlen = max_levenshtein_length(len(unit_source), min_similarity, max_length)

        cursor = self.get_cursor()
        query = """
SELECT s.text, t.text, s.lang, t.lang, TS_RANK_CD(s.vector, query) AS rank
    FROM sources s JOIN targets t ON s.sid = t.sid, PLAINTO_TSQUERY('simple', %(search_str)s) query
    WHERE s.lang = %(slang)s AND t.lang = %(tlang)s AND s.length BETWEEN %(minlen)s AND %(maxlen)s
    AND s.vector @@ query
"""
        cursor.execute(query, {'search_str': unit_source, 'slang': source_lang, 'tlang': target_lang,
                                    'minlen': minlen, 'maxlen': maxlen})
        results = []
        for row in cursor:
            result = {}
            result['source'] = row[0]
            result['target'] = row[1]
            result['context'] = row[2]
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
