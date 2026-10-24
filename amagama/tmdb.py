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

"""Module to provide a translation memory database."""

# We want to estimate PostgreSQL's compression of TEXT fields
try:
    # The text compression is an LZ type compression
    from lz4.frame import compress
    COMPRESSED_LIMIT = 2900
except ImportError:
    # With a different limit, this is a reasonable estimation
    from gzip import compress
    COMPRESSED_LIMIT = 2000

from collections import defaultdict
import logging
import math

from flask import abort, current_app
from psycopg2 import sql
from translate.lang import data
from translate.search.lshtein import LevenshteinComparer
from translate.storage import xliff

from amagama import postgres
from amagama.mojibake import CANDIDATE_REGEX, unmangle
from amagama.normalise import indexing_version

_table_name_cache = {}

# Sources whose "translation" is the translators' names or addresses (GNOME,
# KDE), never imported.
TRANSLATOR_CREDITS = frozenset({
    "translator-credits", "translator_credits", "Your names", "Your emails",
    "ROLES_OF_TRANSLATORS", "CREDIT_FOR_TRANSLATORS",
})

# Schemas named with these prefixes hold a source language's rebuild and its
# previous copy. Schemas starting with "_" are never served.
BUILD_PREFIX = '_build_'
OLD_PREFIX = '_old_'


def lang_to_table(code):
    if code in _table_name_cache:
        return _table_name_cache[code]
    # Normalize to simplest form.
    result = data.simplify_to_common(code)
    if data.langcode_ire.match(result):
        # Normalize to legal table name.
        table_name = result.replace("-", "_").replace("@", "_").lower()
        _table_name_cache[code] = table_name
        return table_name
    # Illegal language name.
    return None


