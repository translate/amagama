#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


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

import datetime
import json
import logging
import math
import re
import weakref

import psycopg2
from flask import abort, current_app
from psycopg2 import errors, sql
from psycopg2.extras import execute_values
from translate.lang import data
from translate.search.lshtein import LevenshteinComparer
from translate.storage import xliff
from translate.storage.base import TranslationUnit

from amagama import postgres
from amagama.mojibake import CANDIDATE_REGEX, unmangle
from amagama.source_options import SourceOptions
from amagama import plurals, pruning
from amagama.normalise import indexing_version
from amagama.updater.languages import COUNTRY_CODES, normalise_language, not_language

# Shorter strings always fit a btree index row (2704 bytes, with the row's
# other columns); a longer one may not, however it compresses.
SAFE_INDEX_BYTES = 2600
# Rows per multi-row INSERT when a file's units are stored.
INSERT_PAGE = 1000
# word_counts keeps words in at least this many sources: the rest are rare,
# all alike to amagama.pruning.
WORD_COUNT_MIN = 100

_table_name_cache = {}

# Sources whose "translation" is the translators' names or addresses (GNOME,
# KDE), never imported.
TRANSLATOR_CREDITS = frozenset({
    "translator-credits", "translator_credits", "Your names", "Your emails",
    "ROLES_OF_TRANSLATORS", "CREDIT_FOR_TRANSLATORS",
})

# A word of a source: letters, perhaps joined by an apostrophe or a hyphen,
# with punctuation around them; placeholders, numbers and URLs aren't words.
_WORD = re.compile(r"[(\"'“‘«]*[^\W\d_]+(?:['’-][^\W\d_]+)*[)\"'”’».,:;!?…]*")
_MARKUP = re.compile(r"<[^<>]*>|&\w+;")


def copied_source(source, target):
    """Whether target is source left untranslated: the same text, of two
    words or more and not all capitals. A name, an acronym or a placeholder
    is often the same in every language."""
    if indexing_version(target) != indexing_version(source):
        return False
    words = [word for word in _MARKUP.sub(" ", source).split() if _WORD.fullmatch(word)]
    return len(words) >= 2 and not all(word.isupper() for word in words)


# Schemas named with these prefixes hold a source language's rebuild and its
# previous copy. Schemas starting with "_" are never served.
BUILD_PREFIX = '_build_'
OLD_PREFIX = '_old_'


# Languages whose every region is localised apart, with the region that is
# the language itself: en_AU and en_GB are targets, en_US is en, the source.
REGIONAL = {"en": "US"}


def lang_to_table(code):
    """The name a language's data is stored under, or None for a bad code.

    Any spelling of a code gives the same name. The country is dropped where
    the language is rarely localised by country (es_ES is es), but a variant
    is kept: sr@latin and sr, or ca@valencia and ca, stay apart. So are the
    regions in REGIONAL.
    """
    if code in _table_name_cache:
        return _table_name_cache[code]
    canonical = normalise_language(code) if code else None
    if not canonical:
        return None
    base, _, variant = canonical.partition("@")
    lang, _, region = base.partition("_")
    if lang in REGIONAL and region and region != REGIONAL[lang]:
        result = base
    else:
        result = data.simplify_to_common(base)
    result += "@" + variant if variant else ""
    if data.langcode_ire.match(result):
        # Normalize to legal table name.
        table_name = result.replace("-", "_").replace("@", "_").lower()
        _table_name_cache[code] = table_name
        return table_name
    # Illegal language name.
    return None


# Names the 2019 database stored as target languages that are ISO 639 codes
# but weren't languages there.
SNAPSHOT_NOT_LANGUAGES = {
    "bad": "a directory name, though the Banda languages' code",
    "mac": "a directory name, though Macedonian's bibliographic code",
}


def _not_language_table(name):
    """Why a stored target language's name isn't a language's, or None."""
    if name in SNAPSHOT_NOT_LANGUAGES:
        return SNAPSHOT_NOT_LANGUAGES[name]
    return not_language(name.partition("_")[0])


def snapshot_left_out(left_out):
    """load_snapshot()'s target languages left out, each with why."""
    return ", ".join("%s (%s; %d targets)" % (name, why, targets)
                     for name, targets, why in left_out)


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



# Letters a language's own text never has: a target with one its source
# hasn't is another language's text stored under the code, such as
# Crimean Tatar's ñ and Azerbaijani's ə under tr (#3202).
FOREIGN_LETTERS = {"tr": "ñÑəƏ"}


def foreign_letters(lang, source, target):
    """Whether target has a letter its language never writes that source hasn't."""
    letters = FOREIGN_LETTERS.get(lang_to_table(lang) or "", "")
    return any(letter in target and letter not in source for letter in letters)


# KDE's transcripts: a translation may add a scripted alternative after
# this, "Ставке пре %1|/|Ставке пре $[на-реч %1 ген]", which only KDE's
# runtime reads (#3862).
TRANSCRIPT = "|/|"


def without_transcript(source, target):
    """target without a KDE transcript the source hasn't."""
    if TRANSCRIPT in target and TRANSCRIPT not in source:
        return target.split(TRANSCRIPT, 1)[0]
    return target


def _foreign_letters_sql():
    """(SQL condition, its parameters) for foreign_letters() of targets t
    of sources s."""
    conditions, params = [], []
    for lang, letters in sorted(FOREIGN_LETTERS.items()):
        conditions.append("(t.lang = %%s AND (%s))" % " OR ".join(
            ["(strpos(t.text, %s) > 0 AND strpos(s.text, %s) = 0)"] * len(letters)))
        params.append(lang)
        for letter in letters:
            params += [letter, letter]
    return " OR ".join(conditions), params


def _encodable(text):
    """text as the database can hold it, or None.

    Readers can leave a character outside the BMP as two surrogates (the
    Translate Toolkit's Android reader does, for "\\uD83D\\uDC8E"); they are
    joined. An unpaired surrogate can't be stored, nor can a NUL.
    """
    if "\x00" in text:
        return None
    try:
        text.encode("utf-8")
        return text
    except UnicodeEncodeError:
        try:
            return text.encode("utf-16", "surrogatepass").decode("utf-16")
        except UnicodeDecodeError:
            return None


def _long(text):
    return len(text.encode("utf-8")) > SAFE_INDEX_BYTES


def _insert_indexable(cursor, query, params, text, returning=False):
    """Run an INSERT of text; False if text is too long for the index.

    With returning, give the row the INSERT returned instead of True.
    Only a long text risks the limit, and only it pays for a savepoint.
    """
    if not _long(text):
        cursor.execute(query, params)
        return cursor.fetchone() if returning else True
    cursor.execute("SAVEPOINT long_text")
    try:
        cursor.execute(query, params)
    except errors.ProgramLimitExceeded:
        cursor.execute("ROLLBACK TO SAVEPOINT long_text")
        return False
    row = cursor.fetchone() if returning else True
    cursor.execute("RELEASE SAVEPOINT long_text")
    return row

