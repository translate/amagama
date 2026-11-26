#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Placeholders of formats that translate-toolkit's general parsers
don't know: brace {name}, i18next {{count}} and $t(key), Fluent
{ $user }, ICU MessageFormat {count, plural, ...}, Apple %@, .NET
{0:N2}, Ruby %{name} and shell $name; and Phrase's inline tags {1}.

Most are ordinary text in other formats, so L{parsers_for} gives each
only to the formats, and the gettext flags, that use it. In a plural or
select, only the structure is a placeable: the text of each case is
for translating.

This module needs only translate-toolkit and the standard library, as
L{virtaal.support.abstraction} does.
"""

import re

from translate.storage.placeables import StringElem, base


class _Malformed(Exception):
    """Text that isn't valid in the syntax being parsed."""


def _elems(tokens):
    """StringElems for (class or None, text) tokens, or None if none is
    a placeable."""
    if not any(cls for cls, _text in tokens):
        return None
    return [cls([text]) if cls else StringElem(text) for cls, text in tokens if text]


class _EscapingRegexPlaceable(base.Ph):
    """A placeable found by regex, whose matches without a C{ph} group
    are escapes (such as C{{{} or C{%%}) and stay text."""

    iseditable = False
    istranslatable = False
    regex = re.compile('(?!)')

    @classmethod
    def parse(cls, pstr):
        tokens = []
        last = 0
        for match in cls.regex.finditer(pstr):
            if match.group('ph') is None:
                continue
            tokens.append((None, pstr[last : match.start()]))
            tokens.append((cls, match.group()))
            last = match.end()
        tokens.append((None, pstr[last:]))
        return _elems(tokens)


class BracePlaceable(_EscapingRegexPlaceable):
    """A Python str.format(), C# or Perl brace field: {name}, {0},
    {user.name}, {0!r:>10}; {{ and }} are escaped braces."""

    regex = re.compile(
        r"""\{\{|\}\}|(?P<ph>\{
        (?:[A-Za-z_]\w*|\d+)?                    # field name
        (?:\.[A-Za-z_]\w*|\[[^\]{}]+\])*         # attributes and indexes
        (?:![rsa])?                              # conversion
        (?::(?:[^{}]|\{[^{}]*\})*)?              # format spec
        \})""",
        re.VERBOSE,
    )


class DotNetPlaceable(_EscapingRegexPlaceable):
    """A .NET composite format item with an alignment or a format
    string, such as {0:N2} or {1,-10}; translate-toolkit's Java
    MessageFormat parser takes a bare {0}."""

    regex = re.compile(r'\{\{|\}\}|(?P<ph>\{\d+(?:\s*,\s*-?\d+(?:\s*:[^{}]*)?|\s*:[^{}]*)\})')


class I18NextPlaceable(_EscapingRegexPlaceable):
    """An i18next interpolation {{count}}, {{ user.name }}, {{- html}},
    {{val, number}}, or a nesting $t(common.save)."""

    regex = re.compile(r'(?P<ph>\{\{[^{}]+\}\}|\$t\([^()\n]+\))')


class ApplePlaceable(_EscapingRegexPlaceable):
    """An Objective-C or Swift object placeholder %@ or %1$@, or a
    .stringsdict variable %#@files@."""

    regex = re.compile(r'%%|(?P<ph>%(?:[1-9]\d*\$)?@|%#@[A-Za-z_]\w*@)')


class RubyPlaceable(_EscapingRegexPlaceable):
    """A Ruby named reference %{name} or %<name>s."""

    regex = re.compile(
        r'%%|(?P<ph>%\{[A-Za-z_]\w*\}|%<[A-Za-z_]\w*>[-+ 0#]*(?:\d+|\*)?(?:\.\d+)?[bBdiouxXeEfgGaAcps])'
    )


class ShellVariablePlaceable(_EscapingRegexPlaceable):
    """A shell variable $name or ${name}, as in gettext's sh-format."""

    # Not the $s of printf's %1$s.
    regex = re.compile(r'\$\$|(?<![\w%])(?P<ph>\$\{[A-Za-z_]\w*\}|\$[A-Za-z_]\w*)')


