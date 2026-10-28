#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Find stored translations that look broken rather than styled differently.

A target fails a check when its printf or brace placeholders, XML tags or
escapes don't match the source's, or when it is a sentence left in English.
Two more are measured against the rest of the language's targets: a script
the language hardly uses, and a length far from the language's usual one.
"""

import array
import collections
import math
import random
import re
import unicodedata

from psycopg2 import sql
from translate.filters import checks

_checker = checks.StandardChecker()

# ICU message format nests translated text in braces.
_ICU = re.compile(r",\s*(?:plural|select|selectordinal)\s*,")
# Tag names only: an attribute such as alt may be translated.
_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)(?:\s[^<>]*?)?(/?)>")
_WORD = re.compile(r"[^\W\d_]{2,}")
# Markup whose letters aren't the language's: printf, braces, tags, entities.
_MARKUP = re.compile(r"%(?:\d+\$|\([^)]*\))?[-+ #0-9.]*[a-zA-Z]|\{[^{}]*\}|<[^<>]*>|&\w+;")
# A sentence, not a name or a single term.
UNCHANGED_MIN_WORDS = 3

# A script under this share of a language's targets is one it doesn't use.
SCRIPT_MIN_SHARE = 0.01
# Lengths are compared for sources at least this long, in powers of two of
# target length over source length: two from the language's usual is four
# times longer or shorter.
LENGTH_MIN_SOURCE = 20
LENGTH_MAX_STEPS = 2

# Unicode character name words that aren't a script.
_NOT_SCRIPTS = {"CIRCLED", "COMBINING", "FULLWIDTH", "HALFWIDTH", "MATHEMATICAL", "MODIFIER",
                "PARENTHESIZED", "SMALL", "SQUARED"}


def _failed(name):
    test = getattr(_checker, name)

    def failed(source, target):
        try:
            test(source, target)
        except checks.FilterFailure:
            return True
        return False
    return failed


_printf = _failed("printf")
_braces = _failed("pythonbraceformat")
_escapes = _failed("escapes")


def tags(text):
    """text's tags, as /name for a closing one and name/ for an empty one."""
    return sorted(close + name + empty for close, name, empty in _TAG.findall(text))


def _unchanged(source, target, lang):
    return (target == source and not lang.split("_")[0] == "en"
            and len(_WORD.findall(source)) >= UNCHANGED_MIN_WORDS)


CHECKS = {
    "printf": lambda source, target, lang: _printf(source, target),
    "braces": lambda source, target, lang: not _ICU.search(source) and _braces(source, target),
    "tags": lambda source, target, lang: tags(source) != tags(target),
    "escapes": lambda source, target, lang: _escapes(source, target),
    "unchanged": _unchanged,
}
# Measured against the language's other targets.
MEASURES = ("script", "length")


def failed_checks(source, target, lang):
    """The names of the CHECKS target fails."""
    return [name for name, check in CHECKS.items() if check(source, target, lang)]


def script(text):
    """The script most of text's letters are in, by Unicode name (LATIN,
    CYRILLIC, CJK, ...), or None without letters."""
    counts = collections.Counter()
    for char in _MARKUP.sub("", text):
        if char.isalpha():
            word = unicodedata.name(char, "").split(" ", 1)[0]
            if word and word not in _NOT_SCRIPTS:
                counts[word] += 1
    return counts.most_common(1)[0][0] if counts else None


def length_step(source, target):
    """log2 of target length over source length, rounded, or None for a
    short source or an empty target."""
    if len(source) < LENGTH_MIN_SOURCE or not target:
        return None
    return round(math.log2(len(target) / len(source)))