class TMDB(postgres.PostGres):

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
    tid SERIAL,
    sid INTEGER NOT NULL,
    text TEXT NOT NULL,
    lang VARCHAR(32) NOT NULL,
    -- The checks it fails, as bits of check_names; NULL for none.
    checks BIGINT,
    -- A plural's forms, in its file's order, and how many the language
    -- has (amagama.plurals); NULL for any other translation.
    forms TEXT[],
    nplurals SMALLINT,
    FOREIGN KEY (sid) references sources(sid)
);
CREATE UNIQUE INDEX targets_unique_idx ON targets (sid, text, lang);
-- The imports that supplied each target.
CREATE TABLE target_imports (
    tid INTEGER NOT NULL,
    iid INTEGER NOT NULL
);
CREATE UNIQUE INDEX target_imports_unique_idx ON target_imports (tid, iid);
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

    INIT_BLOCKS = """
CREATE TABLE IF NOT EXISTS blocks (
    name TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    version TEXT,
    weight DOUBLE PRECISION NOT NULL DEFAULT 1
);
ALTER TABLE blocks ADD COLUMN IF NOT EXISTS weight DOUBLE PRECISION NOT NULL DEFAULT 1;
"""

    # The checks a rebuild ran, each a bit of targets.checks; those left_out
    # removed the targets failing them.
    INIT_CHECKS = """
CREATE TABLE IF NOT EXISTS check_names (
    bit SMALLINT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    left_out BOOLEAN NOT NULL
);
ALTER TABLE targets ADD COLUMN IF NOT EXISTS checks BIGINT;
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

    # A lookup's query as text, and how many sources have each of its words,
    # for a filter on fewer of them (amagama.pruning).
    QUERY_WORDS = """
SELECT query::text, COALESCE((SELECT JSON_OBJECT_AGG(c.word, c.sources) FROM word_counts c
    WHERE c.word = ANY(TSVECTOR_TO_ARRAY(TO_TSVECTOR(%(config)s, %(text)s)))), '{}')
FROM TO_TSQUERY(%(config)s, (SELECT STRING_AGG(
    '''' || REPLACE(REPLACE(token, CHR(92), CHR(92) || CHR(92)), '''', '''''') || '''',
    '|') FROM TS_PARSE('default', %(text)s) WHERE tokid != 12)) query
"""

    LOOKUP = """
