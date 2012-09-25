#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2010-2011 Zuza Software Foundation
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

from flask import current_app, abort

from translate.lang import data
from translate.search.lshtein import LevenshteinComparer

from amagama.postgres import PostGres


_table_name_cache = {}

def lang_to_table(code):
    if code in _table_name_cache:
        return _table_name_cache[code]
    # normalize to simplest form
    result = data.simplify_to_common(code)
    if data.langcode_ire.match(result):
        # normalize to legal table name
        table_name = result.replace("-", "_").replace("@", "_").lower()
        _table_name_cache[code] = table_name
        return table_name
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


def build_cache_key(text, code):
    """Build a simple string to use as cache key.

    For now this is not usable with memcached."""
    return "%s\n%s" % (code, text)


def split_cache_key(key):
    """Give the source string inside the given composite cache key."""
    return key.split('\n', 1)[1]


class TMDB(PostGres):
    # array_agg() is only avaiable since Postgres 8.4, so we provide it if it
    # doesn't exist. This is from http://wiki.postgresql.org/wiki/Array_agg
    ARRAY_AGG_CODE = """
CREATE AGGREGATE array_agg(anyelement) (
SFUNC=array_append,
STYPE=anyarray,
INITCOND='{}'
);
"""
    INIT_FUNCTIONS = """
CREATE FUNCTION prepare_ortsquery(text) RETURNS text AS $$
    SELECT ARRAY_TO_STRING((SELECT ARRAY_AGG(quote_literal(token)) FROM TS_PARSE('default', $1) WHERE tokid != 12), '|');
$$ LANGUAGE SQL;
"""

    INIT_SOURCE = """
CREATE TABLE sources_%(slang)s (
    sid SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    vector TSVECTOR NOT NULL,
    length INTEGER NOT NULL
);
CREATE UNIQUE INDEX sources_%(slang)s_text_unique_idx ON sources_%(slang)s (text);
CREATE INDEX sources_%(slang)s_text_idx ON sources_%(slang)s USING gin(vector);
"""
    INIT_TARGET = """
CREATE TABLE targets_%(slang)s (
    tid SERIAL PRIMARY KEY,
    sid INTEGER NOT NULL,
    text TEXT NOT NULL,
    lang VARCHAR(32) NOT NULL,
    FOREIGN KEY (sid) references sources_%(slang)s(sid)
);
CREATE UNIQUE INDEX targets_%(slang)s_unique_idx ON targets_%(slang)s (sid, text, lang);
"""

    DEPLOY_QUERY = """
ALTER TABLE sources_%(slang)s DROP CONSTRAINT sources_%(slang)s_pkey CASCADE;
ALTER TABLE targets_%(slang)s DROP COLUMN tid;
DROP INDEX sources_%(slang)s_text_unique_idx;
DROP INDEX targets_%(slang)s_unique_idx;
CREATE INDEX targets_%(slang)s_sid_lang_idx ON targets_%(slang)s (sid, text);
"""

    def __init__(self, *args, **kwargs):
        super(TMDB, self).__init__(*args, **kwargs)
        # initialize list of source languages
        query = "SELECT relname FROM pg_class WHERE relkind='r' AND relname LIKE 'sources_%'"
        cursor = self.get_cursor()
        cursor.execute(query)
        self.source_langs = set()
        offset = len('sources_')
        for row in cursor:
            self.source_langs.add(row['relname'][offset:])

    def init_db(self, source_langs):
        cursor = self.get_cursor()
        if not self.function_exists('array_agg'):
            cursor.execute(self.ARRAY_AGG_CODE)
        if not self.function_exists('prepare_ortsquery'):
            cursor.execute(self.INIT_FUNCTIONS)

        for slang in source_langs:
            slang = lang_to_table(slang)
            if slang in self.source_langs:
                continue

            if not self.table_exists('sources_%s' % slang):
                query = self.INIT_SOURCE % {'slang': slang}
                cursor.execute(query)
            if not self.table_exists('targets_%s' % slang):
                query = self.INIT_TARGET % {'slang': slang}
                cursor.execute(query)
        cursor.connection.commit()

    def get_sid(self, unit_dict, cursor):
        source = unit_dict['source']
        slang = unit_dict['source_lang']
        key = build_cache_key(source, slang)
        sid = current_app.cache.get(key)
        #TODO: when using memcached, check that we got the right one since
        # collisions on key names are possible
        if sid:
            return sid

        query = """SELECT sid FROM sources_%s WHERE text=%%(source)s""" % slang
        cursor.execute(query, unit_dict)
        result = cursor.fetchone()
        if result:
            sid = result['sid']
            current_app.cache.set(key, sid)
            return sid
        raise Exception("sid not found although it should have existed")

    def add_unit(self, unit, source_lang, target_lang, commit=True, cursor=None):
        """inserts unit in the database"""
        #TODO: is that really the best way to handle unspecified
        # source and target languages? what about conflicts between
        # unit attributes and passed arguments
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)

        try:
            if cursor is None:
                cursor = self.get_cursor()

            source = unicode(unit.source)
            unitdict = {'source': source,
                        'length': len(source),
                        'target': unicode(unit.target),
                        'source_lang': slang,
                        'target_lang': tlang,
                        'lang_config': lang_config,
                       }

            self.add_dict(unitdict, cursor)

            if commit:
                self.connection.commit()
        except:
            self.connection.rollback()
            raise

    def add_dict(self, unit, cursor):
        """Inserts units represented as dictionaries in database.

        The caller is expected to handle errors.
        """
        slang = unit['source_lang']
        unit['sid'] = self.get_sid(unit, cursor)
        query = """SELECT COUNT(*) FROM targets_%s WHERE
        sid=%%(sid)s AND lang=%%(target_lang)s AND text=%%(target)s""" % slang
        cursor.execute(query, unit)
        if not cursor.fetchone()[0]:
            query = """INSERT INTO targets_%s (sid, text, lang) VALUES (
            %%(sid)s, %%(target)s, %%(target_lang)s)""" % slang
            cursor.execute(query, unit)

    def get_all_sids(self, units, source_lang):
        """Ensures that all source strings are in the database+cache."""
        all_sources = set(u['source'] for u in units)

        d = current_app.cache.get_dict(*(
                build_cache_key(k, source_lang) for k in all_sources
        ))
        # filter out None results (keys not found)
        already_cached = set(filter(lambda x: d[x] is not None, d))
        # unmangle the key to get a source string
        # TODO: update for memcached
        already_cached = set(split_cache_key(k) for k in already_cached)

        uncached = tuple(all_sources - already_cached)
        if not uncached:
            # Everything is already cached
            return

        cursor = self.get_cursor()
        select_query = """SELECT text, sid FROM sources_%s WHERE
        text IN %%(list)s""" % source_lang
        cursor.execute(select_query, {"list": uncached})
        already_stored = dict(cursor.fetchall())

        to_store = all_sources - already_cached - set(already_stored)
        if to_store:
            # some source strings still need to be stored
            insert_query = """INSERT INTO sources_%s (text, vector, length) VALUES(
            %%(source)s, TO_TSVECTOR(%%(lang_config)s, %%(source)s), %%(length)s)
            RETURNING sid""" % source_lang

            lang_config = lang_to_config(source_lang)
            params = [{
                    "lang_config": lang_config,
                    "source": s,
                    "length": len(s),
                } for s in to_store
            ]
            cursor.executemany(insert_query, params)

            # get the inserted rows back so that we have their IDs
            cursor.execute(select_query, {"list": tuple(to_store)})
            newly_stored = dict(cursor.fetchall())
            already_stored.update(newly_stored)

        current_app.cache.set_many(
                (build_cache_key(k, source_lang), v) \
                for (k, v) in already_stored.iteritems()
        )

    def add_store(self, store, source_lang, target_lang, commit=True):
        """insert all units in store in database"""
        units = [{
            'source': unicode(u.source),
            'target': unicode(u.target),
        } for u in store.units if u.istranslatable() and u.istranslated()]

        return self.add_list(units, source_lang, target_lang, commit)

    def add_list(self, units, source_lang, target_lang, commit=True):
        """insert all units in list into the database, units are
        represented as dictionaries"""
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)
        assert slang in self.source_langs

        self.get_all_sids(units, source_lang)
        count = 0
        try:
            cursor = self.get_cursor()
            for unit in units:
                unit['source_lang'] = slang
                unit['target_lang'] = tlang
                unit['lang_config'] = lang_config
                unit['length'] = len(unit['source'])
                self.add_dict(unit, cursor=cursor)
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
        slang = lang_to_table(source_lang)
        if slang not in self.source_langs:
            abort(404)

        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)

        if slang == tlang:
            # We really don't want to serve en->en requests
            abort(404)

        if isinstance(unit_source, str):
            unit_source = unicode(unit_source, "utf-8")

        max_length = current_app.config.get('MAX_LENGTH', 1000)
        min_similarity = max(min_similarity or current_app.config.get('MIN_SIMILARITY', 70), 30)
        max_candidates = max_candidates or current_app.config.get('MAX_CANDIDATES', 5)

        minlen = min_levenshtein_length(len(unit_source), min_similarity)
        maxlen = max_levenshtein_length(len(unit_source), min_similarity, max_length)

        minrank = max(min_similarity / 2, 30)

        search_str = unit_source
        cursor = self.get_cursor()
        query = """
SELECT * from (SELECT s.text AS source, t.text AS target, TS_RANK(s.vector, query, 32) * 1744.93406073519 AS rank
    FROM sources_%s s JOIN targets_%s t ON s.sid = t.sid,
    TO_TSQUERY(%%(lang_config)s, prepare_ortsquery(%%(search_str)s)) query
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
