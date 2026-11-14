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
translate-toolkit's checks (TTK_CHECKS) can run beside them, with the
checker of the project a target came from. Rebuilds leave out the targets
failing FILTER.
"""

import collections
import concurrent.futures
import functools
import io
import itertools
import math
import multiprocessing
import os
import random
import re
import unicodedata

from psycopg2 import sql
from translate.filters import checks
from translate.filters.checks import CheckerConfig, projectcheckers

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


# translate-toolkit's checks that aren't run: they need what amaGama hasn't
# got (a dictionary, a compendium, lists of words or characters), or a stored
# translation always passes them (it isn't empty, and credits aren't
# imported). Its printf, escapes and unchanged are left to amaGama's own.
TTK_LEFT_OUT = frozenset({
    "spellcheck", "compendiumconflicts", "musttranslatewords", "notranslatewords", "validchars",
    "untranslated", "blank", "credits",
})
# translate-toolkit's checks run by default.
TTK_CHECKS = tuple(sorted(set(_checker.defaultfilters) - TTK_LEFT_OUT - set(CHECKS)))
# Checks that are slow on long text, skipped where the source or the target is
# longer: translate-toolkit's sentence splitting is quadratic on text without
# a sentence break it recognises.
SLOW_CHECKS = {"sentencecount": 1000}
# A target's failures are a 64-bit mask.
MAX_CHECKS = 63
# Rows checked at a time in a worker process.
BATCH = 2000
# Failures copied to the database at a time.
COPY_BATCH = 10000


@functools.lru_cache(maxsize=1)
def ttk_names():
    """Every check of translate-toolkit's project checkers, but amaGama's own."""
    names = set()
    for checker in projectcheckers.values():
        names |= set(checker().defaultfilters)
    return names - set(CHECKS) - set(MEASURES)


def check_names(names):
    """names, in order: amaGama's checks and measures first, then
    translate-toolkit's. ValueError for a name that is neither."""
    names = list(dict.fromkeys(names))
    known = list(CHECKS) + list(MEASURES)
    unknown = [name for name in names if name not in known and name not in ttk_names()]
    if unknown:
        raise ValueError("Not checks: %s" % ", ".join(unknown))
    if len(names) > MAX_CHECKS:
        raise ValueError("At most %d checks can run at once." % MAX_CHECKS)
    return [name for name in known if name in names] + sorted(set(names) - set(known))


def failed_checks(source, target, lang, names=None):
    """The names of the CHECKS (or of names) target fails."""
    return [name for name, check in CHECKS.items()
            if (names is None or name in names) and check(source, target, lang)]


class _Unit(object):
    """What a translate-toolkit checker reads of a unit."""
    __slots__ = ("source", "target")

    def __init__(self, source, target):
        self.source, self.target = source, target

    def hasplural(self):
        return False

    def getlocations(self):
        return []


def _passed(name, source, target, error):
    # A check that breaks on odd text doesn't fail the translation.
    return True


class Checks(object):
    """The checks names (in check_names() order), each a bit of a mask.

    The script and length measures need the language statistics the first
    pass finds: the source language's script (home), each language's rare
    scripts and its usual length step.
    """

    def __init__(self, names, home=None, rare=None, usual=None):
        self.names = tuple(names)
        self.bits = {name: 1 << bit for bit, name in enumerate(self.names)}
        self.own = [name for name in self.names if name in CHECKS]
        self.ttk = [name for name in self.names if name not in CHECKS and name not in MEASURES]
        self.slow = {name: limit for name, limit in SLOW_CHECKS.items() if name in self.ttk}
        self.home, self.rare, self.usual = home, rare or {}, usual or {}
        self._checkers = {}

    def __getstate__(self):
        # Workers make their own checkers.
        return dict(self.__dict__, _checkers={})

    def _checker(self, style, lang, skipped):
        key = (style, lang, skipped)
        if key not in self._checkers:
            checker = projectcheckers.get(style or "standard", checks.StandardChecker)
            self._checkers[key] = checker(
                checkerconfig=CheckerConfig(targetlanguage=lang), errorhandler=_passed,
                limitfilters=[name for name in self.ttk if name not in skipped])
        return self._checkers[key]

    def mask(self, lang, style, source, target):
        """The mask of the checks target fails, with style's checker (None:
        the standard one) for translate-toolkit's."""
        mask = 0
        for name in self.own:
            if CHECKS[name](source, target, lang):
                mask |= self.bits[name]
        if "script" in self.bits and \
                target_script(source, target, self.home) in self.rare.get(lang, ()):
            mask |= self.bits["script"]
        if "length" in self.bits:
            step = length_step(source, target)
            if step is not None and abs(step - self.usual[lang]) >= LENGTH_MAX_STEPS:
                mask |= self.bits["length"]
        if self.ttk:
            longest = max(len(source), len(target))
            skipped = frozenset(name for name, limit in self.slow.items() if longest > limit)
            for name in self._checker(style, lang, skipped).run_filters(_Unit(source, target)):
                mask |= self.bits.get(name, 0)
        return mask

    def failed(self, mask):
        """The names of the checks in mask."""
        return [name for name in self.names if mask & self.bits[name]]