class VuePlaceable(_EscapingRegexPlaceable):
    """A vue-i18n interpolation {name}, {0} or {'@'}, a linked message
    @:common.save or @.lower:name, or the | between plural forms."""

    regex = re.compile(
        r"""(?P<ph>\{\s*(?:[A-Za-z_$][\w.$]*|\d+|'[^'{}]*')\s*\}
        |@(?:\.[A-Za-z]+)?:(?:[\w.\-]+|\([\w.\-]+\))
        |(?<=\s)\|(?=\s))""",
        re.VERBOSE,
    )


class FluentReferencePlaceable(_EscapingRegexPlaceable):
    """A plain Fluent reference { $user }, { -brand-name } or
    { msg.attr }, for matching across formats; L{FluentPlaceable}
    parses a Fluent pattern in full."""

    regex = re.compile(r'(?P<ph>\{ *-?\$?[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*)? *\})')


class PhraseTagPlaceable(_EscapingRegexPlaceable):
    """A Phrase (Memsource) .mxliff inline tag, which Phrase writes as
    text: {1}, the {1> and <1} around text, and {b> <b}, {i> <i},
    {u> <u}, {sup> <sup} and {sub> <sub} for formatting."""

    regex = re.compile(r'(?P<ph>\{\d+\}|\{(?:\d+|b|i|u|sup|sub)>|<(?:\d+|b|i|u|sup|sub)\})')


PHRASE_NAMESPACE = 'http://www.memsource.com/mxlf/'
"""The start of Phrase's namespace URI, mxlf/2.0 in today's files."""


# ICU MessageFormat


class ICUPlaceable(base.Ph):
    """An ICU MessageFormat argument: {name} or {n, number}; and, of a
    plural or select, its structure ("{count, plural, one {", "} other
    {", "}}") and # for the number. The text of each case is left for
    translating."""

    iseditable = False
    istranslatable = False

    @classmethod
    def parse(cls, pstr):
        if '{' not in pstr:
            return None
        try:
            tokens, end = _ICUParser(pstr, cls).message(0, plural=False)
        except (_Malformed, IndexError, RecursionError):
            return None
        if end != len(pstr):
            return None
        return _elems(_merge_structure(tokens))


_ICU_NAME = re.compile(r'\s*([^\s{},#\']+)\s*')
_ICU_SELECTOR = re.compile(r'\s*(=-?\d+(?:\.\d+)?|[^\s{}=]+)\s*\{')
_ICU_OFFSET = re.compile(r'\s*offset:\s*\d+')
_ICU_COMPLEX = ('plural', 'selectordinal', 'select')
_ICU_SPECIAL = '{}#|'
_MAX_DEPTH = 20


class _Structure(str):
    """Text of a plural's or select's structure, merged with any
    structure of the same argument beside it."""

    def __new__(cls, text, owner):
        self = super().__new__(cls, text)
        self.owner = owner
        return self


class _ICUParser:
    def __init__(self, text, cls):
        self.text = text
        self.cls = cls

    def message(self, i, plural, depth=0):
        """(tokens, end) of the message from i up to an unmatched } or
        the end; # is the number in a plural's case."""
        if depth > _MAX_DEPTH:
            raise _Malformed()
        text = self.text
        tokens = []
        start = i
        while i < len(text) and text[i] != '}':
            char = text[i]
            if char == "'":
                i = self._quoted(i, plural)
                continue
            if char == '{' or (char == '#' and plural):
                tokens.append((None, text[start:i]))
                if char == '#':
                    tokens.append((self.cls, '#'))
                    i += 1
                else:
                    found, i = self.argument(i, depth)
                    tokens.extend(found)
                start = i
                continue
            i += 1
        tokens.append((None, text[start:i]))
        return tokens, i

    def _quoted(self, i, plural):
        """The end of the apostrophe at i: '' is one apostrophe, and one
        before a special character quotes up to the next apostrophe."""
        text = self.text
        nxt = text[i + 1 : i + 2]
        if nxt == "'":
            return i + 2
        if nxt and nxt in _ICU_SPECIAL and (nxt != '#' or plural):
            close = text.find("'", i + 1)
            return len(text) if close == -1 else close + 1
        return i + 1

    def argument(self, i, depth):
        """(tokens, end) of the argument at text[i] == '{'."""
        text = self.text
        name = _ICU_NAME.match(text, i + 1)
        if name is None:
            raise _Malformed()
        j = name.end()
        if text[j] == '}':
            return [(self.cls, text[i : j + 1])], j + 1
        if text[j] != ',':
            raise _Malformed()
        kind = _ICU_NAME.match(text, j + 1)
        if kind is None:
            raise _Malformed()
        j = kind.end()
        if kind.group(1) in _ICU_COMPLEX:
            if text[j] != ',':
                raise _Malformed()
            return self._cases(i, j + 1, kind.group(1) != 'select', depth)
        return self._simple(i, j)

    def _simple(self, i, j):
        """An argument with a type and maybe a style: {n, number, ::
        percent}, up to its closing brace."""
        text = self.text
        nesting = 0
        while text[j] != '}' or nesting:
            if text[j] == '{':
                nesting += 1
            elif text[j] == '}':
                nesting -= 1
            j += 1
        return [(self.cls, text[i : j + 1])], j + 1

    def _cases(self, i, j, plural, depth):
        text = self.text
        if plural:
            offset = _ICU_OFFSET.match(text, j)
            if offset is not None:
                j = offset.end()
        tokens = [(self.cls, _Structure(text[i:j], i))]
        count = 0
        while True:
            selector = _ICU_SELECTOR.match(text, j)
            if selector is None:
                break
            tokens.append((self.cls, _Structure(text[j : selector.end()], i)))
            body, j = self.message(selector.end(), plural, depth + 1)
            if text[j] != '}':
                raise _Malformed()
            tokens.extend(body)
            tokens.append((self.cls, _Structure('}', i)))
            j += 1
            count += 1
        space = re.compile(r'\s*').match(text, j).end()
        if not count or text[space] != '}':
            raise _Malformed()
        tokens.append((self.cls, _Structure(text[j : space + 1], i)))
        return tokens, space + 1


