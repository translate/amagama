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
import contextlib
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
from translate.filters.decorators import Category

from amagama.tmdb import TMDB

_checker = checks.StandardChecker()

# printf conversions (%s, %1$s, %(name)s, with flags, width and length), or
# KDE's and Qt's numbered %1 to %99.
_PRINTF = re.compile(
    r"%(?:(\d+)\$|\((\w+)\))?[-+#0']*(\d+|\*)?(?:\.(?:\d+|\*))?"
    r"(?:hh|h|ll|l|L|q|j|z|t|I)*([diouxXeEfFgGaAcspn])"
    r"|%L?(\d{1,2})(?![\d$])")
# KDE's and Qt's numbered placeholders alone: %2a is %2 and a letter.
_NUMBERED = re.compile(r"%L?(\d{1,2})(?![\d$])")
# LibreOffice's %PRODUCTNAME and the like.
_VARIABLE = re.compile(r"%[A-Z][A-Z_]{3,}")
# A date and time format, whose fields are reordered as the language needs.
_STRFTIME = re.compile(r"%[-_0^#]?[HMSYyBbPIklZjUWVCRT]")
# Placeholders in braces: Moodle's {$a}, numbered {0} (or typed, {0,number}),
# empty {} and Ruby's %{name}. Other braces are often text to translate.
_BRACES = re.compile(r"%\{[^\W\d]\w*\}|\{\$[^{}]*\}|\{\d+\}|\{\}|\{(\d+)\s*,\s*\w+[^{}]*\}")
# Java MessageFormat's choice format, {0,choice,1#one file|1<{0} files}, and
# ICU's plural and select: their branches' text is translated, and their
# number follows the language.
_CHOICE = re.compile(r"\{(\d+|[^\W\d]\w*)\s*,\s*(choice|plural|selectordinal|select)\s*,")
# ICU's plural and select, whose branches are in braces (Java's choice uses |).
_ICU_BRANCHING = re.compile(r"\{(\d+|[^\W\d]\w*)\s*,\s*(plural|selectordinal|select)\s*,")
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
_NOT_SCRIPTS = {"CIRCLED", "COMBINING", "FEMININE", "FULLWIDTH", "HALFWIDTH", "MASCULINE",
                "MATHEMATICAL", "MODIFIER", "PARENTHESIZED", "SMALL", "SQUARED"}


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


def printf_placeholders(text, numbered_only=False):
    """(placeholders, widened) of text: each placeholder (numbered %1 as
    itself, a conversion as its position or name and type, variables as
    they are), and those with a width. A conversion without a position
    takes the next one, so %s %s can be translated as %2$s %1$s; %i is %d,
    and Qt's %L1 is %1. With numbered_only, only KDE's and Qt's numbered
    ones are read, as their own text is."""
    found, widened = collections.Counter(), set()
    text = text.replace("%%", "")
    for variable in _VARIABLE.findall(text):
        found[variable] += 1
    if numbered_only:
        for number in _NUMBERED.findall(_VARIABLE.sub("", text)):
            found["%" + number] += 1
        return found, widened
    plain = 0
    for position, name, width, conversion, numbered in _PRINTF.findall(_VARIABLE.sub("", text)):
        if not (numbered or position or name):
            plain += 1
            position = str(plain)
        conversion = "d" if conversion == "i" else conversion
        key = "%" + numbered if numbered else "%%%s%s" % (position or name, conversion)
        found[key] += 1
        if width:
            widened.add(key)
    return found, widened


def _icu_chars(text, start=0):
    """(position, char) of text from start, but for ICU's quoted literals: an
    apostrophe before {, }, # or | quotes up to the next one ('' is an
    apostrophe)."""
    position = start
    while position < len(text):
        char = text[position]
        if char == "'" and text[position + 1:position + 2] in ("{", "}", "#", "|"):
            close = text.find("'", position + 1)
            position = len(text) if close < 0 else close + 1
            continue
        yield position, char
        position += 1


def _closing(text, start):
    """The position after the } that closes a { just before start, or None."""
    depth = 1
    for position, char in _icu_chars(text, start):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth == 0:
            return position + 1
    return None


def _branches(body):
    """The branches' texts of an ICU plural or select's body, "one {# file}
    other {# files}"; an empty branch has no text to check."""
    found, depth, start = [], 0, None
    for position, char in _icu_chars(body):
        if char == "{":
            if depth == 0:
                start = position + 1
            depth += 1
        elif char == "}" and depth:
            depth -= 1
            if depth == 0 and body[start:position].strip():
                found.append(body[start:position])
    return found


