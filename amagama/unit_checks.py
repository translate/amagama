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
Rebuilds leave out the targets failing FILTER.
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

# printf conversions (%s, %1$s, %(name)s, with flags, width and length), or
# KDE's and Qt's numbered %1 to %99.
_PRINTF = re.compile(
    r"%(?:(\d+)\$|\((\w+)\))?[-+#0']*(\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|L|q|j|z|t|I)*([diouxXeEfFgGaAcspn])"
    r"|%(\d{1,2})(?![\d$])")
# LibreOffice's %PRODUCTNAME and the like.
_VARIABLE = re.compile(r"%[A-Z][A-Z_]{3,}")
# A date and time format, whose fields are reordered as the language needs.
_STRFTIME = re.compile(r"%[-_0^#]?[HMSYyBbPIklZjUWVCRT]")
# Placeholders in braces: Moodle's {$a}, numbered {0}, empty {} and Ruby's
# %{name}. Other braces are often text to translate.
_BRACES = re.compile(r"%\{\w+\}|\{\$[^{}]*\}|\{\d+\}|\{\}")
# TeX, whose braces hold text.
_TEX = re.compile(r"\\[A-Za-z]+")
# Tag names only: an attribute such as alt may be translated.
_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)(?:\s[^<>]*?)?(/?)>")
_WORD = re.compile(r"[^\W\d_]{2,}")
# Markup whose letters aren't the language's: printf, braces, tags, entities.
_MARKUP = re.compile(r"%(?:\d+\$|\([^)]*\))?[-+ #0-9.]*[a-zA-Z]|\{[^{}]*\}|<[^<>]*>|&\w+;")
# A sentence, not a name or a single term.
UNCHANGED_MIN_WORDS = 3

# A script under this share of a language's targets is one it doesn't use,
# judged in languages with at least SCRIPT_MIN_TARGETS of them.
SCRIPT_MIN_SHARE = 0.01
SCRIPT_MIN_TARGETS = 1000
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


_escapes = _failed("escapes")


def printf_placeholders(text):
    """(placeholders, widened) of text: each placeholder (numbered %1 as
    itself, a conversion as its position or name and type, variables as
    they are), and those with a width."""
    found, widened = collections.Counter(), set()
    text = text.replace("%%", "")
    for variable in _VARIABLE.findall(text):
        found[variable] += 1
    for position, name, width, conversion, numbered in _PRINTF.findall(_VARIABLE.sub("", text)):
        key = "%" + numbered if numbered else "%%%s%s" % (position or name, conversion)
        found[key] += 1
        if width:
            widened.add(key)
    return found, widened


def _printf_differs(source, target):
    if _STRFTIME.search(source):
        return False
    wanted, _ = printf_placeholders(source)
    found, widened = printf_placeholders(target)
    numbered = any(key[1:].isdigit() for key in wanted)
    for key in list(found):
        # Not the source's kind of placeholder: Basque and Turkish write
        # percentages as %95 or %50.
        if key not in wanted and ((key[1:].isdigit() and not numbered) or key in widened):
            del found[key]
    return found != wanted


def _braces_differ(source, target):
    return not _TEX.search(source) and \
        sorted(_BRACES.findall(source)) != sorted(_BRACES.findall(target))


def tags(text):
    """text's tags, as /name for a closing one and name/ for an empty one."""
    return sorted(close + name + empty for close, name, empty in _TAG.findall(text))


def _unchanged(source, target, lang):
    return (target == source and not lang.split("_")[0] == "en"
            and len(_WORD.findall(source)) >= UNCHANGED_MIN_WORDS)


CHECKS = {
    "printf": lambda source, target, lang: _printf_differs(source, target),
    "braces": lambda source, target, lang: _braces_differ(source, target),
    "tags": lambda source, target, lang: tags(source) != tags(target),
    "escapes": lambda source, target, lang: _escapes(source, target),
    "unchanged": _unchanged,
}
# Measured against the language's other targets.
MEASURES = ("script", "length")
# Checks whose failures rebuilds leave out: placeholders that don't match the
# source, and text in a script the language doesn't use.
FILTER = ("printf", "braces", "script")


def failed_checks(source, target, lang, names=None):
    """The names of the CHECKS (or of names) target fails."""
    return [name for name, check in CHECKS.items()
            if (names is None or name in names) and check(source, target, lang)]


def _scripts(text):
    counts = collections.Counter()
    for char in _MARKUP.sub("", text):
        if char.isalpha():
            word = unicodedata.name(char, "").split(" ", 1)[0]
            if word and word not in _NOT_SCRIPTS:
                counts[word] += 1
    return counts