def _joins(earlier, text):
    return isinstance(earlier, _Structure) and earlier.owner == text.owner


def _merge_structure(tokens):
    """tokens with the structure pieces of an argument next to each
    other, or with only spaces between them, joined into one placeable."""
    merged = []
    for cls, text in tokens:
        if not text:
            continue
        if isinstance(text, _Structure) and merged:
            last_cls, last = merged[-1]
            before = merged[-2][1] if len(merged) > 1 else ''
            if _joins(last, text):
                merged[-1] = (cls, _Structure(last + text, text.owner))
                continue
            if last_cls is None and last.isspace() and '\n' not in last and _joins(before, text):
                merged[-2:] = [(cls, _Structure(before + last + text, text.owner))]
                continue
        merged.append((cls, text))
    return [(cls, str(text)) for cls, text in merged]


# Fluent

_FLUENT_INLINE = re.compile(
    r"""\s*(?:
    "(?:[^"\\\n]|\\.)*"                                  # string literal
    |-?\d+(?:\.\d+)?                                     # number
    |[$-]?[A-Za-z][\w-]*(?:\.[A-Za-z][\w-]*)?(?:\([^(){}]*\))?  # reference or call
    |\{.*\}                                              # placeable
    )\s*""",
    re.VERBOSE | re.DOTALL,
)
_FLUENT_VARIANT = re.compile(r'[ \t]*\*?\[[ \t]*[^\]\s]+[ \t]*\]')
_FLUENT_SPECIAL_LINE = re.compile(r'\n[ \t]*\*?\[')


class FluentPlaceable(base.Ph):
    """A Fluent placeable: { $user }, { -brand-name }, { msg.attr },
    { NUMBER($n) }; and, of a select expression, its structure
    ("{ $n ->", "[one]", "*[other]", "}"). The text of each variant is
    left for translating."""

    iseditable = False
    istranslatable = False

    @classmethod
    def parse(cls, pstr):
        if '{' not in pstr:
            return None
        try:
            tokens, end = _FluentParser(pstr, cls).pattern(0, variant=False)
        except (_Malformed, IndexError, RecursionError):
            return None
        if end != len(pstr):
            return None
        return _elems(tokens)