_worker_checks = None


def _start_worker(checks):
    global _worker_checks
    _worker_checks = checks


def _check_batch(batch):
    return [_worker_checks.mask(lang, style, source, target)
            for lang, _, source, target, style in batch]


def _batched(rows, size):
    batch = []
    for row in rows:
        batch.append(row)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def masks(rows, checks, workers=1):
    """(row, mask) for each (lang, tid, source, target, style) of rows, in
    order; with workers over 1, checked in that many processes, unless the
    rows fit one batch."""
    batches = _batched(rows, BATCH)
    first = next(batches, [])
    if workers <= 1 or len(first) < BATCH:
        rows = itertools.chain.from_iterable(itertools.chain([first], batches))
        for lang, tid, source, target, style in rows:
            yield (lang, tid, source, target, style), checks.mask(lang, style, source, target)
        return
    try:
        with concurrent.futures.ProcessPoolExecutor(
                workers, mp_context=multiprocessing.get_context("spawn"),
                initializer=_start_worker, initargs=(checks,)) as pool:
            # A few batches ahead of the results, never the whole table.
            pending = collections.deque()
            for batch in itertools.chain([first], batches):
                pending.append((batch, pool.submit(_check_batch, batch)))
                if len(pending) > 2 * workers:
                    batch, future = pending.popleft()
                    yield from zip(batch, future.result())
            while pending:
                batch, future = pending.popleft()
                yield from zip(batch, future.result())
    except concurrent.futures.process.BrokenProcessPool as e:
        raise RuntimeError("A check worker died, perhaps for lack of memory") from e


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
        # The targets a rebuild left out.
        self.left_out = 0
        # Failures so far, for choosing examples.
        self.seen = collections.Counter()

    def failures(self, name):
        return sum(self.by_language[name].values())

    def add(self, name, lang, tid, source, target):
        self.by_language[name][lang] += 1
        self.seen[name] += 1
        # Each failure so far is equally likely to be an example.
        example = [lang, source, target, tid]
        if len(self.examples[name]) < self.max_examples:
            self.examples[name].append(example)
        else:
            n = self.rng.randrange(self.seen[name])
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

    def count_blocks(self, cursor, schema, checks):
        """Count each check's failures (in the checked table) by the blocks
        that supplied them."""
        cursor.execute(sql.SQL("""SELECT i.block, b.bit, COUNT(DISTINCT c.tid) FROM checked c
            JOIN {0}.target_imports l ON l.tid = c.tid JOIN {0}.imports i ON i.iid = l.iid
            CROSS JOIN generate_series(0, %s) b(bit)
            WHERE i.block IS NOT NULL AND c.mask & (1::BIGINT << b.bit) <> 0
            GROUP BY 1, 2""").format(sql.Identifier(schema)), (len(checks.names) - 1,))
        for block, bit, n in cursor.fetchall():
            self.by_block[checks.names[bit]][block] += n
        for examples in self.examples.values():
            for example in examples:
                cursor.execute(sql.SQL("""SELECT DISTINCT i.block FROM {0}.target_imports l
                    JOIN {0}.imports i ON i.iid = l.iid
                    WHERE l.tid = %s AND i.block IS NOT NULL ORDER BY i.block""").format(
                    sql.Identifier(schema)), (example[3],))
                example[3] = [block for block, in cursor.fetchall()]


def _rows(connection, schema, langs, styled=False):
    """(lang, tid, source, target, style) of the targets in schema, in langs or
    all; style is that of the target_styles table, with styled."""
    query = sql.SQL("""SELECT t.lang, t.tid, s.text, t.text, {1} FROM {0}.targets t
        JOIN {0}.sources s ON s.sid = t.sid{2}""").format(
        sql.Identifier(schema), sql.SQL("ts.style" if styled else "NULL"),
        sql.SQL(" LEFT JOIN target_styles ts ON ts.tid = t.tid" if styled else ""))
    params = ()
    if langs:
        query += sql.SQL(" WHERE t.lang = ANY(%s)")
        params = (list(langs),)
    with connection.cursor(name="check_units") as rows:
        rows.itersize = 10000
        rows.execute(query, params)
        yield from rows