CODE_CONFIG_MAP = {
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


# Keyed by schema name, so a code and its schema (pt_BR, pt_br) agree.
_SCHEMA_CONFIG_MAP = {lang_to_table(code): config for code, config in CODE_CONFIG_MAP.items()}


def lang_to_config(code):
    return CODE_CONFIG_MAP.get(code) or _SCHEMA_CONFIG_MAP.get(lang_to_table(code), 'simple')


def project_checker(project_style, source_lang):
    if project_style:
        from translate.filters.checks import projectcheckers
        checker = projectcheckers.get(project_style, None)
        if checker:
            checker = checker()
            from translate.lang import factory
            checker.config.sourcelang = factory.getlanguage(source_lang)
            return checker


def build_cache_key(text, code):
    """Build a simple string to use as cache key.

    For now this is not usable with memcached.
    """
    return "%s\n%s" % (code, text)


def split_cache_key(key):
    """Give the source string inside the given composite cache key."""
    return key.split('\n', 1)[1]


class TMDB(postgres.PostGres):

    INIT_FUNCTIONS = """
CREATE FUNCTION public.prepare_or_tsquery(text) RETURNS text AS $$
    SELECT ARRAY_TO_STRING(
        (SELECT ARRAY_AGG(quote_literal(token)) FROM TS_PARSE('default', $1) WHERE tokid != 12),
        '|');
$$ LANGUAGE SQL;
"""

    INIT_SOURCE = """
CREATE TABLE sources (
    sid SERIAL PRIMARY KEY,
    text TEXT NOT NULL,
    vector TSVECTOR NOT NULL,
    length INTEGER NOT NULL
);
CREATE UNIQUE INDEX sources_text_unique_idx ON sources (text);
"""

    INIT_TARGET = """
CREATE TABLE targets (
    sid INTEGER NOT NULL,
    text TEXT NOT NULL,
    lang VARCHAR(32) NOT NULL,
    FOREIGN KEY (sid) references sources(sid)
);
CREATE UNIQUE INDEX targets_unique_idx ON targets (sid, text, lang);
"""

    INIT_IMPORTS = """
CREATE TABLE IF NOT EXISTS imports (
    iid SERIAL PRIMARY KEY,
    block TEXT,
    filename TEXT NOT NULL,
    checksum CHAR(64),
    lang VARCHAR(32) NOT NULL,
    licence TEXT,
    units INTEGER NOT NULL,
    skipped INTEGER NOT NULL,
    imported TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

    DEPLOY_QUERY = """
ALTER TABLE sources DROP CONSTRAINT IF EXISTS sources_pkey CASCADE;
DROP INDEX IF EXISTS sources_text_unique_idx;
DROP INDEX IF EXISTS targets_unique_idx;

-- CLUSTER sources so that it is physically sorted from short to long
CREATE INDEX sources_cluster_idx ON sources (length) WITH (fillfactor = 100);
CLUSTER sources USING sources_cluster_idx;
DROP INDEX sources_cluster_idx;  -- was only for CLUSTER

-- CLUSTER targets so that one language's strings are physically contiguous
CREATE INDEX targets_cluster_idx ON targets (lang, length(text)) WITH (fillfactor = 100);
CLUSTER targets USING targets_cluster_idx;
DROP INDEX targets_cluster_idx;  -- was only for CLUSTER
CREATE INDEX targets_lang_sid_idx ON targets (lang, sid) WITH (fillfactor = 100);
--- The rest is handled in the code.
"""

    PREPARE_LOOKUP = """
PREPARE lookup AS
SELECT * from (
    SELECT s.text AS source, t.text AS target, TS_RANK(s.vector, query, 32) * 1744.93406073519 AS rank
    FROM sources s JOIN targets t ON s.sid = t.sid,
    TO_TSQUERY($1, public.prepare_or_tsquery($2)) query
    WHERE t.lang = $3 AND s.length BETWEEN $4 AND $5
    AND s.vector @@ query
) sub
WHERE rank > $6
ORDER BY rank DESC;
"""
    # TODO: stop returning "rank" once we're happy we have no users.

    schema_prefix = ''

    def __init__(self, *args, **kwargs):
        super(TMDB, self).__init__(*args, **kwargs)
        self._available_langs = {}
        self._load_source_langs()
        # set of languages with prepared statements per connection key:
        self._prepared_statements = defaultdict(set)

    def _load_source_langs(self):
        cursor = self.get_cursor()
        cursor.execute("SELECT schemaname FROM pg_tables WHERE tablename = 'sources'")
        self.source_langs = set()
        for row in cursor:
            name = row['schemaname']
            if self.schema_prefix:
                if name.startswith(self.schema_prefix):
                    self.source_langs.add(name[len(self.schema_prefix):])
            elif not name.startswith('_'):
                self.source_langs.add(name)

    def use_schemas(self, prefix):
        """Work on the schemas named <prefix><lang> instead of the live ones."""
        self.schema_prefix = prefix
        self._available_langs = {}
        self._load_source_langs()

    def schema(self, slang):
        return self.schema_prefix + slang

    def forget_connection(self, conn):
        super(TMDB, self).forget_connection(conn)
        self._prepared_statements.pop(id(conn), None)

    def init_app(self, app):
        super(TMDB, self).init_app(app)
        self.max_import_len = app.config.get('MAX_LENGTH', 2000)
        if self.max_import_len > 4000:
            logging.warning("Very high value for MAX_LENGTH. Please reconsider. Continuing anyway...")

    def init_db(self, source_langs):
        if not self.function_exists('prepare_or_tsquery'):
            cursor = self.get_cursor()
            cursor.execute(self.INIT_FUNCTIONS)
            cursor.connection.commit()

        for slang in source_langs:
            slang = lang_to_table(slang)
            if slang in self.source_langs:
                continue
            cursor = self.get_cursor(self.schema(slang))
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema(slang))))
            cursor.execute(self.INIT_SOURCE)
            cursor.execute(self.INIT_TARGET)
            cursor.execute(self.INIT_IMPORTS)
            self.source_langs.add(slang)
            cursor.connection.commit()

    def init_imports(self, slang):
        """Create the imports table in a schema made before it existed."""
        cursor = self.get_cursor(self.schema(slang))
        # Parallel imports would race on CREATE TABLE IF NOT EXISTS.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.schema(slang),))
        cursor.execute(self.INIT_IMPORTS)
        cursor.connection.commit()

    def record_import(self, slang, tlang, filename, checksum, units, skipped,
                      block=None, licence=None, commit=True):
        cursor = self.get_cursor(self.schema(lang_to_table(slang)))
        cursor.execute("""INSERT INTO imports
            (block, filename, checksum, lang, licence, units, skipped)
            VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (block, filename, checksum, lang_to_table(tlang), licence, units, skipped))
        if commit:
            cursor.connection.commit()

    def snapshot(self, slang, name):
        """Copy the live slang schema to _<name>_<slang>, which isn't served.

        Returns the number of sources and targets copied.
        """
        slang = lang_to_table(slang)
        snapshot = '_%s_%s' % (name, slang)
        cursor = self.get_cursor()
        try:
            if not self._schema_exists(cursor, slang):
                raise ValueError("Schema %s doesn't exist." % slang)
            if self._schema_exists(cursor, snapshot):
                raise ValueError("Schema %s already exists." % snapshot)
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(snapshot)))
            counts = []
            for table in ('sources', 'targets'):
                cursor.execute(sql.SQL("CREATE TABLE {} AS TABLE {}").format(
                    sql.Identifier(snapshot, table), sql.Identifier(slang, table)))
                counts.append(cursor.rowcount)
            cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            raise
        return tuple(counts)

    def load_snapshot(self, slang, name, block=None, licence=None):
        """Copy _<name>_<slang> into the empty slang schema, keeping sids.

        Skips what an import would also refuse: sources longer than
        MAX_LENGTH, and text too long for the unique indexes. Records an
        import per target language. Returns the number of sources and
        targets loaded and skipped.
        """
        slang = lang_to_table(slang)
        snapshot = '_%s_%s' % (name, slang)
        snap_sources = sql.Identifier(snapshot, 'sources')
        snap_targets = sql.Identifier(snapshot, 'targets')
        cursor = self.get_cursor(self.schema(slang))
        try:
            if not self._schema_exists(cursor, snapshot):
                raise ValueError("Schema %s doesn't exist." % snapshot)
            cursor.execute("SELECT to_regclass('sources_text_unique_idx') IS NOT NULL")
            if not cursor.fetchone()[0]:
                raise ValueError("Schema %s was deployed; load snapshots before deploy_db."
                                 % self.schema(slang))
            cursor.execute("SELECT EXISTS(SELECT 1 FROM sources)")
            if cursor.fetchone()[0]:
                raise ValueError("Schema %s isn't empty." % self.schema(slang))

            # Text under 1000 characters always fits the indexes.
            cursor.execute(sql.SQL("""INSERT INTO sources (sid, text, vector, length)
                SELECT sid, text, vector, length FROM {}
                WHERE length <= %s AND length(text) < 1000
                ON CONFLICT DO NOTHING""").format(snap_sources), (self.max_import_len,))
            cursor.execute(sql.SQL("""SELECT sid, text, vector, length FROM {}
                WHERE length <= %s AND length(text) >= 1000""").format(snap_sources),
                (self.max_import_len,))
            long_sources = [row for row in cursor.fetchall()
                            if self._indexable_string(row['text'])]
            cursor.executemany("""INSERT INTO sources (sid, text, vector, length)
                VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                [tuple(row) for row in long_sources])
            cursor.execute("""SELECT setval(pg_get_serial_sequence('sources', 'sid'),
                COALESCE(MAX(sid), 0) + 1, false) FROM sources""")

            cursor.execute(sql.SQL("""INSERT INTO targets (sid, text, lang)
                SELECT t.sid, t.text, t.lang FROM {} t JOIN sources s ON s.sid = t.sid
                WHERE length(t.text) < 1000
                ON CONFLICT DO NOTHING""").format(snap_targets))
            cursor.execute(sql.SQL("""SELECT t.sid, t.text, t.lang FROM {} t
                JOIN sources s ON s.sid = t.sid
                WHERE length(t.text) >= 1000""").format(snap_targets))
            long_targets = [tuple(row) for row in cursor.fetchall()
                            if self._indexable_string(row['text'])]
            cursor.executemany("""INSERT INTO targets (sid, text, lang)
                VALUES (%s, %s, %s) ON CONFLICT DO NOTHING""", long_targets)

            cursor.execute(sql.SQL("""SELECT
                (SELECT COUNT(*) FROM sources), (SELECT COUNT(*) FROM {})""").format(
                snap_sources))
            sources, snapshot_sources = cursor.fetchone()
            cursor.execute(sql.SQL("""SELECT s.lang, s.total, COALESCE(t.loaded, 0)
                FROM (SELECT lang, COUNT(*) AS total FROM {} GROUP BY lang) s
                LEFT JOIN (SELECT lang, COUNT(*) AS loaded FROM targets GROUP BY lang) t
                ON s.lang = t.lang ORDER BY s.lang""").format(snap_targets))
            per_lang = cursor.fetchall()
            for lang, total, loaded in per_lang:
                cursor.execute("""INSERT INTO imports
                    (block, filename, lang, licence, units, skipped)
                    VALUES (%s, %s, %s, %s, %s, %s)""",
                    (block, snapshot, lang, licence, loaded, total - loaded))
            cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            raise
        targets = sum(row[2] for row in per_lang)
        snapshot_targets = sum(row[1] for row in per_lang)
        return (sources, snapshot_sources - sources,
                targets, snapshot_targets - targets)

    def import_summary(self, slang):
        """Files, target languages and units per block, oldest block first."""
        cursor = self.get_cursor(self.schema(slang))
        cursor.execute("SELECT to_regclass('imports') IS NOT NULL")
        if not cursor.fetchone()[0]:
            return []
        cursor.execute("""SELECT block, COUNT(*), COUNT(DISTINCT lang),
                                 SUM(units), SUM(skipped), MAX(imported)
                          FROM imports GROUP BY block ORDER BY MIN(imported)""")
        return cursor.fetchall()

    def old_layout_langs(self):
        """Source languages stored in the old public.sources_<lang> tables."""
        cursor = self.get_cursor()
        cursor.execute("""SELECT tablename FROM pg_tables
                          WHERE schemaname = 'public' AND tablename LIKE 'sources\\_%'""")
        return sorted(row['tablename'][len('sources_'):] for row in cursor)

    def migrate_old_layout(self, slang):
        """Copy public.sources_<slang> and targets_<slang> into the <slang> schema.

        Returns the number of sources and targets copied. The old tables are
        left in place. An old database that went through deploy_db is copied
        into the deployed layout: no sid key and no unique indexes, which
        also can't hold strings longer than a btree entry.
        """
        if not self.function_exists('prepare_or_tsquery'):
            cursor = self.get_cursor()
            cursor.execute(self.INIT_FUNCTIONS)
            cursor.connection.commit()

        old_sources = sql.Identifier('public', 'sources_%s' % slang)
        old_targets = sql.Identifier('public', 'targets_%s' % slang)
        cursor = self.get_cursor(self.schema(slang))
        try:
            cursor.execute("""SELECT NOT EXISTS(SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = %s
                AND column_name = 'tid')""", ('targets_%s' % slang,))
            deployed = cursor.fetchone()[0]
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema(slang))))
            cursor.execute(self.INIT_SOURCE)
            cursor.execute(self.INIT_TARGET)
            cursor.execute(self.INIT_IMPORTS)
            if deployed:
                cursor.execute("""
                    ALTER TABLE sources DROP CONSTRAINT sources_pkey CASCADE;
                    DROP INDEX sources_text_unique_idx;
                    DROP INDEX targets_unique_idx;""")
            cursor.execute(sql.SQL("""INSERT INTO sources (sid, text, vector, length)
                SELECT sid, text, vector, length FROM {}""").format(old_sources))
            sources = cursor.rowcount
            cursor.execute("""SELECT setval(pg_get_serial_sequence('sources', 'sid'),
                COALESCE(MAX(sid), 0) + 1, false) FROM sources""")
            cursor.execute(sql.SQL("""INSERT INTO targets (sid, text, lang)
                SELECT sid, text, lang FROM {}""").format(old_targets))
            targets = cursor.rowcount
            cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            self.forget_connection(cursor.connection)
            raise
        self.source_langs.add(slang)
        return sources, targets

    def repair_mojibake(self, slang, apply=False):
        """Find, and with apply fix, mojibake in the slang schema.

        Returns the number of sources and targets that convert, and the
        (table, text) of those that look mangled but don't.
        """
        cursor = self.get_cursor(self.schema(slang))
        unconverted = []

        cursor.execute("SELECT DISTINCT sid, lang, text FROM targets WHERE text ~ %s",
                       (CANDIDATE_REGEX,))
        targets = []
        for sid, lang, text in cursor.fetchall():
            fixed = unmangle(text)
            if fixed is None:
                unconverted.append(('targets', text))
            else:
                targets.append((sid, lang, text, fixed))

        cursor.execute("SELECT sid, text FROM sources WHERE text ~ %s", (CANDIDATE_REGEX,))
        sources = []
        for sid, text in cursor.fetchall():
            fixed = unmangle(text)
            if fixed is None:
                unconverted.append(('sources', text))
            else:
                sources.append((sid, fixed))

        if not apply:
            return len(sources), len(targets), unconverted

        lang_config = lang_to_config(slang)
        checker = project_checker(None, slang)
        try:
            for sid, lang, text, fixed in targets:
                cursor.execute("DELETE FROM targets WHERE sid = %s AND lang = %s AND text = %s",
                               (sid, lang, text))
                cursor.execute("""INSERT INTO targets (sid, text, lang)
                    SELECT %(sid)s, %(text)s, %(lang)s WHERE NOT EXISTS (
                        SELECT 1 FROM targets
                        WHERE sid = %(sid)s AND lang = %(lang)s AND text = %(text)s)""",
                    {'sid': sid, 'text': fixed, 'lang': lang})
            for sid, fixed in sources:
                cursor.execute("SELECT sid FROM sources WHERE text = %s", (fixed,))
                existing = cursor.fetchone()
                if existing:
                    # Move the targets to the existing source, dropping ones
                    # it already has.
                    cursor.execute("""DELETE FROM targets t WHERE t.sid = %(old)s AND EXISTS (
                        SELECT 1 FROM targets u WHERE u.sid = %(new)s
                        AND u.lang = t.lang AND u.text = t.text)""",
                        {'old': sid, 'new': existing[0]})
                    cursor.execute("UPDATE targets SET sid = %s WHERE sid = %s",
                                   (existing[0], sid))
                    cursor.execute("DELETE FROM sources WHERE sid = %s", (sid,))
                else:
                    cursor.execute("""UPDATE sources SET text = %(text)s, length = %(length)s,
                        vector = TO_TSVECTOR(%(lang_config)s, %(indexed)s)
                        WHERE sid = %(sid)s""", {
                        'text': fixed, 'length': len(fixed), 'lang_config': lang_config,
                        'indexed': indexing_version(fixed, checker), 'sid': sid})
            cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            raise
        current_app.cache.clear()
        return len(sources), len(targets), unconverted

    def drop_db(self, source_langs):
        for slang in source_langs:
            slang = lang_to_table(slang)
            cursor = self.get_cursor()
            cursor.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(self.schema(slang))))
            cursor.connection.commit()
            self.source_langs.discard(slang)

    def deploy(self, slang):
        """Optimise the slang schema for lookups; imports no longer fit."""
        similarity = current_app.config.get("MIN_SIMILARITY")
        max_length = current_app.config.get("MAX_LENGTH")
        cursor = self.get_cursor(self.schema(slang))
        cursor.execute(self.DEPLOY_QUERY)
        lower_bound = 0
        for upper_bound in (28, 93):
            idx_name = sql.Identifier("sources_up_to_%d_text_idx" % upper_bound)
            if lower_bound == 0:
                cursor.execute(sql.SQL("""
                    CREATE INDEX {} ON sources USING gin(vector)
                    WHERE length <= %s""").format(idx_name),
                    (max_levenshtein_length(upper_bound, similarity, max_length),))
            else:
                cursor.execute(sql.SQL("""
                    CREATE INDEX {} ON sources USING gin(vector)
                    WHERE length BETWEEN %s AND %s""").format(idx_name),
                    (min_levenshtein_length(lower_bound, similarity),
                     max_levenshtein_length(upper_bound, similarity, max_length)))
            lower_bound = upper_bound + 1
        cursor.execute("""
            CREATE INDEX sources_long_idx ON sources USING gin(vector)
            WHERE length >= %s""", (min_levenshtein_length(lower_bound, similarity),))
        cursor.connection.commit()

    def _schema_exists(self, cursor, name):
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", (name,))
        return cursor.fetchone() is not None

    def swap(self, slang, rollback=False):
        """Make the slang build schema live, keeping the live one as old.

        With rollback, make the old schema live again, and the live one the
        build schema. Both renames happen in one transaction.
        """
        slang = lang_to_table(slang)
        live, build, old = slang, BUILD_PREFIX + slang, OLD_PREFIX + slang
        if rollback:
            needed, in_the_way = old, build
            renames = [(live, build), (old, live)]
        else:
            needed, in_the_way = build, old
            renames = [(live, old), (build, live)]
        cursor = self.get_cursor()
        try:
            if not self._schema_exists(cursor, needed):
                raise ValueError("Schema %s doesn't exist." % needed)
            if self._schema_exists(cursor, in_the_way):
                raise ValueError("Schema %s already exists." % in_the_way)
            for old_name, new_name in renames:
                if self._schema_exists(cursor, old_name):
                    cursor.execute(sql.SQL("ALTER SCHEMA {} RENAME TO {}").format(
                        sql.Identifier(old_name), sql.Identifier(new_name)))
            cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            raise
        self._available_langs = {}
        self._load_source_langs()
        current_app.cache.clear()

    @property
    def available_languages(self):
        if not self._available_langs:
            source_languages = list(self.source_langs)
            target_languages = set()

            # Get all the target languages.
            for slang in source_languages:
                cursor = self.get_cursor(self.schema(slang))
                query = "SELECT DISTINCT lang FROM targets"
                cursor.execute(query)
                target_results = cursor.fetchall()

                for result in target_results:
                    tlang = result[0]
                    target_languages.add(tlang)

            source_languages.sort()
            target_languages = list(target_languages)
            target_languages.sort()

            self._available_langs = {
                'sourceLanguages': source_languages,
                'targetLanguages': target_languages,
            }

        return self._available_langs

    def get_sid(self, unit_dict, cursor):
        source = unit_dict['source']
        slang = unit_dict['source_lang']
        key = build_cache_key(source, self.schema(slang))
        sid = current_app.cache.get(key)
        #TODO: when using memcached, check that we got the right one since
        # collisions on key names are possible.
        if sid:
            return sid

        query = """SELECT sid FROM sources WHERE text=%(source)s"""
        cursor.execute(query, unit_dict)
        result = cursor.fetchone()
        if result:
            sid = result['sid']
            current_app.cache.set(key, sid)
            return sid
        raise Exception("sid not found although it should have existed")

    def add_unit(self, unit, source_lang, target_lang, commit=True,
                 cursor=None):
        """Insert unit in the database."""
        #TODO: is that really the best way to handle unspecified source and
        # target languages? what about conflicts between unit attributes and
        # passed arguments?
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)

        if cursor is None:
            cursor = self.get_cursor(self.schema(slang))
        try:
            unitdict = {
                'source': str(unit.source),
                'target': str(unit.target),
                'source_lang': slang,
                'target_lang': tlang,
                'lang_config': lang_config,
            }

            # Unlike add_list()/add_store(), this inserts a single unit, so
            # the source string won't already be in the sources table
            # (add_dict()'s get_sid() only looks one up, it doesn't insert).
            self.get_all_sids([unitdict], source_lang, None)
            self.add_dict(unitdict, cursor)

            if commit:
                cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            raise

    def add_dict(self, unit, cursor):
        """Insert units represented as dictionaries in database.

        The caller is expected to handle errors.
        """
        unit['sid'] = self.get_sid(unit, cursor)
        # ON CONFLICT covers a parallel import adding the same target; NOT
        # EXISTS a deployed schema, which has no unique index.
        cursor.execute("""INSERT INTO targets (sid, text, lang)
            SELECT %(sid)s, %(target)s, %(target_lang)s
            WHERE NOT EXISTS (SELECT 1 FROM targets
                WHERE sid = %(sid)s AND lang = %(target_lang)s AND text = %(target)s)
            ON CONFLICT DO NOTHING""", unit)

    def get_all_sids(self, units, source_lang, project_style):
        """Ensure that all source strings are in the database+cache."""
        all_sources = set(u['source'] for u in units)
        schema = self.schema(lang_to_table(source_lang))

        d = current_app.cache.get_dict(*(
                build_cache_key(k, schema) for k in all_sources
        ))
        # Filter out None results (keys not found).
        already_cached = set(filter(lambda x: d[x] is not None, d))
        # Unmangle the key to get a source string.
        # TODO: update for memcached
        already_cached = set(split_cache_key(k) for k in already_cached)

        uncached = tuple(all_sources - already_cached)
        if not uncached:
            # Everything is already cached.
            return

        checker = project_checker(project_style, source_lang)

        cursor = self.get_cursor(schema)
        select_query = """SELECT text, sid FROM sources WHERE
        text IN %(list)s"""

        cursor.execute(select_query, {"list": uncached})
        already_stored = dict(cursor.fetchall())
        to_store = all_sources - already_cached - set(already_stored)
        if to_store:
            lang_config = lang_to_config(source_lang)
            params = [{
                    "lang_config": lang_config,
                    "source": s,
                    "indexed_source": indexing_version(s, checker),
                    "length": len(s),
                } for s in sorted(to_store)  # sorted to avoid deadlocks
            ]
            # A parallel import may add the same source first.
            cursor.executemany("""INSERT INTO sources (text, vector, length)
                VALUES (%(source)s, TO_TSVECTOR(%(lang_config)s, %(indexed_source)s), %(length)s)
                ON CONFLICT DO NOTHING""", params)
            cursor.execute(select_query, {"list": tuple(to_store)})
            already_stored.update(cursor.fetchall())

        current_app.cache.set_many({
                build_cache_key(k, schema): v
                for (k, v) in already_stored.items()
        })

    @staticmethod
    def _indexable_string(s):
        # The indexes can't handle strings of arbitrary length, but there isn't
        # a fixed limit, since compression is applied.
        # See https://github.com/translate/amagama/issues/3184
        # We don't implement the same compression, and we don't take the other
        # columns going into the index into account. So we have to play on the
        # safe side.
        # The limit is a third of a buffer page, usually 2712 bytes.

        # Shortcut to avoid the compression most of the time before testing
        # with compression:
        return len(s) < 1000 or \
               len(compress(s.encode('utf-8'))) < COMPRESSED_LIMIT

    @staticmethod
    def _translated(u):
        # Translate Toolkit counts an XLIFF target as translated only with
        # approved="yes"; state="translated", "final" or "signed-off" means it too.
        if isinstance(u, xliff.xliffunit):
            return bool(u.target) and u.get_state_n() >= u.S_UNREVIEWED
        return u.istranslated()

    def _usable_unit(self, u):
        return u.istranslatable() and \
               bool(u.source) and \
               str(u.source) not in TRANSLATOR_CREDITS and \
               self._translated(u) and \
               len(u.source) <= self.max_import_len and \
               self._indexable_string(u.source) and \
               self._indexable_string(u.target)

    def add_store(self, store, source_lang, target_lang, project_style=None,
                  commit=True):
        """Insert all units in store in database."""
        units = [{
            'source': str(u.source),
            'target': str(u.target),
        } for u in store.units if self._usable_unit(u)]

        if not units:
            return 0
        return self.add_list(units, source_lang, target_lang, project_style,
                             commit)

    def add_list(self, units, source_lang, target_lang, project_style=None,
                 commit=True):
        """Insert all units in list into the database.

        Units are represented as dictionaries.
        """
        slang = lang_to_table(source_lang)
        tlang = lang_to_table(target_lang)
        lang_config = lang_to_config(slang)
        assert slang in self.source_langs
        if slang == tlang:
            # These won't be returned when querying, so it is useless to even
            # store them.
            return 0

        self.get_all_sids(units, source_lang, project_style)

        cursor = self.get_cursor(self.schema(slang))
        try:
            # We sort to avoid deadlocks during parallel import.
            units.sort(key=lambda x: x['target'])
            count = 0
            for unit in units:
                unit.update({
                    'source_lang': slang,
                    'target_lang': tlang,
                    'lang_config': lang_config,
                })
                self.add_dict(unit, cursor=cursor)
                count += 1
            if commit:
                cursor.connection.commit()
        except Exception:
            cursor.connection.rollback()
            raise

        if count:
            self._available_langs = {}
        return count

    @property
    def comparer(self):
        if not hasattr(self, '_comparer'):
            max_length = current_app.config.get('MAX_LENGTH', 2000)
            self._comparer = LevenshteinComparer(max_length)
        return self._comparer

    def _translate_query(self, cursor, slang, tlang, query, min_len, max_len, min_rank):
        if slang not in self._prepared_statements[id(cursor.connection)]:
            if not self.prepared_statement_exists("lookup"):
                cursor.execute(self.PREPARE_LOOKUP)
            self._prepared_statements[id(cursor.connection)].add(slang)
        lang_config = lang_to_config(slang)
        cursor.execute("EXECUTE lookup (%s, %s, %s, %s, %s, %s)",
                       (lang_config, query, tlang, min_len, max_len, min_rank))

    def translate_unit(self, unit_source, source_lang, target_lang,
                       project_style=None, min_similarity=None,
                       max_candidates=None):
        """Return TM suggestions for unit_source."""
        slang = lang_to_table(source_lang)
        if slang not in self.source_langs:
            abort(404)

        tlang = lang_to_table(target_lang)
        if slang == tlang:
            # We really don't want to serve en->en requests.
            abort(404)

        if isinstance(unit_source, bytes):
            unit_source = str(unit_source, "utf-8")

        checker = project_checker(project_style, source_lang)

        max_length = current_app.config.get('MAX_LENGTH', 2000)
        min_similarity = max(min_similarity or current_app.config.get('MIN_SIMILARITY', 70), 70)
        max_candidates = min(max_candidates or current_app.config.get('MAX_CANDIDATES', 5), 30)

        source_len = len(unit_source)
        minlen = min_levenshtein_length(source_len, min_similarity)
        maxlen = max_levenshtein_length(source_len, min_similarity, max_length)

        minrank = max(min_similarity / 2, 30)

        cursor = self.get_cursor(self.schema(slang))
        try:
            self._translate_query(cursor, slang, tlang,
                                  indexing_version(unit_source, checker),
                                  minlen, maxlen, minrank)
        except postgres.psycopg2.ProgrammingError:
            # Avoid problems parsing strings like '<a "\b">'. If any of the
            # characters in the example string is not present, then no error is
            # thrown. The error is still present if any number of other letters
            # are included between any of the characters in the example string.
            cursor.connection.rollback()
            return []

        results = []
        similarity = self.comparer.similarity
        for row in cursor:
            quality = similarity(unit_source, row['source'], min_similarity)
            if quality >= min_similarity:
                result = dict(row)
                result['quality'] = quality
                results.append(result)
        results.sort(key=lambda match: match['quality'], reverse=True)
        results = results[:max_candidates]
        return results


def min_levenshtein_length(length, min_similarity):
    # This should return an integer, to avoid using a float in the SQL, which
    # produces slightly less optimal query plans. In Python 2, math.ceil
    # returns a float.
    return int(math.ceil(max(length * (min_similarity/100.0), 2)))


def max_levenshtein_length(length, min_similarity, max_length):
    # This should return an integer, to avoid using a float in the SQL, which
    # produces slightly less optimal query plans. In Python 2, math.floor
    # returns a float.
    return int(math.floor(min(length / (min_similarity/100.0), max_length)))