class _FluentParser:
    def __init__(self, text, cls):
        self.text = text
        self.cls = cls

    def pattern(self, i, variant, depth=0):
        """(tokens, end) of the text and placeables from i up to an
        unmatched }, the end, or, in a variant, the next variant."""
        if depth > _MAX_DEPTH:
            raise _Malformed()
        text = self.text
        tokens = []
        start = i
        while i < len(text) and text[i] != '}':
            if variant and text[i] == '\n' and _FLUENT_SPECIAL_LINE.match(text, i):
                break
            if text[i] == '{':
                tokens.append((None, text[start:i]))
                found, i = self.placeable(i, depth)
                tokens.extend(found)
                start = i
                continue
            i += 1
        tokens.append((None, text[start:i]))
        return tokens, i

    def _expression_end(self, i):
        """(index of the } closing the placeable at text[i] == '{', or of
        the -> of a select, whether it is a select)."""
        text = self.text
        nesting = 0
        j = i + 1
        while True:
            char = text[j]
            if char == '"':
                j = self._string_end(j)
                continue
            if char == '{':
                nesting += 1
            elif char == '}':
                if not nesting:
                    return j, False
                nesting -= 1
            elif char == '\n' and not nesting:
                if text[i + 1 : j].strip():
                    raise _Malformed()
            elif text.startswith('->', j) and not nesting:
                return j, True
            j += 1

    def _string_end(self, j):
        text = self.text
        j += 1
        while text[j] != '"':
            if text[j] == '\n':
                raise _Malformed()
            j += 2 if text[j] == '\\' else 1
        return j + 1

    def placeable(self, i, depth):
        text = self.text
        j, select = self._expression_end(i)
        if not _FLUENT_INLINE.fullmatch(text, i + 1, j):
            raise _Malformed()
        if not select:
            return [(self.cls, text[i : j + 1])], j + 1
        tokens = [(self.cls, text[i : j + 2])]
        j += 2
        count = 0
        while True:
            line = text.find('\n', j)
            if line == -1 or text[j:line].strip():
                break
            variant = _FLUENT_VARIANT.match(text, line + 1)
            if variant is None:
                break
            key = variant.group().lstrip(' \t')
            tokens.append((None, text[j : variant.end() - len(key)]))
            tokens.append((self.cls, key))
            body, j = self.pattern(variant.end(), True, depth + 1)
            tokens.extend(body)
            count += 1
        close = len(text) - len(text[j:].lstrip())
        if not count or text[close : close + 1] != '}':
            raise _Malformed()
        tokens.append((None, text[j:close]))
        tokens.append((self.cls, '}'))
        return tokens, close + 1


# Which formats use which


EVERYWHERE = (ApplePlaceable, DotNetPlaceable)
"""Parsers for every file: Apple's %@ is printf's conversion for an
object, and .NET's {0:N2} is otherwise read as two numbers where
translate-toolkit already takes {0} as a placeable."""

FORMAT_PARSERS = {
    'arb': (ICUPlaceable,),
    'fluent': (FluentPlaceable,),
    'json': (I18NextPlaceable,),
    'json-icu': (ICUPlaceable,),
    'json-vue': (VuePlaceable,),
    'mxliff': (PhraseTagPlaceable,),
    'strings': (ApplePlaceable,),
    'ruby': (RubyPlaceable,),
    'dotnet': (DotNetPlaceable, BracePlaceable),
}
"""Format (see L{format_of}) -> the parsers its text needs: 'ruby' for
Ruby on Rails' YAML, 'dotnet' for .resx and the like, for a file whose
model names its format (placeable_format)."""

FLAG_PARSERS = {
    'python-brace-format': (BracePlaceable,),
    'perl-brace-format': (BracePlaceable,),
    'csharp-format': (DotNetPlaceable, BracePlaceable),
    'objc-format': (ApplePlaceable,),
    'ruby-format': (RubyPlaceable,),
    'sh-format': (ShellVariablePlaceable,),
}
"""gettext flag (#, python-brace-format) -> the parsers it calls for."""

PLACEABLES = (
    PhraseTagPlaceable,
    FluentPlaceable,
    ICUPlaceable,
    I18NextPlaceable,
    VuePlaceable,
    RubyPlaceable,
    ApplePlaceable,
    DotNetPlaceable,
    ShellVariablePlaceable,
    BracePlaceable,
)
"""All of them, in the order they parse, before translate-toolkit's:
a Fluent or ICU message first, as a whole, then {{count}} before a
brace or Java {0} takes its inside, %{name} and ${name} before
{name}, and %1$@ before Qt's %1. Phrase's {1} before anything else
reads it as a number in braces."""

parsers = [cls.parse for cls in PLACEABLES]


def _model_of(store, unit):
    """The file's model in store that holds unit: in a stream of
    several files, the one unit is from."""
    segments = getattr(store, 'segments', None)
    if segments is None:
        return store
    for segment in segments:
        if unit is not None and segment.local_index(unit) is not None:
            return segment.model
    return getattr(store, 'current_model', None)