def _icu_split(text):
    """(text outside its ICU plurals and selects, their branches' texts), or
    None if it has none. A branch with a plural or select inside is split
    too, its own text a branch of its own."""
    outside, branches, pos = [], [], 0
    while match := _ICU_BRANCHING.search(text, pos):
        end = _closing(text, match.end())
        if end is None:
            break
        outside.append(text[pos:match.start()])
        for branch in _branches(text[match.end():end - 1]):
            nested = _icu_split(branch)
            branches += [branch] if nested is None else [nested[0]] + nested[1]
        pos = end
    if pos == 0:
        return None
    return "".join(outside + [text[pos:]]), branches


def _printf_parts(text, numbered_only=False):
    """(text's printf placeholders outside ICU plurals and selects, widened,
    and the placeholders their branches use or None if it has none). A
    branch uses a placeholder as many times as the language's form needs,
    so only which occur is compared; each branch counts its own positions."""
    split = _icu_split(text)
    if split is None:
        found, widened = printf_placeholders(text, numbered_only)
        return found, widened, None
    found, widened = printf_placeholders(split[0], numbered_only)
    used = set()
    for branch in split[1]:
        branch_found, branch_widened = printf_placeholders(branch, numbered_only)
        used |= set(branch_found)
        widened |= branch_widened
    return found, widened, used


def _printf_differs(source, target):
    if _STRFTIME.search(source):
        return False
    wanted, _, wanted_used = _printf_parts(source)
    # A source of KDE's or Qt's numbered placeholders alone: the target's are
    # read so too, so a letter after one isn't a conversion.
    keys = set(wanted) | (wanted_used or set())
    kde = bool(keys) and all(key[1:].isdigit() for key in keys)
    found, widened, found_used = _printf_parts(target, kde)
    known = set(wanted) | (wanted_used or set())
    numbered = any(key[1:].isdigit() for key in known)

    def unwanted(key):
        # Not the source's kind of placeholder: Basque and Turkish write
        # percentages as %95 or %50.
        return key not in known and ((key[1:].isdigit() and not numbered) or key in widened)
    found = collections.Counter({key: n for key, n in found.items() if not unwanted(key)})
    if found_used is not None:
        found_used = {key for key in found_used if not unwanted(key)}
    if wanted_used is not None and found_used is not None:
        return found != wanted or found_used != wanted_used
    if wanted_used is not None or found_used is not None:
        # One has a plural or select the other hasn't: compare which occur.
        return set(found) | (found_used or set()) != known
    return found != wanted


def _plain_braces(text):
    """The placeholders in braces in text, a typed one ({0,number}) as its
    argument ({0})."""
    return ["{%s}" % match[1] if match[1] else match[0] for match in _BRACES.finditer(text)]


def _braces(text):
    """text's placeholders in braces. A choice, plural or select format
    counts as its argument and the placeholders its branches use, but not
    its own argument again."""
    found = []
    pos = 0
    while match := _CHOICE.search(text, pos):
        depth, end = 1, match.end()
        while end < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[end], 0)
            end += 1
        if depth:
            break
        found += _plain_braces(text[pos:match.start()])
        found.append("{%s,%s}" % (match[1], match[2]))
        body = text[match.end():end - 1]
        # An empty branch ("one {}") isn't the empty placeholder {}.
        inner = " ".join(_branches(body)) if match[2] != "choice" else body
        found += sorted(set(_braces(inner)) - {"{%s}" % match[1]})
        pos = end
    return sorted(found + _plain_braces(text[pos:]))


def _braces_differ(source, target):
    """Whether target's placeholders differ from source's. A numbered or
    named one may be used more or fewer times; each {} takes the next
    argument, so their number must match."""
    if _TEX.search(source):
        return False
    wanted, found = _braces(source), _braces(target)
    return set(wanted) != set(found) or wanted.count("{}") != found.count("{}")


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
# a sentence break it recognises, and its functions check is cubic on dotted
# text with "()" in it.
SLOW_CHECKS = {"sentencecount": 1000, "functions": 500}
# A target's failures are a 64-bit mask.
MAX_CHECKS = 63
# Blocks with fewer targets get no suggested weight.
WEIGHT_MIN_TARGETS = 1000
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