def script(text):
    """The script most of text's letters are in, by Unicode name (LATIN,
    CYRILLIC, CJK, ...), or None without letters."""
    counts = _scripts(text)
    return counts.most_common(1)[0][0] if counts else None


def target_script(source, target, home):
    """target's script, or None if it has no letters or it is a script other
    than the source language's (home) that the source has too: a name or
    symbol kept as it is."""
    found = script(target)
    if found is None or (found != home and found in _scripts(source)):
        return None
    return found


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

    def lines(self, names, top=10):
        """The report on names, as lines of text."""
        for name in names:
            failures = self.failures(name)
            yield "%s: %d (%.2f%%)" % (name, failures, 100 * failures / (self.targets or 1))
            if not failures:
                continue
            yield "  Languages: " + ", ".join(
                "%s %d (%.1f%%)" % (lang, n, 100 * n / self.languages[lang])
                for lang, n in self.by_language[name].most_common(top))
            if self.by_block[name]:
                yield "  Blocks: " + ", ".join(
                    "%s %d" % item for item in self.by_block[name].most_common(top))
            for lang, source, target, blocks in self.examples[name]:
                yield "  [%s] %r -> %r%s" % (
                    lang, source, target, " (%s)" % ", ".join(blocks) if blocks else "")

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


def check_units(connection, schema, langs=(), examples=3, names=None):
    """A Report on the targets in schema, in langs or all of its languages,
    for the CHECKS and MEASURES in names (None: all).

    The first pass finds each language's usual scripts and length; the
    second runs the checks.
    """
    names = set(CHECKS) | set(MEASURES) if names is None else set(names)
    # Targets are counted by script given the sources' own script, as far as
    # the pass has seen them.
    homes = collections.Counter()
    scripts = collections.defaultdict(collections.Counter)
    steps = collections.defaultdict(collections.Counter)
    if names & set(MEASURES):
        for lang, _, source, target in _rows(connection, schema, langs):
            if "script" in names:
                homes[script(source)] += 1
                scripts[lang][target_script(source, target, homes.most_common(1)[0][0])] += 1
            if "length" in names:
                steps[lang][length_step(source, target)] += 1
    homes.pop(None, None)
    home = homes.most_common(1)[0][0] if homes else None
    rare = {}
    for lang, counter in scripts.items():
        counter.pop(None, None)
        judged = counter.total()
        rare[lang] = {name for name, n in counter.items()
                      if judged >= SCRIPT_MIN_TARGETS and n < SCRIPT_MIN_SHARE * judged}
    usual = {}
    for lang, counter in steps.items():
        counter.pop(None, None)
        usual[lang] = counter.most_common(1)[0][0] if counter else None

    report = Report(examples)
    for lang, tid, source, target in _rows(connection, schema, langs):
        report.targets += 1
        report.languages[lang] += 1
        failed = failed_checks(source, target, lang, names)
        if "script" in names and target_script(source, target, home) in rare.get(lang, ()):
            failed.append("script")
        step = length_step(source, target) if "length" in names else None
        if step is not None and abs(step - usual[lang]) >= LENGTH_MAX_STEPS:
            failed.append("length")
        for name in failed:
            report.add(name, lang, tid, source, target)
    with connection.cursor() as cursor:
        if _has_imports(cursor, schema):
            report.count_blocks(cursor, schema)
        else:
            for examples in report.examples.values():
                for example in examples:
                    example[3] = []
    # Nothing is written.
    connection.rollback()
    return report


def _has_imports(cursor, schema):
    cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = %s AND table_name = 'target_imports')""", (schema,))
    return cursor.fetchone()[0]


def reject(connection, schema, examples=3):
    """Delete the targets in schema that fail FILTER; a Report on them."""
    report = check_units(connection, schema, examples=examples, names=FILTER)
    tids = sorted({tid for name in FILTER for tid in report.tids[name]})
    if tids:
        with connection.cursor() as cursor:
            cursor.execute("CREATE TEMPORARY TABLE rejected (tid INTEGER) ON COMMIT DROP")
            for start in range(0, len(tids), 10000):
                cursor.execute("INSERT INTO rejected SELECT UNNEST(%s::INTEGER[])",
                               (tids[start:start + 10000],))
            if _has_imports(cursor, schema):
                cursor.execute(sql.SQL("""DELETE FROM {}.target_imports l USING rejected r
                    WHERE l.tid = r.tid""").format(sql.Identifier(schema)))
            cursor.execute(sql.SQL("DELETE FROM {}.targets t USING rejected r WHERE t.tid = r.tid")
                           .format(sql.Identifier(schema)))
        connection.commit()
    return report