def format_of(store, unit=None):
    """The key in L{FORMAT_PARSERS} of the file of unit in store (a
    L{virtaal.models.storemodel.StoreModel} or a stream of them), or
    None. A model's placeable_format, if set, says: such as a
    translation server's work copy built in XLIFF from strings of
    another format."""
    model = _model_of(store, unit)
    chosen = getattr(model, 'placeable_format', None)
    if chosen:
        return chosen
    pair = getattr(model, 'pair', None)
    if pair is None:
        return _bilingual_format(model)
    key = pair.converter.id
    if key != 'json':
        return key
    if str(getattr(pair, 'target', '')).lower().endswith('.arb'):
        return 'arb'
    flavour = getattr(pair, 'json_flavour', None)
    if flavour is None:
        flavour = json_flavour(_sample(pair))
        pair.json_flavour = flavour
    return flavour


def _bilingual_format(model):
    """'mxliff' for a Phrase XLIFF file, whatever its extension, else
    None."""
    document = getattr(getattr(model, '_trans_store', None), 'document', None)
    root = document.getroot() if document is not None else None
    if root is not None and any(
        str(uri).startswith(PHRASE_NAMESPACE) for uri in root.nsmap.values()
    ):
        return 'mxliff'
    return None


# Which JSON a file is: from what its strings hold

JSON_FLAVOURS = (
    (
        'json',  # i18next
        (
            (re.compile(r'\{\{[^{}]+\}\}'), 2),
            (re.compile(r'\$t\('), 3),
        ),
    ),
    (
        'json-vue',  # vue-i18n
        (
            (re.compile(r'(?<!\{)\{\s*[A-Za-z_$][\w.$]*\s*\}(?!\})|(?<!\{)\{\d+\}(?!\})'), 1),
            (re.compile(r'@(?:\.[A-Za-z]+)?:[\w(]'), 3),
            (re.compile(r'\s\|\s'), 2),
        ),
    ),
    (
        'json-icu',  # FormatJS and other ICU MessageFormat
        (
            (re.compile(r'\{\s*\w+\s*,\s*(?:plural|select|selectordinal)\s*,'), 4),
            (re.compile(r'\{\s*\w+\s*,\s*(?:number|date|time)\s*[,}]'), 3),
        ),
    ),
)
"""(format, ((pattern, weight), ...)) for each kind of JSON file: the
one whose patterns score most over a file's strings wins."""

JSON_SAMPLE = 200
"""How many units' strings a JSON file is judged by."""


def _sample(pair):
    """Up to L{JSON_SAMPLE} units' source and target texts of pair, and
    whether any is a plural (i18next's key_one/key_other)."""
    texts = []
    plural = False
    store = getattr(pair, 'store', None)
    for unit in store.units[:JSON_SAMPLE] if store is not None else ():
        plural = plural or unit.hasplural()
        for text in (unit.source, unit.target):
            texts.extend(str(form) for form in getattr(text, 'strings', [text]) if form)
    return texts, plural


def json_flavour(sample):
    """The L{JSON_FLAVOURS} format that best fits sample, (texts, has
    i18next plurals); i18next ('json') when none fits better."""
    texts, plural = sample
    scores = {}
    for fmt, patterns in JSON_FLAVOURS:
        scores[fmt] = sum(
            weight * len(regex.findall(t)) for regex, weight in patterns for t in texts
        )
    if plural:
        scores['json'] += 4
    best = max(scores.values())
    winners = [fmt for fmt, score in scores.items() if score == best]
    if best == 0 or len(winners) > 1:
        return 'json'
    return winners[0]


def unit_flags(unit):
    """The gettext flags of unit, such as python-brace-format."""
    flags = set()
    for line in getattr(unit, 'typecomments', None) or ():
        flags.update(flag.strip() for flag in line.lstrip('#,').split(','))
    return flags


def placeables_for(fmt, flags=()):
    """The classes of L{PLACEABLES} for text of format fmt, with the
    gettext flags flags, in parsing order."""
    wanted = set(EVERYWHERE) | set(FORMAT_PARSERS.get(fmt, ()))
    for flag in flags:
        wanted.update(FLAG_PARSERS.get(flag, ()))
    return [cls for cls in PLACEABLES if cls in wanted]


def parsers_for(fmt, flags=()):
    """The parse functions of L{placeables_for}."""
    return [cls.parse for cls in placeables_for(fmt, flags)]