# Check categories, most serious first, as translate-toolkit's Category.
CATEGORIES = ("critical", "functional", "cosmetic", "extraction")
_CATEGORY_NAMES = {Category.CRITICAL: "critical", Category.FUNCTIONAL: "functional",
                   Category.COSMETIC: "cosmetic", Category.EXTRACTION: "extraction"}
# amaGama's checks and measures, as translate-toolkit would have them: a
# placeholder, tag, escape or script that doesn't match breaks the
# translation; a sentence left in English, or a length far from the usual,
# is likely the wrong text, like translate-toolkit's unchanged, long and short.
OWN_CATEGORIES = {"printf": "critical", "braces": "critical", "tags": "critical",
                  "escapes": "critical", "script": "critical", "unchanged": "functional",
                  "length": "functional"}


@functools.lru_cache(maxsize=1)
def _ttk_categories():
    # A checker records a check's category the first time the check runs.
    found = {}
    for checker in [checks.StandardChecker] + [projectcheckers[style]
                                               for style in sorted(projectcheckers)]:
        checker = checker()
        for name in checker.defaultfilters:
            with contextlib.suppress(Exception):
                getattr(checker, name)("", "")
        for name, found_category in checker.categories.items():
            found.setdefault(name, _CATEGORY_NAMES.get(found_category))
    return found