class Report(object):
    """What check_units found, by check: counts by language and by block,
    and examples as (lang, source, target, blocks), chosen at random."""

    def __init__(self, examples=3, rng=None):
        self.max_examples = examples
        self.rng = rng or random.Random()
        self.targets = 0
        self.languages = collections.Counter()
        self.by_language = collections.defaultdict(collections.Counter)
        self.by_block = collections.defaultdict(collections.Counter)
        self.examples = collections.defaultdict(list)
        # The failed targets of each check, for counting by block.
        self.tids = collections.defaultdict(lambda: array.array("q"))

    def failures(self, name):
        return sum(self.by_language[name].values())

    def add(self, name, lang, tid, source, target):
        self.by_language[name][lang] += 1
        self.tids[name].append(tid)
        # Each failure so far is equally likely to be an example.
        example = [lang, source, target, tid]
        if len(self.examples[name]) < self.max_examples:
            self.examples[name].append(example)
        else:
            n = self.rng.randrange(len(self.tids[name]))
            if n < self.max_examples:
                self.examples[name][n] = example

    def count_blocks(self, cursor, schema):
        """Count each check's failures by the blocks that supplied them."""
        query = sql.SQL("""SELECT i.block, COUNT(*) FROM {0}.target_imports l
            JOIN {0}.imports i ON i.iid = l.iid
            WHERE l.tid = ANY(%s) AND i.block IS NOT NULL GROUP BY i.block""").format(
            sql.Identifier(schema))
        for name, tids in self.tids.items():
            for start in range(0, len(tids), 10000):
                cursor.execute(query, (list(tids[start:start + 10000]),))
                self.by_block[name].update(dict(cursor.fetchall()))
        for examples in self.examples.values():
            for example in examples:
                cursor.execute(sql.SQL("""SELECT i.block FROM {0}.target_imports l
                    JOIN {0}.imports i ON i.iid = l.iid
                    WHERE l.tid = %s AND i.block IS NOT NULL ORDER BY i.block""").format(
                    sql.Identifier(schema)), (example[3],))
                example[3] = [block for block, in cursor.fetchall()]


def _rows(connection, schema, langs):
    """(lang, tid, source, target) of the targets in schema, in langs or all."""
    query = sql.SQL("""SELECT t.lang, t.tid, s.text, t.text FROM {0}.targets t
        JOIN {0}.sources s ON s.sid = t.sid""").format(sql.Identifier(schema))
    params = ()
    if langs:
        query += sql.SQL(" WHERE t.lang = ANY(%s)")
        params = (list(langs),)
    with connection.cursor(name="check_units") as rows:
        rows.itersize = 10000
        rows.execute(query, params)
        yield from rows


def check_units(connection, schema, langs=(), examples=3):
    """A Report on the targets in schema, in langs or all of its languages.

    The first pass finds each language's usual scripts and length; the
    second runs the checks.
    """
    scripts = collections.defaultdict(collections.Counter)
    steps = collections.defaultdict(collections.Counter)
    for lang, _, source, target in _rows(connection, schema, langs):
        scripts[lang][script(target)] += 1
        steps[lang][length_step(source, target)] += 1
    rare = {lang: {name for name, n in counter.items()
                   if name and n < SCRIPT_MIN_SHARE * counter.total()}
            for lang, counter in scripts.items()}
    usual = {}
    for lang, counter in steps.items():
        counter.pop(None, None)
        usual[lang] = counter.most_common(1)[0][0] if counter else None

    report = Report(examples)
    for lang, tid, source, target in _rows(connection, schema, langs):
        report.targets += 1
        report.languages[lang] += 1
        failed = failed_checks(source, target, lang)
        if script(target) in rare[lang]:
            failed.append("script")
        step = length_step(source, target)
        if step is not None and abs(step - usual[lang]) >= LENGTH_MAX_STEPS:
            failed.append("length")
        for name in failed:
            report.add(name, lang, tid, source, target)
    with connection.cursor() as cursor:
        cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.tables
            WHERE table_schema = %s AND table_name = 'target_imports')""", (schema,))
        if cursor.fetchone()[0]:
            report.count_blocks(cursor, schema)
        else:
            for examples in report.examples.values():
                for example in examples:
                    example[3] = []
    # Nothing is written.
    connection.rollback()
    return report