PREPARE {name} AS
SELECT sid, source, target, rank from (
    SELECT s.sid, {tid}s.text AS source, t.text AS target,
        TS_RANK(s.vector, query, 32) * 1744.93406073519 AS rank
    FROM sources s JOIN targets t ON s.sid = t.sid,
    TO_TSQUERY($1, (SELECT STRING_AGG(
        '''' || REPLACE(REPLACE(token, CHR(92), CHR(92) || CHR(92)), '''', '''''') || '''',
        '|')
        FROM TS_PARSE('default', $2) WHERE tokid != 12)) query
    WHERE t.lang = $3 AND s.length BETWEEN $4 AND $5
    AND s.vector @@ query{pruned}
) sub
WHERE rank > $6
ORDER BY rank DESC{order};
"""
    # Equal matches in import order, not the table's physical order (which
    # deploy_db sorts by length). A schema made before targets had a tid
    # can't tell, so its ties stay in physical order.
    PREPARE_LOOKUP = LOOKUP.format(name="lookup", tid="t.tid, ", order=", tid", pruned="")
    PREPARE_LOOKUP_WITHOUT_TID = LOOKUP.format(name="lookup_without_tid", tid="", order="",
                                               pruned="")
    # The same, matching only sources that also match $7: a query on fewer
    # words that every source ranking above $6 matches (amagama.pruning).
    PRUNED = "\n    AND s.vector @@ $7::tsquery"
    PREPARE_LOOKUP_PRUNED = LOOKUP.format(name="lookup_pruned", tid="t.tid, ",
                                          order=", tid", pruned=PRUNED)
    PREPARE_LOOKUP_PRUNED_WITHOUT_TID = LOOKUP.format(
        name="lookup_pruned_without_tid", tid="", order="", pruned=PRUNED)
    # TODO: stop returning "rank" once we're happy we have no users.

    schema_prefix = ''

    def __init__(self, *args, **kwargs):
        super(TMDB, self).__init__(*args, **kwargs)
        self._available_langs = {}
        self._sources = None
        self._weights = {}
        self._licences = {}
        self._check_names = {}
        self._load_source_langs()
        # {connection: {slang: the names of the lookup statements prepared for it,
        # as now and, if its data counts words, pruned}}
        self._prepared_statements = weakref.WeakKeyDictionary()
        # {connection: {schema: whether its targets hold plurals' forms}}
        self._forms_known = weakref.WeakKeyDictionary()

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
        self._sources = None
        self._weights = {}
        self._licences = {}
        self._check_names = {}
        self._load_source_langs()

    def schema(self, slang):
        return self.schema_prefix + slang

    def forget_connection(self, conn):
        super(TMDB, self).forget_connection(conn)
        self._prepared_statements.pop(conn, None)
        self._forms_known.pop(conn, None)

    def init_app(self, app):
        super(TMDB, self).init_app(app)
        self.max_import_len = app.config.get('MAX_LENGTH', 2000)
        if self.max_import_len > 4000:
            logging.warning("Very high value for MAX_LENGTH. Please reconsider. Continuing anyway...")

    def init_db(self, source_langs):
        for slang in source_langs:
            slang = lang_to_table(slang)
            if slang in self.source_langs:
                continue
            cursor = self.get_cursor(self.schema(slang))
            cursor.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema(slang))))
            cursor.execute(self.INIT_SOURCE)
            cursor.execute(self.INIT_TARGET)
            cursor.execute(self.INIT_IMPORTS)
            cursor.execute(self.INIT_BLOCKS)
            self.source_langs.add(slang)
            cursor.connection.commit()

    def init_imports(self, slang):
        """Create the imports table in a schema made before it existed."""
        cursor = self.get_cursor(self.schema(slang))
        # Parallel imports would race on CREATE TABLE IF NOT EXISTS.
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (self.schema(slang),))
        cursor.execute(self.INIT_IMPORTS)
        cursor.connection.commit()

    def start_import(self, slang, tlang, filename, block=None, licence=None):
        """Add the imports row of a file about to be imported; return its iid.

        The row is part of the import's transaction, and its targets link to
        it. finish_import() records the counts.
        """
        cursor = self.get_cursor(self.schema(lang_to_table(slang)))
        cursor.execute("""INSERT INTO imports (block, filename, lang, licence, units, skipped)
            VALUES (%s, %s, %s, %s, 0, 0) RETURNING iid""",
            (block, filename, lang_to_table(tlang), licence))
        return cursor.fetchone()[0]

    def record_blocks(self, slang, blocks):
        """Record each of the (name, title, version, weight) blocks."""
        cursor = self.get_cursor(self.schema(lang_to_table(slang)))
        cursor.execute(self.INIT_BLOCKS)
        cursor.executemany("""INSERT INTO blocks (name, title, version, weight)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (name) DO UPDATE SET title = EXCLUDED.title,
            version = EXCLUDED.version, weight = EXCLUDED.weight""", blocks)
        cursor.connection.commit()

    def forget_import(self, slang, iid):
        """Undo an import that failed: roll back what isn't committed, and
        unlink what its committed pieces stored. Their targets are noted in
        forgotten_targets, for drop_forgotten() to delete once no import is
        running: another may be linking to one of them now."""
        connection = self.get_cursor(self.schema(lang_to_table(slang))).connection
        self.rollback(connection)
        cursor = self.get_cursor(self.schema(lang_to_table(slang)))
        cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                       (self.schema(slang) + ".forgotten",))
        cursor.execute("CREATE TABLE IF NOT EXISTS forgotten_targets (tid INTEGER NOT NULL)")
        cursor.execute("""INSERT INTO forgotten_targets
            SELECT tid FROM target_imports WHERE iid = %s""", (iid,))
        cursor.execute("DELETE FROM target_imports WHERE iid = %s", (iid,))
        cursor.execute("DELETE FROM imports WHERE iid = %s", (iid,))
        cursor.connection.commit()

    def drop_forgotten(self, slang):
        """Delete the targets of forgotten imports that no import links to,
        and the sources left with no targets; for when no import is running.
        Returns how many targets went."""
        cursor = self.get_cursor(self.schema(lang_to_table(slang)))
        if not self._has_relation(cursor, "forgotten_targets"):
            return 0
        cursor.execute("""DELETE FROM targets t USING (SELECT DISTINCT tid FROM forgotten_targets) f
            WHERE t.tid = f.tid AND NOT EXISTS (SELECT 1 FROM target_imports l WHERE l.tid = t.tid)
            RETURNING t.sid""")
        rows = cursor.fetchall()
        sids = sorted({sid for (sid,) in rows})
        cursor.execute("""DELETE FROM sources s WHERE s.sid = ANY(%s)
            AND NOT EXISTS (SELECT 1 FROM targets t WHERE t.sid = s.sid)""", (sids,))
        cursor.execute("DROP TABLE forgotten_targets")
        cursor.connection.commit()
        return len(rows)

    def finish_import(self, slang, iid, units, skipped, checksum=None, commit=True):
        cursor = self.get_cursor(self.schema(lang_to_table(slang)))
        cursor.execute("""UPDATE imports SET units = %s, skipped = %s, checksum = %s,
            imported = now() WHERE iid = %s""", (units, skipped, checksum, iid))
        if commit:
            cursor.connection.commit()

    def recorded_write(self, slang, tlang, name, total, add):
        """Run add(iid) for a write-API request, recorded as an import of block api.

        add returns how many of the total units it stored. A schema made
        before target_imports existed records nothing.
        """
        slang = lang_to_table(slang)
        iid = None
        if slang in self.source_langs and self._has_relation(
                self.get_cursor(self.schema(slang)), 'target_imports'):
            iid = self.start_import(slang, tlang, name, block='api')
        count = add(iid)
        if iid is not None:
            self.finish_import(slang, iid, count, total - count)
        return count

    @staticmethod
    def _has_relation(cursor, name):
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
        return cursor.fetchone()[0]

    def _link_mode(self, cursor, iid):
        """The iid to link targets to, and whether targets has its unique index.

        A schema made before target_imports existed gets no links.
        """
        if iid is not None and not self._has_relation(cursor, 'target_imports'):
            iid = None
        return iid, self._has_relation(cursor, 'targets_unique_idx')

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
            self.rollback(cursor.connection)
            raise
        return tuple(counts)

    def records_blocks(self, slang):
        """Whether the live slang schema records which block supplied what."""
        slang = lang_to_table(slang)
        cursor = self.get_cursor()
        found = all(self._has_relation(cursor, "%s.%s" % (slang, table))
                    for table in ("imports", "target_imports"))
        cursor.connection.commit()
        return found

    def snapshot_fingerprint(self, slang, name):
        """The _<name>_<slang> snapshot's schema id and row counts, as text, or
        None: a snapshot made again (a new schema) is one to load again."""
        snapshot = "_%s_%s" % (name, lang_to_table(slang))
        cursor = self.get_cursor()
        if not self._schema_exists(cursor, snapshot):
            return None
        cursor.execute(sql.SQL("SELECT (SELECT oid FROM pg_namespace WHERE nspname = %s), "
                               "(SELECT COUNT(*) FROM {0}.sources), "
                               "(SELECT COUNT(*) FROM {0}.targets)").format(
            sql.Identifier(snapshot)), (snapshot,))
        found = cursor.fetchone()
        cursor.connection.commit()
        return "schema %d: %d sources, %d targets" % tuple(found)

    def live_built(self, slang):
        """When the live slang schema's newest import was, as text, or None."""
        slang = lang_to_table(slang)
        cursor = self.get_cursor()
        if not self._has_relation(cursor, "%s.imports" % slang):
            return None
        cursor.execute(sql.SQL("SELECT MAX(imported) FROM {}.imports").format(
            sql.Identifier(slang)))
        built = cursor.fetchone()[0]
        cursor.connection.commit()
        return built.isoformat() if built else None

    def copy_blocks(self, slang, blocks):
        """Copy blocks' data from the live slang schema into the build schema,
        as importing their files again would store it: their imports, the
        sources and targets those supplied, with each target's check bits,
        and the links. The build may hold some of the sources and targets
        already (from the snapshot or another block); they're shared, as an
        import would share them.

        Returns (imports, sources, targets, links) copied, or None if live
        doesn't record which block supplied what (built before imports were
        recorded): then the blocks must be imported.
        """
        slang = lang_to_table(slang)
        live, build = sql.Identifier(slang), sql.Identifier(BUILD_PREFIX + slang)
        cursor = self.get_cursor(self.schema(slang))
        if not all(self._has_relation(cursor, "%s.%s" % (slang, table))
                   for table in ("imports", "target_imports")):
            return None
        names = sorted(blocks)
        try:
            # Each copied import gets the build's next iid.
            cursor.execute(sql.SQL("""CREATE TEMP TABLE copied_imports ON COMMIT DROP AS
                SELECT iid AS old, nextval(pg_get_serial_sequence(%s, 'iid')) AS new
                FROM {}.imports WHERE block = ANY(%s)""").format(live),
                (BUILD_PREFIX + slang + ".imports", names))
            cursor.execute(sql.SQL("""INSERT INTO {0}.imports
                    (iid, block, filename, checksum, lang, licence, units, skipped, imported)
                SELECT c.new, i.block, i.filename, i.checksum, i.lang, i.licence, i.units,
                    i.skipped, i.imported
                FROM {1}.imports i JOIN copied_imports c ON c.old = i.iid""").format(build, live))
            imports = cursor.rowcount
            # One pass over the links for all the blocks: live has no index by iid.
            cursor.execute(sql.SQL("""CREATE TEMP TABLE copied_links ON COMMIT DROP AS
                SELECT l.tid, c.new AS iid FROM {}.target_imports l
                JOIN copied_imports c ON c.old = l.iid""").format(live))
            cursor.execute("CREATE INDEX ON copied_links (tid)")
            # A plural's forms too, where live has them (built after #3351).
            cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_attribute
                WHERE attrelid = to_regclass(%s) AND attname = 'forms' AND NOT attisdropped)""",
                (slang + ".targets",))
            forms = ("forms, nplurals" if cursor.fetchone()[0]
                     else "NULL::text[] AS forms, NULL::smallint AS nplurals")
            cursor.execute(sql.SQL("""CREATE TEMP TABLE copied_targets ON COMMIT DROP AS
                SELECT tid, sid, text, lang, checks, %s FROM {}.targets
                WHERE tid IN (SELECT tid FROM copied_links)""" % forms).format(live))
            # Each source once, with its vector, and the build's sid for it.
            cursor.execute(sql.SQL("""CREATE TEMP TABLE copied_sources ON COMMIT DROP AS
                SELECT sid, text, vector, length FROM {}.sources
                WHERE sid IN (SELECT sid FROM copied_targets)""").format(live))
            # Text too long for the build's unique indexes as it is goes one
            # row at a time, as an import's does: live has no such index.
            cursor.execute(sql.SQL("""INSERT INTO {}.sources (text, vector, length)
                SELECT text, vector, length FROM copied_sources WHERE octet_length(text) <= %s
                ON CONFLICT (text) DO NOTHING""").format(build), (SAFE_INDEX_BYTES,))
            sources = cursor.rowcount
            cursor.execute("""SELECT text, vector, length FROM copied_sources
                WHERE octet_length(text) > %s""", (SAFE_INDEX_BYTES,))
            insert = sql.SQL("""INSERT INTO {}.sources (text, vector, length) VALUES (%s, %s, %s)
                ON CONFLICT (text) DO NOTHING RETURNING sid""").format(build).as_string(cursor)
            for row in cursor.fetchall():
                # The row it returns, none if the build had the text already.
                sources += bool(_insert_indexable(cursor, insert, tuple(row), row[0],
                                                  returning=True))
            cursor.execute(sql.SQL("""CREATE TEMP TABLE copied_sids ON COMMIT DROP AS
                SELECT c.sid AS old, b.sid AS new FROM copied_sources c
                JOIN {}.sources b ON b.text = c.text""").format(build))
            cursor.execute("CREATE INDEX ON copied_sids (old)")
            # The targets this inserts keep their check bits, and aren't checked
            # again; one the build has already (from the snapshot, say) is.
            cursor.execute(sql.SQL("""CREATE TABLE IF NOT EXISTS {}.checked_before
                (tid INTEGER PRIMARY KEY)""").format(build))
            cursor.execute(sql.SQL("""WITH inserted AS (
                    INSERT INTO {0}.targets (sid, text, lang, checks, forms, nplurals)
                    SELECT m.new, t.text, t.lang, t.checks, t.forms, t.nplurals
                    FROM copied_targets t
                    JOIN copied_sids m ON m.old = t.sid WHERE octet_length(t.text) <= %s
                    ON CONFLICT (sid, text, lang) DO NOTHING RETURNING tid)
                INSERT INTO {0}.checked_before SELECT tid FROM inserted""").format(build),
                (SAFE_INDEX_BYTES,))
            targets = cursor.rowcount
            cursor.execute("""SELECT m.new, t.text, t.lang, t.checks, t.forms, t.nplurals
                FROM copied_targets t
                JOIN copied_sids m ON m.old = t.sid WHERE octet_length(t.text) > %s""",
                (SAFE_INDEX_BYTES,))
            insert = sql.SQL("""INSERT INTO {}.targets (sid, text, lang, checks, forms, nplurals)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (sid, text, lang) DO NOTHING RETURNING tid""").format(
                build).as_string(cursor)
            checked = sql.SQL("INSERT INTO {}.checked_before VALUES (%s)").format(
                build).as_string(cursor)
            for row in cursor.fetchall():
                inserted = _insert_indexable(cursor, insert, tuple(row), row[1], returning=True)
                if inserted:
                    targets += 1
                    cursor.execute(checked, (inserted[0],))
            cursor.execute(sql.SQL("""INSERT INTO {}.target_imports (tid, iid)
                SELECT b.tid, l.iid FROM copied_links l
                JOIN copied_targets t ON t.tid = l.tid
                JOIN copied_sids m ON m.old = t.sid
                JOIN {}.targets b ON b.sid = m.new AND b.text = t.text AND b.lang = t.lang
                ON CONFLICT DO NOTHING""").format(build, build))
            links = cursor.rowcount
            cursor.connection.commit()
        except Exception:
            self.rollback(cursor.connection)
            raise
        return imports, sources, targets, links

    def load_snapshot(self, slang, name, block=None, licence=None):
        """Copy _<name>_<slang> into the empty slang schema, keeping sids.

        Skips what an import would also refuse: sources longer than
        MAX_LENGTH, text too long for the unique indexes, and targets
        stored under codes that aren't languages. Records an import per
        target language. Returns the number of sources and targets loaded
        and skipped, and the target languages left out: (name, targets, why).
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

            # Text of up to SAFE_INDEX_BYTES always fits the indexes.
            cursor.execute(sql.SQL("""INSERT INTO sources (sid, text, vector, length)
                SELECT sid, text, vector, length FROM {}
                WHERE length <= %s AND octet_length(text) <= %s
                ON CONFLICT DO NOTHING""").format(snap_sources),
                (self.max_import_len, SAFE_INDEX_BYTES))
            cursor.execute(sql.SQL("""SELECT sid, text, vector, length FROM {}
                WHERE length <= %s AND octet_length(text) > %s""").format(snap_sources),
                (self.max_import_len, SAFE_INDEX_BYTES))
            for row in cursor.fetchall():
                if self._indexable_string(row['text']):
                    _insert_indexable(cursor, """INSERT INTO sources (sid, text, vector, length)
                        VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                        tuple(row), row['text'])
            cursor.execute("""SELECT setval(pg_get_serial_sequence('sources', 'sid'),
                COALESCE(MAX(sid), 0) + 1, false) FROM sources""")

            cursor.execute(sql.SQL("SELECT lang, COUNT(*) FROM {} GROUP BY lang ORDER BY lang")
                           .format(snap_targets))
            totals = cursor.fetchall()
            snapshot_targets = sum(total for lang, total in totals)
            left_out = [(lang, total, _not_language_table(lang)) for lang, total in totals
                        if _not_language_table(lang)]
            langs = [lang for lang, total in totals if not _not_language_table(lang)]
            # Stored under a country's code (gr): the language's (el).
            renamed = {lang: lang_to_table(lang) for lang in langs
                       if lang.lower() in COUNTRY_CODES}

            # Keeping tids keeps the import order that equal matches are
            # returned in; the snapshot's physical order is deploy_db's.
            cursor.execute("""SELECT COUNT(*) = 2 FROM information_schema.columns
                WHERE table_name = 'targets' AND column_name = 'tid'
                AND table_schema IN (current_schema(), %s)""", (snapshot,))
            keep_tid = cursor.fetchone()[0]
            columns = "tid, sid, text, lang" if keep_tid else "sid, text, lang"
            # A plural's forms, from a snapshot made since targets had them.
            cursor.execute("""SELECT COUNT(*) = 2 FROM information_schema.columns
                WHERE table_name = 'targets' AND column_name = 'forms'
                AND table_schema IN (current_schema(), %s)""", (snapshot,))
            keep_forms = cursor.fetchone()[0]
            # A KDE transcript's translation before it (without_transcript());
            # one with none isn't loaded, and duplicates fall to the index.
            transcript = "strpos(t.text, '%s') > 0 AND strpos(s.text, '%s') = 0" % (
                TRANSCRIPT, TRANSCRIPT)
            text = "CASE WHEN %s THEN split_part(t.text, '%s', 1) ELSE t.text END" % (
                transcript, TRANSCRIPT)
            picked = ("t.tid, t.sid, " if keep_tid else "t.sid, ") + text + " AS text" + \
                ", COALESCE(%s::jsonb ->> t.lang, t.lang) AS lang"
            if keep_forms:
                columns += ", forms, nplurals"
                picked += ", t.forms, t.nplurals"
            loaded = "t.lang = ANY(%%s) AND NOT (%s AND btrim(%s) = '')" % (transcript, text)
            cursor.execute(sql.SQL("""INSERT INTO targets (%s)
                SELECT %s FROM {} t JOIN sources s ON s.sid = t.sid
                WHERE octet_length(%s) <= %%s AND %s
                ON CONFLICT DO NOTHING""" % (columns, picked, text, loaded)).format(snap_targets),
                (json.dumps(renamed), SAFE_INDEX_BYTES, langs))
            cursor.execute(sql.SQL("""SELECT %s FROM {} t
                JOIN sources s ON s.sid = t.sid
                WHERE octet_length(%s) > %%s AND %s""" % (picked, text, loaded)).format(
                snap_targets), (json.dumps(renamed), SAFE_INDEX_BYTES, langs))
            insert = "INSERT INTO targets (%s) VALUES (%s) ON CONFLICT DO NOTHING" % (
                columns, ", ".join(["%s"] * len(columns.split(", "))))
            for row in cursor.fetchall():
                if self._indexable_string(row['text']):
                    _insert_indexable(cursor, insert, tuple(row), row['text'])
            condition, params = _foreign_letters_sql()
            if condition:
                cursor.execute("DELETE FROM targets t USING sources s WHERE s.sid = t.sid AND (%s)"
                               % condition, params)
            if keep_tid:
                cursor.execute("""SELECT setval(pg_get_serial_sequence('targets', 'tid'),
                    COALESCE(MAX(tid), 0) + 1, false) FROM targets""")

            cursor.execute(sql.SQL("""SELECT
                (SELECT COUNT(*) FROM sources), (SELECT COUNT(*) FROM {})""").format(
                snap_sources))
            sources, snapshot_sources = cursor.fetchone()
            cursor.execute("SELECT lang, COUNT(*) FROM targets GROUP BY lang")
            loaded_by_lang = dict(cursor.fetchall())
            per_lang = {}
            for lang, total in totals:
                if lang in langs:
                    lang = renamed.get(lang, lang)
                    per_lang[lang] = per_lang.get(lang, 0) + total
            per_lang = [(lang, total, loaded_by_lang.get(lang, 0))
                        for lang, total in sorted(per_lang.items())]
            iids = []
            for lang, total, loaded in per_lang:
                cursor.execute("""INSERT INTO imports
                    (block, filename, lang, licence, units, skipped)
                    VALUES (%s, %s, %s, %s, %s, %s) RETURNING iid""",
                    (block, snapshot, lang, licence, loaded, total - loaded))
                iids.append(cursor.fetchone()[0])
            if self._has_relation(cursor, 'target_imports'):
                # Every target came from the snapshot: link it to its language's import.
                cursor.execute("""INSERT INTO target_imports (tid, iid)
                    SELECT t.tid, i.iid FROM targets t JOIN imports i ON i.lang = t.lang
                    WHERE i.iid = ANY(%s)""", (iids,))
            cursor.connection.commit()
        except Exception:
            self.rollback(cursor.connection)
            raise
        targets = sum(row[2] for row in per_lang)
        return (sources, snapshot_sources - sources,
                targets, snapshot_targets - targets, left_out)

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
            self.rollback(cursor.connection)
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
            links = self._has_relation(cursor, 'target_imports')
            for sid, lang, text, fixed in targets:
                params = {'sid': sid, 'lang': lang, 'text': text, 'fixed': fixed}
                # Fixing in place keeps the target's imports.
                cursor.execute("""UPDATE targets SET text = %(fixed)s
                    WHERE sid = %(sid)s AND lang = %(lang)s AND text = %(text)s
                    AND NOT EXISTS (SELECT 1 FROM targets
                        WHERE sid = %(sid)s AND lang = %(lang)s AND text = %(fixed)s)""", params)
                if cursor.rowcount:
                    continue
                if links:
                    self._move_links(cursor, """SELECT t.tid AS old, u.tid AS new
                        FROM targets t JOIN targets u
                        ON u.sid = t.sid AND u.lang = t.lang AND u.text = %(fixed)s
                        WHERE t.sid = %(sid)s AND t.lang = %(lang)s AND t.text = %(text)s""",
                        params)
                cursor.execute("""DELETE FROM targets
                    WHERE sid = %(sid)s AND lang = %(lang)s AND text = %(text)s""", params)
            for sid, fixed in sources:
                cursor.execute("SELECT sid FROM sources WHERE text = %s", (fixed,))
                existing = cursor.fetchone()
                if existing:
                    # Move the targets to the existing source, dropping ones
                    # it already has.
                    if links:
                        self._move_links(cursor, """SELECT t.tid AS old, u.tid AS new
                            FROM targets t JOIN targets u
                            ON u.sid = %(new)s AND u.lang = t.lang AND u.text = t.text
                            WHERE t.sid = %(old)s""", {'old': sid, 'new': existing[0]})
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
            self.rollback(cursor.connection)
            raise
        current_app.cache.clear()
        return len(sources), len(targets), unconverted

    @staticmethod
    def _move_links(cursor, pairs, params):
        """Give each new target of the (old, new) tid pairs the old one's imports."""
        cursor.execute("""INSERT INTO target_imports (tid, iid)
            SELECT p.new, l.iid FROM (%s) p JOIN target_imports l ON l.tid = p.old
            ON CONFLICT DO NOTHING""" % pairs, params)
        cursor.execute("DELETE FROM target_imports WHERE tid IN (SELECT old FROM (%s) p)"
                       % pairs, params)

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
        # The longer bands' indexes hold the length too (btree_gin), so a
        # lookup's length range is applied in the index: half the sources
        # read (#3476). Not the shortest band's: its lengths are so common
        # that reading them costs more than it saves.
        banded = "length, vector" if self._btree_gin(cursor) else "vector"
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
                    CREATE INDEX {} ON sources USING gin(%s)
                    WHERE length BETWEEN %%s AND %%s""" % banded).format(idx_name),
                    (min_levenshtein_length(lower_bound, similarity),
                     max_levenshtein_length(upper_bound, similarity, max_length)))
            lower_bound = upper_bound + 1
        cursor.execute("""
            CREATE INDEX sources_long_idx ON sources USING gin(%s)
            WHERE length >= %%s""" % banded, (min_levenshtein_length(lower_bound, similarity),))
        # How many sources have each word, for lookups to match fewer of a
        # query's words (amagama.pruning); a word in fewer is rare.
        cursor.execute("""
            DROP TABLE IF EXISTS word_counts;
            CREATE TABLE word_counts (word text PRIMARY KEY, sources integer NOT NULL);
            INSERT INTO word_counts
                SELECT word, COUNT(*) FROM sources, UNNEST(TSVECTOR_TO_ARRAY(vector)) word
                GROUP BY word HAVING COUNT(*) >= %s""", (WORD_COUNT_MIN,))
        # The languages page reads these; finding them takes a scan of targets.
        cursor.execute("""
            DROP TABLE IF EXISTS target_languages;
            CREATE TABLE target_languages (lang text PRIMARY KEY);
            INSERT INTO target_languages SELECT DISTINCT lang FROM targets""")
        cursor.connection.commit()

    @staticmethod
    def _btree_gin(cursor):
        """Whether GIN indexes can hold a length (the btree_gin extension),
        installing it if need be; it's PostgreSQL's own, but may be missing
        or need rights this user hasn't."""
        cursor.execute("SAVEPOINT btree_gin")
        try:
            # In public, not the schema being deployed: dropping that would
            # take the extension, and every index using it, with it.
            cursor.execute("CREATE EXTENSION IF NOT EXISTS btree_gin SCHEMA public")
        except psycopg2.Error:
            cursor.execute("ROLLBACK TO SAVEPOINT btree_gin")
            return False
        cursor.execute("RELEASE SAVEPOINT btree_gin")
        return True

    def _has_target_languages(self, cursor):
        cursor.execute("SELECT to_regclass('target_languages') IS NOT NULL")
        return cursor.fetchone()[0]

    def _add_target_language(self, cursor, tlang):
        """Note a target language written to a deployed schema; the cached
        list is dropped once the write is committed."""
        if self._has_target_languages(cursor):
            cursor.execute("INSERT INTO target_languages VALUES (%s) ON CONFLICT DO NOTHING",
                           (tlang,))

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
            self.rollback(cursor.connection)
            raise
        self._available_langs = {}
        self._sources = None
        self._weights = {}
        self._licences = {}
        self._check_names = {}
        self._load_source_langs()
        current_app.cache.clear()

    @property
    def available_languages(self):
        if not self._available_langs:
            source_languages = list(self.source_langs)
            target_languages = set()

            # Get all the target languages: as deploy() stored them, or from
            # the targets in a schema that wasn't deployed.
            for slang in source_languages:
                cursor = self.get_cursor(self.schema(slang))
                if self._has_target_languages(cursor):
                    cursor.execute("SELECT lang FROM target_languages")
                else:
                    cursor.execute("SELECT DISTINCT lang FROM targets")
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

    @property
    def sources(self):
        """The imported blocks of each source language, by name."""
        if self._sources is None:
            sources = []
            for slang in sorted(self.source_langs):
                cursor = self.get_cursor(self.schema(slang))
                if not self._has_relation(cursor, 'imports'):
                    continue
                if self._has_weights(cursor):
                    titles = "b.title, b.version, b.weight"
                    join = "LEFT JOIN blocks b ON b.name = i.block"
                elif self._has_relation(cursor, 'blocks'):
                    titles = "b.title, b.version, NULL"
                    join = "LEFT JOIN blocks b ON b.name = i.block"
                else:
                    titles, join = "NULL, NULL, NULL", ""
                # A block's files share its licence; a mix of them is no licence.
                cursor.execute("""SELECT i.block, %s,
                        CASE WHEN COUNT(DISTINCT i.licence) = 1 THEN MAX(i.licence) END,
                        ARRAY_AGG(DISTINCT i.lang ORDER BY i.lang), SUM(i.units)::bigint, MAX(i.imported)
                    FROM imports i %s WHERE i.block IS NOT NULL
                    GROUP BY 1, 2, 3, 4 ORDER BY 1""" % (titles, join))
                for name, title, version, weight, licence, langs, units, imported in \
                        cursor.fetchall():
                    sources.append({
                        'name': name,
                        'title': title or name,
                        'version': version,
                        'weight': 1.0 if weight is None else weight,
                        'licence': licence,
                        'sourceLanguage': slang,
                        'targetLanguages': langs,
                        'units': units,
                        'imported': imported.astimezone(datetime.timezone.utc)
                                            .strftime('%Y-%m-%dT%H:%M:%SZ'),
                    })
            self._sources = sources
        return self._sources

    def _has_weights(self, cursor):
        cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = 'blocks'
            AND column_name = 'weight')""")
        return cursor.fetchone()[0]

    def block_weights(self, slang):
        """{block name: default weight} of the slang blocks weighted other than 1."""
        if slang not in self._weights:
            cursor = self.get_cursor(self.schema(slang))
            weights = {}
            if self._has_weights(cursor):
                cursor.execute("SELECT name, weight FROM blocks WHERE weight <> 1")
                weights = dict(cursor.fetchall())
            self._weights[slang] = weights
        return self._weights[slang]

    def block_licences(self, slang):
        """{block name: licence} of the slang blocks; None for a mix of licences."""
        if slang not in self._licences:
            cursor = self.get_cursor(self.schema(slang))
            licences = {}
            if self._has_relation(cursor, 'imports'):
                cursor.execute("""SELECT block,
                        CASE WHEN COUNT(DISTINCT licence) = 1 THEN MAX(licence) END
                    FROM imports WHERE block IS NOT NULL GROUP BY block""")
                licences = dict(cursor.fetchall())
            self._licences[slang] = licences
        return self._licences[slang]

    def check_names(self, slang):
        """{bit: check name} of the checks the slang rebuild flagged targets with."""
        if slang not in self._check_names:
            cursor = self.get_cursor(self.schema(slang))
            names = {}
            if self._has_relation(cursor, 'check_names'):
                cursor.execute("SELECT bit, name FROM check_names WHERE NOT left_out")
                names = dict(cursor.fetchall())
            self._check_names[slang] = names
        return self._check_names[slang]

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
                 cursor=None, iid=None):
        """Insert unit in the database, linked to import iid; 1 if stored."""
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
            if self.get_all_sids([unitdict], source_lang, None):
                # The source is too long for the index.
                return 0
            iid, unique = self._link_mode(cursor, iid)
            added = self.add_dict(unitdict, cursor, iid, unique)
            if added:
                self._add_target_language(cursor, tlang)

            if commit:
                cursor.connection.commit()
        except Exception:
            self.rollback(cursor.connection)
            raise
        if added:
            self._available_langs = {}
        return int(bool(added))

    def add_dict(self, unit, cursor, iid=None, unique=True, forms=False):
        """Insert units represented as dictionaries in database.

        With iid, link the target to that import; unique says whether
        targets has its unique index, which a deployed schema doesn't; forms
        whether it has a plural's forms (unit's forms and nplurals). The
        caller is expected to handle errors.
        """
        unit['sid'] = self.get_sid(unit, cursor)
        columns, values = ((", forms, nplurals", ", %(forms)s, %(nplurals)s")
                           if forms and unit.get('forms') else ("", ""))
        if iid is None:
            # ON CONFLICT covers a parallel import adding the same target; NOT
            # EXISTS a deployed schema, which has no unique index.
            return _insert_indexable(cursor, """INSERT INTO targets (sid, text, lang%s)
                SELECT %%(sid)s, %%(target)s, %%(target_lang)s%s
                WHERE NOT EXISTS (SELECT 1 FROM targets
                    WHERE sid = %%(sid)s AND lang = %%(target_lang)s AND text = %%(target)s)
                ON CONFLICT DO NOTHING""" % (columns, values), unit, unit['target'])
        if unique:
            # Unlike DO NOTHING, DO UPDATE returns the tid of a target that
            # is already there, even one a parallel import just added.
            # A plural's forms fill a row that has none (an ordinary
            # translation with the same text imported first).
            filled = (", forms = COALESCE(targets.forms, EXCLUDED.forms), "
                      "nplurals = COALESCE(targets.nplurals, EXCLUDED.nplurals)" if columns else "")
            row = _insert_indexable(cursor, """INSERT INTO targets (sid, text, lang%s)
                VALUES (%%(sid)s, %%(target)s, %%(target_lang)s%s)
                ON CONFLICT (sid, text, lang) DO UPDATE SET sid = EXCLUDED.sid%s
                RETURNING tid""" % (columns, values, filled), unit, unit['target'],
                returning=True)
            if not row:
                return False
        else:
            cursor.execute("""SELECT tid FROM targets
                WHERE sid = %(sid)s AND lang = %(target_lang)s AND text = %(target)s
                LIMIT 1""", unit)
            row = cursor.fetchone()
            if row is None:
                cursor.execute("""INSERT INTO targets (sid, text, lang%s)
                    VALUES (%%(sid)s, %%(target)s, %%(target_lang)s%s) RETURNING tid"""
                               % (columns, values), unit)
                row = cursor.fetchone()
            elif columns:
                cursor.execute("""UPDATE targets SET forms = %(forms)s, nplurals = %(nplurals)s
                    WHERE sid = %(sid)s AND lang = %(target_lang)s AND tid = %(tid)s
                    AND forms IS NULL""", dict(unit, tid=row[0]))
        cursor.execute("""INSERT INTO target_imports (tid, iid) VALUES (%s, %s)
            ON CONFLICT DO NOTHING""", (row[0], iid))
        return True

    def _add_batch(self, units, cursor, iid, forms=False):
        """add_dict() for units in a few statements, not a few each: targets
        too short to risk the index limit, linked to import iid, in a schema
        with its unique index (and a plural's forms if forms). Returns how
        many units were stored."""
        if not units:
            return 0
        sids = self._sids(cursor, {unit['source'] for unit in units},
                          self.schema(units[0]['source_lang']))
        # One row per target (an INSERT can't update one twice), a plural's
        # if any of them is.
        by_target = {}
        for unit in units:
            key = (sids[unit['source']], unit['target'], unit['target_lang'])
            plural = (tuple(unit['forms']), unit.get('nplurals')) if forms and unit.get('forms') \
                else (None, None)
            if key not in by_target or by_target[key][0] is None:
                by_target[key] = plural
        rows = sorted((key + plural for key, plural in by_target.items()),
                      key=lambda row: (row[1], row[0]))
        # A target already there isn't written again (DO UPDATE would make a
        # new row version for each repeat): its tid is read instead.
        if forms:
            # A plural's forms fill a row that has none; other rows aren't
            # written again.
            added = execute_values(cursor, """INSERT INTO targets (sid, text, lang, forms, nplurals)
                VALUES %s ON CONFLICT (sid, text, lang) DO UPDATE
                SET forms = EXCLUDED.forms, nplurals = EXCLUDED.nplurals
                WHERE targets.forms IS NULL AND EXCLUDED.forms IS NOT NULL
                RETURNING sid, text, lang, tid""",
                [row[:3] + (list(row[3]) if row[3] else None, row[4]) for row in rows],
                template="(%s, %s, %s, %s::text[], %s)", page_size=INSERT_PAGE, fetch=True)
        else:
            added = execute_values(cursor, """INSERT INTO targets (sid, text, lang) VALUES %s
                ON CONFLICT (sid, text, lang) DO NOTHING
                RETURNING sid, text, lang, tid""", [row[:3] for row in rows],
                page_size=INSERT_PAGE, fetch=True)
        tids = {row[3] for row in added}
        there = sorted({row[:3] for row in rows} - {(row[0], row[1], row[2]) for row in added})
        if there:
            tids.update(row[0] for row in execute_values(cursor, """SELECT t.tid
                FROM targets t JOIN (VALUES %s) AS v (sid, text, lang)
                ON t.sid = v.sid AND t.text = v.text AND t.lang = v.lang""",
                there, template="(%s::integer, %s::text, %s::text)",
                page_size=INSERT_PAGE, fetch=True))
        execute_values(cursor, """INSERT INTO target_imports (tid, iid) VALUES %s
            ON CONFLICT DO NOTHING""", sorted((tid, iid) for tid in tids),
            page_size=INSERT_PAGE)
        return len(units)

    @staticmethod
    def _sids(cursor, sources, schema):
        """{source: sid} for sources, which get_all_sids() stored: from the
        cache, and the rest in one query."""
        cached = current_app.cache.get_dict(*(build_cache_key(s, schema) for s in sources))
        sids = {split_cache_key(key): sid for key, sid in cached.items() if sid}
        missing = tuple(sources - set(sids))
        if missing:
            cursor.execute("SELECT text, sid FROM sources WHERE text IN %s", (missing,))
            sids.update(cursor.fetchall())
        if len(sids) < len(sources):
            raise Exception("sid not found although it should have existed")
        return sids

    def get_all_sids(self, units, source_lang, project_style):
        """Ensure that all source strings are in the database+cache.

        Returns the sources too long for the index, which aren't stored.
        """
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
            return set()

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
            insert = """INSERT INTO sources (text, vector, length)
                VALUES (%(source)s, TO_TSVECTOR(%(lang_config)s, %(indexed_source)s), %(length)s)
                ON CONFLICT DO NOTHING"""
            execute_values(cursor, """INSERT INTO sources (text, vector, length) VALUES %s
                ON CONFLICT DO NOTHING""", [p for p in params if not _long(p["source"])],
                template="(%(source)s, TO_TSVECTOR(%(lang_config)s, %(indexed_source)s), %(length)s)",
                page_size=INSERT_PAGE)
            rejected = {p["source"] for p in params if _long(p["source"])
                        and not _insert_indexable(cursor, insert, p, p["source"])}
            cursor.execute(select_query, {"list": tuple(to_store)})
            already_stored.update(cursor.fetchall())
        else:
            rejected = set()

        current_app.cache.set_many({
                build_cache_key(k, schema): v
                for (k, v) in already_stored.items()
        })
        return rejected

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
        # with compression. The text is measured as add_store() stores it.
        if len(s) < 1000:
            return True
        text = _encodable(s)
        return text is not None and len(compress(text.encode('utf-8'))) < COMPRESSED_LIMIT

    @staticmethod
    def _translated(u):
        # Translate Toolkit counts an XLIFF target as translated only with
        # approved="yes"; state="translated", "final" or "signed-off" means it too.
        if isinstance(u, xliff.xliffunit):
            return bool(u.target) and u.get_state_n() >= u.S_UNREVIEWED
        return u.istranslated()

    @staticmethod
    def _has_forms(cursor):
        """Whether targets, in the current schema, can hold a plural's forms
        (one made before #3351 can't)."""
        cursor.execute("""SELECT EXISTS(SELECT 1 FROM pg_attribute
            WHERE attrelid = to_regclass('targets') AND attname = 'forms' AND NOT attisdropped)""")
        return cursor.fetchone()[0]

    def _plural_translated(self, unit):
        """Whether a plural unit's forms are translations: not fuzzy (and an
        XLIFF one's state as _translated() asks). Its first form may be empty
        while others aren't: each form is then taken or not on its own."""
        if isinstance(unit, xliff.xliffunit):
            return unit.get_state_n() >= unit.S_UNREVIEWED
        return not unit.isfuzzy()

    def _usable_unit(self, u, target_lang=None):
        """Whether to import u. With target_lang, a copied_source() isn't,
        unless target_lang is a variant of English."""
        try:
            target = u.target
        except ValueError:
            # A Qt TS plural with no forms: Translate Toolkit can't make its target.
            return False
        plain = without_transcript(str(u.source), str(target))
        if plain != str(target) and not plain.strip():
            # Only a transcript: no translation before it.
            return False
        target = plain
        if target_lang and (lang_to_table(target_lang) or "").split("_")[0] != "en" \
                and copied_source(str(u.source), target):
            return False
        if target_lang and foreign_letters(target_lang, str(u.source), target):
            return False
        return u.istranslatable() and \
               bool(u.source) and \
               str(u.source) not in TRANSLATOR_CREDITS and \
               self._translated(u) and \
               len(u.source) <= self.max_import_len and \
               self._indexable_string(u.source) and \
               self._indexable_string(target) and \
               _encodable(str(u.source)) is not None and \
               _encodable(target) is not None

    def add_store(self, store, source_lang, target_lang, project_style=None,
                  commit=True, iid=None):
        """Insert all units in store in database.

        A unit whose text can't be stored even with its surrogate pairs
        joined is left out.
        """
        units = []
        plural_rule = plurals.rule(store, target_lang)
        # Plurals stored as two pairs: the second isn't one of the file's
        # units, for the importer's skipped count.
        store.plural_pairs = 0
        for unit in store.units:
            expanded = plurals.entries(unit, plural_rule)
            if expanded is None:
                pairs, forms, nplurals = [unit], None, None
            elif not (unit.istranslatable() and self._plural_translated(unit)):
                continue
            else:
                # A plural: its singular and its plural, each checked as any
                # translation is, both with all the forms.
                found, forms, nplurals = expanded
                if not found:
                    # Neither form its rule picks is in the file.
                    continue
                pairs = []
                for source, target in found:
                    pairs.append(TranslationUnit(source))
                    pairs[-1].target = target
                # As the targets are stored: without a KDE transcript.
                forms = [_encodable(without_transcript(found[0][0], form)) for form in forms]
                if None in forms:
                    forms = nplurals = None
            stored = 0
            for u in pairs:
                if not self._usable_unit(u, target_lang):
                    continue
                source = _encodable(str(u.source))
                target = _encodable(without_transcript(str(u.source), str(u.target)))
                if source is not None and target is not None:
                    units.append({'source': source, 'target': target, 'forms': forms,
                                  'nplurals': nplurals})
                    stored += 1
            store.plural_pairs += max(stored - 1, 0)

        if not units:
            return 0
        return self.add_list(units, source_lang, target_lang, project_style,
                             commit, iid)

    def add_list(self, units, source_lang, target_lang, project_style=None,
                 commit=True, iid=None):
        """Insert all units in list into the database, linked to import iid.

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

        too_long = self.get_all_sids(units, source_lang, project_style)

        cursor = self.get_cursor(self.schema(slang))
        try:
            iid, unique = self._link_mode(cursor, iid)
            forms = self._has_forms(cursor)
            # We sort to avoid deadlocks during parallel import.
            units.sort(key=lambda x: x['target'])
            count = 0
            batch = []
            for unit in units:
                unit.update({
                    'source_lang': slang,
                    'target_lang': tlang,
                    'lang_config': lang_config,
                })
                unit.setdefault('forms', None)
                unit.setdefault('nplurals', None)
                if unit['source'] in too_long:
                    continue
                if iid is not None and unique and not _long(unit['target']):
                    batch.append(unit)
                elif self.add_dict(unit, cursor, iid, unique, forms):
                    count += 1
            count += self._add_batch(batch, cursor, iid, forms)
            if count:
                self._add_target_language(cursor, tlang)
            if commit:
                cursor.connection.commit()
        except Exception:
            self.rollback(cursor.connection)
            raise

        if count:
            self._available_langs = {}
            self._sources = None
            self._weights = {}
            self._licences = {}
        self._check_names = {}
        return count

    @property
    def comparer(self):
        if not hasattr(self, '_comparer'):
            max_length = current_app.config.get('MAX_LENGTH', 2000)
            self._comparer = LevenshteinComparer(max_length)
        return self._comparer

    def _translate_query(self, cursor, slang, tlang, query, min_len, max_len, min_rank):
        prepared = self._prepared_statements.setdefault(cursor.connection, {})
        if slang not in prepared:
            cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = 'targets'
                AND column_name = 'tid')""")
            if cursor.fetchone()[0]:
                names = (("lookup", self.PREPARE_LOOKUP),
                         ("lookup_pruned", self.PREPARE_LOOKUP_PRUNED))
            else:
                names = (("lookup_without_tid", self.PREPARE_LOOKUP_WITHOUT_TID),
                         ("lookup_pruned_without_tid", self.PREPARE_LOOKUP_PRUNED_WITHOUT_TID))
            # Data deployed before word_counts was made isn't pruned.
            cursor.execute("SELECT to_regclass('word_counts') IS NOT NULL")
            if not cursor.fetchone()[0]:
                names = names[:1]
            for name, statement in names:
                if not self.prepared_statement_exists(name):
                    cursor.execute(statement)
            prepared[slang] = [name for name, _ in names]
        lang_config = lang_to_config(slang)
        names = prepared[slang]
        pruned = None
        # Fewer words than pruning.FEWEST_WORDS make fewer query words: nothing
        # to prune, and no need to ask.
        if len(names) > 1 and len(re.findall(r"\w+", query)) >= pruning.FEWEST_WORDS:
            cursor.execute(self.QUERY_WORDS, {"config": lang_config, "text": query})
            text, counts = cursor.fetchone()
            if text:
                pruned = pruning.lookup_filter(text, counts, min_rank)
        if pruned:
            cursor.execute(sql.SQL("EXECUTE {} (%s, %s, %s, %s, %s, %s, %s)").format(
                           sql.Identifier(names[1])),
                           (lang_config, query, tlang, min_len, max_len, min_rank, pruned))
        else:
            cursor.execute(sql.SQL("EXECUTE {} (%s, %s, %s, %s, %s, %s)").format(
                           sql.Identifier(names[0])),
                           (lang_config, query, tlang, min_len, max_len, min_rank))

    def translate_unit(self, unit_source, source_lang, target_lang,
                       project_style=None, min_similarity=None,
                       max_candidates=None, source_options=None):
        """Return TM suggestions for unit_source, chosen by source_options."""
        slang = lang_to_table(source_lang)
        if slang not in self.source_langs:
            abort(404)

        tlang = lang_to_table(target_lang)
        if slang == tlang:
            abort(400)

        if isinstance(unit_source, bytes):
            unit_source = str(unit_source, "utf-8")
        # PostgreSQL text can't hold NUL, so no source has one.
        if "\x00" in unit_source:
            abort(400)

        checker = project_checker(project_style, source_lang)

        max_length = current_app.config.get('MAX_LENGTH', 2000)
        min_similarity = max(min_similarity or current_app.config.get('MIN_SIMILARITY', 70), 70)
        if not max_candidates or max_candidates < 1:
            max_candidates = current_app.config.get('MAX_CANDIDATES', 5)
        max_candidates = min(max_candidates, 30)

        source_len = len(unit_source)
        minlen = min_levenshtein_length(source_len, min_similarity)
        maxlen = max_levenshtein_length(source_len, min_similarity, max_length)

        minrank = max(min_similarity / 2, 30)

        cursor = self.get_cursor(self.schema(slang))
        self._translate_query(cursor, slang, tlang, indexing_version(unit_source, checker),
                              minlen, maxlen, minrank)

        results = []
        similarity = self.comparer.similarity
        for row in cursor:
            quality = similarity(unit_source, row['source'], min_similarity)
            if quality >= min_similarity:
                result = dict(row)
                result['quality'] = quality
                results.append(result)
        # Choosing needs the sources of every match, not just the best.
        self._add_forms(cursor, slang, tlang, results)
        self._add_sources(cursor, tlang, results, self.check_names(slang))
        source_options = source_options or SourceOptions()
        licences = self.block_licences(slang) if source_options.licences else None
        return source_options.choose(results, max_candidates, self.block_weights(slang), licences)

    def _add_forms(self, cursor, slang, tlang, results):
        """Give each result that is a plural its forms and nplurals."""
        known = self._forms_known.setdefault(cursor.connection, {})
        schema = self.schema(slang)
        if schema not in known:
            known[schema] = self._has_forms(cursor)
        if not results or not known[schema]:
            return
        cursor.execute("""SELECT sid, text, forms, nplurals FROM targets
            WHERE lang = %s AND sid = ANY(%s) AND forms IS NOT NULL""",
            (tlang, [result['sid'] for result in results]))
        found = {(sid, text): (forms, nplurals) for sid, text, forms, nplurals in cursor.fetchall()}
        for result in results:
            plural = found.get((result['sid'], result['target']))
            if plural:
                result['forms'], result['nplurals'] = plural

    def _add_sources(self, cursor, tlang, results, check_names=None):
        """Give each result the names of the blocks it was imported from, and
        of the check_names ({bit: name}) it is flagged with."""
        blocks, flags = {}, {}
        if results and self._has_relation(cursor, 'target_imports'):
            cursor.execute("""SELECT t.sid, t.text, ARRAY_AGG(DISTINCT i.block ORDER BY i.block),
                    %s
                FROM targets t JOIN target_imports l ON l.tid = t.tid
                JOIN imports i ON i.iid = l.iid
                WHERE t.lang = %%s AND t.sid = ANY(%%s) AND i.block IS NOT NULL
                GROUP BY t.sid, t.text""" % ("BIT_OR(t.checks)" if check_names else "NULL"),
                (tlang, [result['sid'] for result in results]))
            for sid, text, names, mask in cursor.fetchall():
                blocks[(sid, text)], flags[(sid, text)] = names, mask or 0
        for result in results:
            key = (result.pop('sid'), result['target'])
            result['sources'] = blocks.get(key, [])
            result['checks'] = [name for bit, name in sorted((check_names or {}).items())
                                if flags.get(key, 0) >> bit & 1]


def min_levenshtein_length(length, min_similarity):
    # This should return an integer, to avoid using a float in the SQL, which
    # produces slightly less optimal query plans. In Python 2, math.ceil
    # returns a float. Never above the length itself, or a one-character
    # source can't match.
    return int(math.ceil(min(max(length * (min_similarity/100.0), 2), length)))


def max_levenshtein_length(length, min_similarity, max_length):
    # This should return an integer, to avoid using a float in the SQL, which
    # produces slightly less optimal query plans. In Python 2, math.floor
    # returns a float.
    return int(math.floor(min(length / (min_similarity/100.0), max_length)))