def category(name):
    """The category of check name (one of CATEGORIES), or None if unknown.
    A translate-toolkit check's is the standard checker's, else the first
    project checker's that has it."""
    return OWN_CATEGORIES.get(name) or _ttk_categories().get(name)


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

    def __init__(self, examples=3, rng=None, sample=None):
        self.max_examples = examples
        self.rng = rng or random.Random()
        # The check whose examples are chosen in each language instead.
        self.sample = sample
        self.targets = 0
        self.languages = collections.Counter()
        self.by_language = collections.defaultdict(collections.Counter)
        self.by_block = collections.defaultdict(collections.Counter)
        self.examples = collections.defaultdict(list)
        # Failures by (block, language), and the targets of each.
        self.by_source = collections.defaultdict(collections.Counter)
        self.source_targets = {}
        # Targets failing the weighed checks, by block and (None) in all.
        self.weighed = collections.Counter()
        # The targets a rebuild left out, and those it flagged.
        self.left_out = self.flagged = 0
        # Failures so far, for choosing examples.
        self.seen = collections.Counter()

    def failures(self, name):
        return sum(self.by_language[name].values())

    def add(self, name, lang, tid, source, target):
        self.by_language[name][lang] += 1
        key = (name, lang) if name == self.sample else name
        self.seen[key] += 1
        # Each failure so far is equally likely to be an example.
        example = [lang, source, target, tid]
        if len(self.examples[key]) < self.max_examples:
            self.examples[key].append(example)
        else:
            n = self.rng.randrange(self.seen[key])
            if n < self.max_examples:
                self.examples[key][n] = example

    def sample_lines(self):
        """The sample check's examples, by language, as lines of text."""
        for lang in sorted(self.by_language[self.sample]):
            for _, source, target, blocks in self.examples[(self.sample, lang)]:
                yield "[%s] %r -> %r%s" % (
                    lang, source, target, " (%s)" % ", ".join(blocks) if blocks else "")

    def lines(self, names, top=10):
        """The report on names, as lines of text."""
        for name in names:
            yield next(self.summary([name]))
            if not self.failures(name):
                continue
            yield "  Languages: " + ", ".join(
                "%s %d (%.1f%%)" % (lang, n, 100 * n / self.languages[lang])
                for lang, n in self.by_language[name].most_common(top))
            if self.by_block[name]:
                yield "  Blocks: " + ", ".join(
                    "%s %d" % item for item in self.by_block[name].most_common(top))
            for lang, source, target, blocks in self.examples.get(name, ()):
                yield "  [%s] %r -> %r%s" % (
                    lang, source, target, " (%s)" % ", ".join(blocks) if blocks else "")

    def count_sources(self, cursor, schema, langs, checks, weigh):
        """Count the targets each block supplied in each language, and those
        failing any of the checks weigh, by block and in all."""
        mask = sum(checks.bits[name] for name in weigh)
        query = sql.SQL("""SELECT i.block, i.lang, COUNT(DISTINCT l.tid),
                COUNT(DISTINCT c.tid) FILTER (WHERE c.mask & %s <> 0)
            FROM {0}.target_imports l JOIN {0}.imports i ON i.iid = l.iid
            LEFT JOIN checked c ON c.tid = l.tid WHERE i.block IS NOT NULL""").format(
            sql.Identifier(schema))
        params = [mask]
        if langs:
            query += sql.SQL(" AND i.lang = ANY(%s)")
            params.append(list(langs))
        cursor.execute(query + sql.SQL(" GROUP BY 1, 2"), params)
        for block, lang, targets, weighed in cursor.fetchall():
            self.source_targets[(block, lang)] = targets
            self.weighed[block] += weighed
        cursor.execute("SELECT COUNT(*) FROM checked WHERE mask & %s <> 0", (mask,))
        self.weighed[None] = cursor.fetchone()[0]

    def source_table(self, slang, names, header=True):
        """Rows of slang, block, language, targets, then each of names'
        failures; with header, a row of column names first."""
        if header:
            yield ["source", "block", "language", "targets"] + list(names)
        for block, lang in sorted(self.source_targets):
            failures = self.by_source.get((block, lang), {})
            yield [slang, block, lang, self.source_targets[(block, lang)]] + [
                failures.get(name, 0) for name in names]

    def suggested_weights(self, weights=None, min_targets=None):
        """(block, weight, suggested weight, share failing) of the blocks of
        min_targets or more whose share of targets failing the weighed checks
        is over the share of all targets: weight (default 1, from weights)
        less twice the difference, by at most half, in steps of 0.05; none
        for a block whose weight has no lower step from half up."""
        weights = weights or {}
        min_targets = WEIGHT_MIN_TARGETS if min_targets is None else min_targets
        overall = self.weighed[None] / (self.targets or 1)
        targets = collections.Counter()
        for (block, _), n in self.source_targets.items():
            targets[block] += n
        suggested = []
        for block, n in sorted(targets.items()):
            share = self.weighed[block] / n
            weight = weights.get(block, 1.0)
            new = max(math.floor(20 * weight * max(0.5, 1 - 2 * (share - overall)) + 1e-9),
                      math.ceil(10 * weight - 1e-9)) / 20
            if n >= min_targets and share > overall and new < weight:
                suggested.append((block, weight, new, share))
        return suggested

    def weight_lines(self, weigh, weights=None):
        """The suggested weights, as lines of text."""
        yield "Suggested weights, by %s (%.2f%% of all targets fail): %s" % (
            ", ".join(weigh), 100 * self.weighed[None] / (self.targets or 1),
            "" if self.suggested_weights(weights) else "none")
        for block, weight, new, share in self.suggested_weights(weights):
            yield "  %s: %g -> %g (%.2f%% fail)" % (block, weight, new, 100 * share)

    def summary(self, names):
        """A line per check in names: its failures."""
        for name in names:
            failures = self.failures(name)
            yield "%s: %d (%.2f%%)" % (name, failures, 100 * failures / (self.targets or 1))

    def count_blocks(self, cursor, schema, checks, by_source=False):
        """Count each check's failures (in the checked table) by the blocks
        that supplied them, and with by_source by block and language."""
        cursor.execute(sql.SQL("""SELECT i.block, i.lang, c.mask, COUNT(DISTINCT c.tid)
            FROM checked c
            JOIN {0}.target_imports l ON l.tid = c.tid JOIN {0}.imports i ON i.iid = l.iid
            WHERE i.block IS NOT NULL GROUP BY 1, 2, 3""").format(sql.Identifier(schema)))
        for block, lang, mask, n in cursor.fetchall():
            for name in checks.failed(mask):
                self.by_block[name][block] += n
                if by_source:
                    self.by_source[(block, lang)][name] += n
        # The examples' blocks in one query: in a deployed schema
        # target_imports is a view, and finding a target by tid reads them all.
        tids = sorted({example[3] for examples in self.examples.values() for example in examples})
        found = {}
        if tids:
            cursor.execute(sql.SQL("""SELECT l.tid, ARRAY_AGG(DISTINCT i.block ORDER BY i.block)
                FROM {0}.target_imports l JOIN {0}.imports i ON i.iid = l.iid
                WHERE l.tid = ANY(%s) AND i.block IS NOT NULL GROUP BY l.tid""").format(
                sql.Identifier(schema)), (tids,))
            found = dict(cursor.fetchall())
        for examples in self.examples.values():
            for example in examples:
                example[3] = found.get(example[3], [])