def _style_targets(cursor, schema, styles):
    """Make the target_styles table: each target's checker style, where the
    blocks it came from that have one (styles: {block: style}) agree."""
    cursor.execute("CREATE TEMPORARY TABLE block_styles (block TEXT, style TEXT) ON COMMIT DROP")
    cursor.executemany("INSERT INTO block_styles VALUES (%s, %s)", sorted(styles.items()))
    cursor.execute(sql.SQL("""CREATE TEMPORARY TABLE target_styles ON COMMIT DROP AS
        SELECT l.tid, MIN(b.style) AS style FROM {0}.target_imports l
        JOIN {0}.imports i ON i.iid = l.iid JOIN block_styles b ON b.block = i.block
        GROUP BY l.tid HAVING COUNT(DISTINCT b.style) = 1""").format(sql.Identifier(schema)))
    cursor.execute("ANALYZE target_styles")


def default_names():
    """amaGama's checks and measures."""
    return list(CHECKS) + list(MEASURES)


def configured_ttk(config):
    """translate-toolkit's checks to run, as UNIT_CHECKS_TTK sets them."""
    names = config.get("UNIT_CHECKS_TTK")
    return list(TTK_CHECKS if names is None else names)


def workers(config, given=None):
    """Processes to run checks in: given, else UNIT_CHECKS_WORKERS, else one per CPU."""
    return given or config.get("UNIT_CHECKS_WORKERS") or os.cpu_count() or 1


def _check(connection, schema, langs=(), examples=3, names=None, styles=None, workers=1):
    """(Report, Checks) on the targets in schema, in langs or all of its
    languages, for names (check_names(); None: default_names()), leaving
    each failing target's mask in the checked table. Nothing is committed.

    translate-toolkit's checks use the checker of the style the target's
    blocks have (styles: {block: style}), else the standard one.

    The first pass finds each language's usual scripts and length; the
    second runs the checks.
    """
    names = check_names(default_names() if names is None else names)
    # Targets are counted by script given the sources' own script, as far as
    # the pass has seen them.
    homes = collections.Counter()
    scripts = collections.defaultdict(collections.Counter)
    steps = collections.defaultdict(collections.Counter)
    if set(names) & set(MEASURES):
        for lang, _, source, target, _ in _rows(connection, schema, langs):
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
    checks = Checks(names, home, rare, usual)

    report = Report(examples)
    with connection.cursor() as cursor:
        links = _has_imports(cursor, schema)
        styled = bool(links and styles and checks.ttk)
        if styled:
            _style_targets(cursor, schema, styles)
        cursor.execute("CREATE TEMPORARY TABLE checked (tid INTEGER NOT NULL, mask BIGINT NOT NULL)"
                       " ON COMMIT DROP")
        failed = io.StringIO()

        def write():
            failed.seek(0)
            cursor.copy_from(failed, "checked", columns=("tid", "mask"))
            failed.seek(0)
            failed.truncate()
        written = 0
        for (lang, tid, source, target, _), mask in masks(
                _rows(connection, schema, langs, styled), checks, workers):
            report.targets += 1
            report.languages[lang] += 1
            if not mask:
                continue
            for name in checks.failed(mask):
                report.add(name, lang, tid, source, target)
            failed.write("%d\t%d\n" % (tid, mask))
            written += 1
            if written % COPY_BATCH == 0:
                write()
        write()
        cursor.execute("ANALYZE checked")
        if links:
            report.count_blocks(cursor, schema, checks)
        else:
            for examples in report.examples.values():
                for example in examples:
                    example[3] = []
    return report, checks


def check_units(connection, schema, langs=(), examples=3, names=None, styles=None, workers=1):
    """A Report on the targets in schema; see _check(). Nothing is written."""
    try:
        return _check(connection, schema, langs, examples, names, styles, workers)[0]
    finally:
        connection.rollback()


def _has_imports(cursor, schema):
    cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = %s AND table_name = 'target_imports')""", (schema,))
    return cursor.fetchone()[0]


def reject(connection, schema, examples=3):
    """Delete the targets in schema that fail FILTER; a Report on them."""
    report, checks = _check(connection, schema, examples=examples, names=FILTER)
    leave_out = sum(checks.bits[name] for name in FILTER)
    with connection.cursor() as cursor:
        if _has_imports(cursor, schema):
            cursor.execute(sql.SQL("""DELETE FROM {}.target_imports l USING checked c
                WHERE l.tid = c.tid AND c.mask & %s <> 0""").format(sql.Identifier(schema)),
                (leave_out,))
        cursor.execute(sql.SQL("""DELETE FROM {}.targets t USING checked c
            WHERE t.tid = c.tid AND c.mask & %s <> 0""").format(sql.Identifier(schema)),
            (leave_out,))
        report.left_out = cursor.rowcount
    connection.commit()
    return report