def _rows(connection, schema, langs, styled=False, unchecked=False):
    """(lang, tid, source, target, style) of the targets in schema, in langs or
    all; style is that of the target_styles table, with styled. With
    unchecked, the targets checked_before doesn't list."""
    query = sql.SQL("""SELECT t.lang, t.tid, s.text, t.text, {1} FROM {0}.targets t
        JOIN {0}.sources s ON s.sid = t.sid{2}""").format(
        sql.Identifier(schema), sql.SQL("ts.style" if styled else "NULL"),
        sql.SQL(" LEFT JOIN target_styles ts ON ts.tid = t.tid" if styled else ""))
    params = ()
    conditions = []
    if langs:
        conditions.append(sql.SQL("t.lang = ANY(%s)"))
        params = (list(langs),)
    if unchecked:
        conditions.append(sql.SQL(
            "NOT EXISTS (SELECT 1 FROM {}.checked_before c WHERE c.tid = t.tid)").format(
            sql.Identifier(schema)))
    if conditions:
        query += sql.SQL(" WHERE ") + sql.SQL(" AND ").join(conditions)
    with connection.cursor(name="check_units") as rows:
        rows.itersize = 10000
        rows.execute(query, params)
        yield from rows


def _style_targets(cursor, schema, styles, langs):
    """Make the target_styles table: each target's checker style, where the
    blocks it came from that have one (styles: {block: style}) agree."""
    cursor.execute("CREATE TEMPORARY TABLE block_styles (block TEXT, style TEXT) ON COMMIT DROP")
    cursor.executemany("INSERT INTO block_styles VALUES (%s, %s)", sorted(styles.items()))
    cursor.execute(sql.SQL("""CREATE TEMPORARY TABLE target_styles ON COMMIT DROP AS
        SELECT l.tid, MIN(b.style) AS style FROM {0}.target_imports l
        JOIN {0}.imports i ON i.iid = l.iid JOIN block_styles b ON b.block = i.block
        WHERE %s IS NULL OR i.lang = ANY(%s)
        GROUP BY l.tid HAVING COUNT(DISTINCT b.style) = 1""").format(sql.Identifier(schema)),
        (list(langs) or None, list(langs)))
    cursor.execute("ANALYZE target_styles")


def default_names():
    """amaGama's checks and measures."""
    return list(CHECKS) + list(MEASURES)


def configured_ttk(config):
    """translate-toolkit's checks to run, as UNIT_CHECKS_TTK sets them."""
    names = config.get("UNIT_CHECKS_TTK")
    return list(TTK_CHECKS if names is None else names)


def leave_out(config):
    """The checks whose failures rebuilds leave out: FILTER, and those
    UNIT_CHECKS_LEAVE_OUT adds."""
    return list(dict.fromkeys(list(FILTER) + list(config.get("UNIT_CHECKS_LEAVE_OUT") or ())))


def workers(config, given=None):
    """Processes to run checks in: given, else UNIT_CHECKS_WORKERS, else one per CPU."""
    return given or config.get("UNIT_CHECKS_WORKERS") or os.cpu_count() or 1


def _shifted(cursor, schema, checks):
    """The languages whose measures (rare scripts, usual length) differ from
    those schema's check_measures recorded, or True for all of them: no
    record, or the source language's script changed."""
    if not _has_relation_in(cursor, schema, "check_measures"):
        return True
    cursor.execute(sql.SQL("SELECT lang, rare, usual, home FROM {}.check_measures").format(
        sql.Identifier(schema)))
    recorded = {lang: (sorted(rare), usual) for lang, rare, usual, home in cursor.fetchall()}
    cursor.execute(sql.SQL("SELECT DISTINCT home FROM {}.check_measures").format(
        sql.Identifier(schema)))
    homes = {home for (home,) in cursor.fetchall()}
    if homes and homes != {checks.home}:
        return True
    now = {lang: (sorted(checks.rare.get(lang, ())), checks.usual.get(lang))
           for lang in set(checks.rare) | set(checks.usual)}
    return sorted(lang for lang in set(now) | set(recorded) if now.get(lang) != recorded.get(lang))


def _has_relation_in(cursor, schema, table):
    cursor.execute("SELECT to_regclass(%s) IS NOT NULL",
                   (sql.Identifier(schema, table).as_string(cursor),))
    return cursor.fetchone()[0]


def _check(connection, schema, langs=(), examples=3, names=None, styles=None, workers=1,
           sample=None, rng=None, weigh=None, measured=None):
    """(Report, Checks) on the targets in schema, in langs or all of its
    languages, for names (check_names(); None: default_names()), leaving
    each failing target's mask in the checked table. Nothing is committed.
    With sample, a check, its examples are chosen in each language. With
    weigh, checks among names, the targets of each block and language are
    counted, and those failing any of weigh.

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

    report = Report(examples, rng, sample)
    with connection.cursor() as cursor:
        links = _has_imports(cursor, schema)
        # A rebuild's targets copied with their check bits aren't checked
        # again; the measures above still count them. Weights are suggested
        # from every block's failures, so then all are. A language whose
        # measures moved since the data the bits came from (measured, a
        # schema) is checked again whole.
        unchecked = weigh is None and _has_relation_in(cursor, schema, "checked_before")
        if unchecked and measured:
            shifted = _shifted(cursor, measured, checks)
            if shifted is True:
                unchecked = False
            elif shifted:
                cursor.execute(sql.SQL("""DELETE FROM {0}.checked_before c USING {0}.targets t
                    WHERE t.tid = c.tid AND t.lang = ANY(%s)""").format(sql.Identifier(schema)),
                    (shifted,))
        styled = bool(links and styles and checks.ttk)
        if styled:
            _style_targets(cursor, schema, styles, langs)
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
                _rows(connection, schema, langs, styled, unchecked), checks, workers):
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
            report.count_blocks(cursor, schema, checks, weigh is not None)
            if weigh is not None:
                report.count_sources(cursor, schema, langs, checks, weigh)
        else:
            for examples in report.examples.values():
                for example in examples:
                    example[3] = []
    return report, checks


def check_units(connection, schema, langs=(), examples=3, names=None, styles=None, workers=1,
                sample=None, rng=None, weigh=None):
    """A Report on the targets in schema; see _check(). Nothing is written."""
    try:
        return _check(connection, schema, langs, examples, names, styles, workers, sample,
                      rng, weigh)[0]
    finally:
        connection.rollback()


def _has_imports(cursor, schema):
    cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.tables
        WHERE table_schema = %s AND table_name = 'target_imports')""", (schema,))
    return cursor.fetchone()[0]


def reject(connection, schema, examples=3, names=(), leave_out=FILTER, styles=None, workers=1,
           weigh=None, measured=None):
    """Delete the targets in schema failing leave_out, and record the
    failures of names on the others (targets.checks, named in
    check_names); a Report on both, see _check().

    For a rebuild about to deploy: the targets' unique index, which deploy
    drops anyway, goes first, so the flags don't rewrite it.
    """
    report, checks = _check(connection, schema, examples=examples,
                            names=list(leave_out) + list(names), styles=styles, workers=workers,
                            weigh=weigh, measured=measured)
    mask = sum(checks.bits[name] for name in leave_out)
    table = sql.Identifier(schema)
    with connection.cursor() as cursor:
        if _has_imports(cursor, schema):
            cursor.execute(sql.SQL("""DELETE FROM {}.target_imports l USING checked c
                WHERE l.tid = c.tid AND c.mask & %s <> 0""").format(table), (mask,))
        cursor.execute(sql.SQL("""DELETE FROM {}.targets t USING checked c
            WHERE t.tid = c.tid AND c.mask & %s <> 0""").format(table), (mask,))
        report.left_out = cursor.rowcount
        cursor.execute(sql.SQL("SET LOCAL search_path TO {}").format(table))
        cursor.execute(TMDB.INIT_CHECKS)
        cursor.execute("DELETE FROM check_names")
        cursor.executemany("INSERT INTO check_names (bit, name, left_out) VALUES (%s, %s, %s)",
                           [(bit, name, name in leave_out) for bit, name in enumerate(checks.names)])
        cursor.execute("DROP INDEX IF EXISTS targets_unique_idx")
        cursor.execute("""UPDATE targets t SET checks = c.mask FROM checked c
            WHERE t.tid = c.tid AND c.mask & %s = 0""", (mask,))
        report.flagged = cursor.rowcount
        cursor.execute("DROP TABLE IF EXISTS checked_before")
        # What the measures were, for the next rebuild to tell whether the
        # bits it copies from this data still hold.
        cursor.execute("DROP TABLE IF EXISTS check_measures")
        cursor.execute("""CREATE TABLE check_measures (lang TEXT PRIMARY KEY, rare TEXT[] NOT NULL,
            usual INTEGER, home TEXT)""")
        cursor.executemany("INSERT INTO check_measures VALUES (%s, %s, %s, %s)", [
            (lang, sorted(checks.rare.get(lang, ())), checks.usual.get(lang), checks.home)
            for lang in sorted(set(checks.rare) | set(checks.usual))])
    connection.commit()
    return report
