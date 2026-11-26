#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""The abstract form of a string: each placeable replaced by one marker
character for its class, and an accelerator removed, so that strings
differing only in placeables or accelerator style match.

Translation memory scores matches on the abstract form and rebuilds a
match's target with the current unit's own placeables and accelerator.
The raw text is always kept; an abstract is never turned back into text.

This module needs only translate-toolkit and the standard library, so the
local TM server process (and amaGama, from a copy) can use it.
"""

import re
from collections import Counter
from functools import lru_cache
from typing import NamedTuple

from translate.storage.placeables import StringElem, general, parse

from amagama.vendored import formatplaceables as fp
from amagama.vendored import textfold

ABSTRACTION_VERSION = 2
"""Changes whenever an abstract of the same text could change: the parser
list, the parser fixes, the accelerator rule or the ellipsis rule."""

VARIABLE = '\ue001'
TAG_OPEN = '\ue002'
TAG_CLOSE = '\ue003'
TAG_EMPTY = '\ue004'
ENTITY = '\ue005'
URL = '\ue006'
EMAIL = '\ue007'
FILE = '\ue008'
NUMBER = '\ue009'
OPTION = '\ue00a'


class _UrlPlaceable(general.UrlPlaceable):
    """translate-toolkit's URL, which may also end right before a
    sentence's punctuation: without that, "https://example.org." is
    "https:" and a file path."""

    regex = re.compile(
        r"""
    ((((news|nttp|file|https?|ftp|irc)://)
    |((www|ftp)[-A-Za-z0-9]*\.))
    ([-A-Za-z0-9]+(\.[-A-Za-z0-9]+)*)
    |(\d{1,3}(\.\d{1,3}){3,3}))
    (:[0-9]{1,5})?
    (/[-A-Za-z0-9_\$\.\+\!\*\(\),;:@&=\?/~\#\%]*)?
    (?=[.,;:!?]*($|\s|([]'}>\),\"])))
    """,
        re.VERBOSE,
    )


# Fixed, and not the user's placeable settings, so stored abstracts don't
# drift between users and sessions. Any file's: {name} adapts like %s
# wherever it is. Virtaal's first, in their order, then
# translate-toolkit's in its order.
_CLASSES = (
    (fp.I18NextPlaceable, VARIABLE),
    # Before Fluent's, whose { name } would take the braces of %{name}.
    (fp.RubyPlaceable, VARIABLE),
    (fp.FluentReferencePlaceable, VARIABLE),
    (fp.ApplePlaceable, VARIABLE),
    (fp.DotNetPlaceable, VARIABLE),
    (fp.BracePlaceable, VARIABLE),
    (general.XMLTagPlaceable, None),
    (general.XMLEntityPlaceable, ENTITY),
    (general.PythonFormattingPlaceable, VARIABLE),
    (general.JavaMessageFormatPlaceable, VARIABLE),
    (general.FormattingPlaceable, VARIABLE),
    (general.QtFormattingPlaceable, VARIABLE),
    (_UrlPlaceable, URL),
    (general.FilePlaceable, FILE),
    (general.EmailPlaceable, EMAIL),
    (general.OptionPlaceable, OPTION),
    (general.NumberPlaceable, NUMBER),
)
_PARSERS = [cls.parse for cls, _marker in _CLASSES]
_MARKERS = {cls: marker for cls, marker in _CLASSES}

# A string holding one of these is left raw, so a marker is never ambiguous.
_RESERVED_RE = re.compile('[\ue000-\ue0ff]')

# "100% done": a space flag right before a letter is a word, not "% d".
_SPACE_FLAG_RE = re.compile(r'%[ ][A-Za-z]\Z')
_URL_TRAILING = '.,;:!?)'

_TAG_RE = re.compile(r'<\s*(/)?\s*([^\s/>]*)[^>]*?(/)?\s*>\Z', re.DOTALL)

# One of these before a letter or digit; a doubled one is a literal.
_ACCEL_RE = re.compile(r'__|&&|~~|([_&~])(?=[^\W_])')

_ELLIPSIS = '...'


class Placeable(NamedTuple):
    marker: str
    """The class's marker character."""
    raw: str
    start: int
    end: int
    pair: int | None = None
    """For a tag, the index of its open or close partner, if it has one."""


class Rebuilt(NamedTuple):
    text: str
    rebuilt: int
    """Target placeables replaced with a current placeable of different text."""
    unresolved: int
    """Target placeables left as they were, with no current counterpart."""
    accelerator: bool
    """Whether the target's accelerator marker was changed."""


class Adapted(NamedTuple):
    target: str
    quality: int
    raw_quality: int
    rebuilt: Rebuilt


def _tag_marker(raw):
    if raw.startswith(('<!', '<?')):
        return TAG_EMPTY, None
    found = _TAG_RE.match(raw)
    if found is None:
        return TAG_EMPTY, None
    closing, name, empty = found.groups()
    if closing:
        return TAG_CLOSE, name
    if empty:
        return TAG_EMPTY, None
    return TAG_OPEN, name


def _walk(elem):
    """(placeable class or None, text) for each top-level piece."""
    for sub in elem.sub:
        if isinstance(sub, str):
            yield None, sub
        elif type(sub) is StringElem:
            yield from _walk(sub)
        else:
            yield type(sub), str(sub)


def _pieces(text):
    """(marker or None, raw, tag name) per piece, after the parser fixes."""
    pieces = []
    for cls, raw in _walk(parse(text, _PARSERS)):
        if not raw:
            continue
        marker = _MARKERS.get(cls) if cls is not None else None
        name = None
        if cls is general.XMLTagPlaceable:
            marker, name = _tag_marker(raw)
        elif marker == VARIABLE and _SPACE_FLAG_RE.match(raw):
            marker = None
        if marker == URL:
            stripped = raw
            while stripped and stripped[-1] in _URL_TRAILING:
                if stripped[-1] == ')' and stripped.count('(') >= stripped.count(')'):
                    break
                stripped = stripped[:-1]
            if stripped != raw:
                pieces.append((URL, stripped, None))
                pieces.append((None, raw[len(stripped) :], None))
                continue
        pieces.append((marker, raw, name))
    return pieces


class _Analysis(NamedTuple):
    pieces: tuple
    placeables: tuple
    accelerator: tuple | None
    raw: bool = False


@lru_cache(maxsize=2048)
def _analyse(text):
    if _RESERVED_RE.search(text):
        return _Analysis(((None, text),), (), None, raw=True)
    pieces = []
    placeables = []
    open_tags = {}
    offset = 0
    for marker, raw, name in _pieces(text):
        end = offset + len(raw)
        if marker is None:
            if pieces and pieces[-1][0] is None:
                pieces[-1] = (None, pieces[-1][1] + raw)
            else:
                pieces.append((None, raw))
        else:
            pair = None
            index = len(placeables)
            if marker == TAG_OPEN:
                open_tags.setdefault(name, []).append(index)
            elif marker == TAG_CLOSE and open_tags.get(name):
                pair = open_tags[name].pop()
                placeables[pair] = placeables[pair]._replace(pair=index)
            placeables.append(Placeable(marker, raw, offset, end, pair))
            pieces.append((marker, raw))
        offset = end
    return _Analysis(tuple(pieces), tuple(placeables), _find_accelerator(text, pieces))


def _find_accelerator(text, pieces):
    """(offset, marker character) of the one accelerator in the text
    pieces of text, or None for none or several."""
    text_at = set()
    offset = 0
    for marker, raw in pieces:
        if marker is None:
            text_at.update(range(offset, offset + len(raw)))
        offset += len(raw)
    # Over the whole text: "_1" is an accelerator though "1" is a number.
    accels = []
    for found in _ACCEL_RE.finditer(text):
        if found.group(1) and found.start(1) in text_at:
            accels.append((found.start(1), found.group(1)))
    return accels[0] if len(accels) == 1 else None


def placeables(text):
    """The placeables of text, in order, as L{Placeable}s."""
    return list(_analyse(str(text)).placeables)


def accelerator(text):
    """(offset, marker character) of text's accelerator, or None.

    Style-free: text has an accelerator when, outside its placeables, it
    holds exactly one C{_}, C{&} or C{~} before a letter or digit."""
    return _analyse(str(text)).accelerator


def abstract(text):
    """(abstract text, placeables) for text."""
    analysis = _analyse(str(text))
    if analysis.raw:
        return str(text), []
    accel = analysis.accelerator
    out = []
    offset = 0
    for marker, raw in analysis.pieces:
        if marker is not None:
            out.append(marker)
        else:
            piece = raw
            if accel is not None and offset <= accel[0] < offset + len(raw):
                cut = accel[0] - offset
                piece = raw[:cut] + raw[cut + 1 :]
            out.append(piece.replace(_ELLIPSIS, '…'))
        offset += len(raw)
    return ''.join(out), list(analysis.placeables)


def abstract_offsets(text):
    """(abstract text, offsets) for text, where offsets[i] is the
    (start, end) in text of the abstract's character i: a placeable's
    marker covers the placeable, "…" the "..." it stands for."""
    text = str(text)
    analysis = _analyse(text)
    if analysis.raw:
        return text, [(i, i + 1) for i in range(len(text))]
    accel = analysis.accelerator
    out = []
    offsets = []
    offset = 0
    for marker, raw in analysis.pieces:
        end = offset + len(raw)
        if marker is not None:
            out.append(marker)
            offsets.append((offset, end))
        else:
            _text_offsets(text, offset, end, accel, out, offsets)
        offset = end
    return ''.join(out), offsets


def _text_offsets(text, start, end, accel, out, offsets):
    i = start
    while i < end:
        if accel is not None and i == accel[0]:
            i += 1
        elif text.startswith(_ELLIPSIS, i) and i + len(_ELLIPSIS) <= end:
            out.append('…')
            offsets.append((i, i + len(_ELLIPSIS)))
            i += len(_ELLIPSIS)
        else:
            out.append(text[i])
            offsets.append((i, i + 1))
            i += 1


def _by_class(placeables_):
    classes = {}
    for placeable in placeables_:
        classes.setdefault(placeable.marker, []).append(placeable)
    return classes


def rebuild(target, source_placeables, query_placeables, query_accelerator=None, accel_markers=()):
    """Target rebuilt with the query's placeables and accelerator, as a
    L{Rebuilt}.

    The nth placeable of a class in the match source stands for the nth of
    that class in the query. A target placeable takes the query placeable
    for the source placeable with its own text (the kth occurrence of a
    text for the kth), which keeps reordering and repeats. One with no
    such source placeable, or whose source placeable has no counterpart in
    the query, is kept and counted as unresolved.

    If the query has an accelerator whose marker is one of accel_markers,
    the target's accelerator, if it has exactly one, takes that marker."""
    target = str(target)
    analysis = _analyse(target)
    query_classes = _by_class(query_placeables)
    source_classes = _by_class(source_placeables)
    source_by_text = {}
    for placeable in source_placeables:
        source_by_text.setdefault(placeable.raw, []).append(placeable)

    def current_for(source_placeable):
        n = source_classes[source_placeable.marker].index(source_placeable)
        current = query_classes.get(source_placeable.marker, [])
        return current[n] if n < len(current) else None

    accel = analysis.accelerator
    new_marker = None
    if query_accelerator is not None and accel is not None:
        wanted = query_accelerator[1]
        if wanted in accel_markers and wanted != accel[1]:
            new_marker = wanted

    out = []
    rebuilt = unresolved = 0
    target_seen = {}
    offset = 0
    for marker, raw in analysis.pieces:
        if marker is None:
            if new_marker is not None and offset <= accel[0] < offset + len(raw):
                cut = accel[0] - offset
                out.append(raw[:cut] + new_marker + raw[cut + 1 :])
            else:
                out.append(raw)
            offset += len(raw)
            continue
        offset += len(raw)
        k = target_seen.get(raw, 0)
        target_seen[raw] = k + 1
        same_text = source_by_text.get(raw)
        current = current_for(same_text[min(k, len(same_text) - 1)]) if same_text else None
        if current is None:
            unresolved += 1
            out.append(raw)
            continue
        if current.raw != raw:
            rebuilt += 1
        out.append(current.raw)
    return Rebuilt(''.join(out), rebuilt, unresolved, new_marker is not None)


def transfer(text, from_source, to_source):
    """text, which goes with from_source, given to_source's placeables in
    place of from_source's ("Maak %s oop" from "Open %s" to "Open %d" is
    "Maak %d oop"). None unless that changes text and maps cleanly: both
    sources have as many placeables of each class, and each of text's
    has its counterpart."""
    from_placeables = placeables(from_source)
    to_placeables = placeables(to_source)
    if Counter(p.marker for p in from_placeables) != Counter(p.marker for p in to_placeables):
        return None
    result = rebuild(text, from_placeables, to_placeables)
    if result.unresolved or not result.rebuilt:
        return None
    return result.text


def similarity(a, b, lang=None):
    """Levenshtein similarity of a and b in lang, from 0 to 100, past
    case and Unicode form (L{textfold})."""
    return textfold.comparer(lang).similarity(a, b, 0)


REBUILT_PENALTY = 1
UNRESOLVED_PENALTY = 5
UNMATCHED_PENALTY = 8
"""Per placeable that one of query and source has and the other lacks:
on the abstracts it is a one-character edit, too cheap on its own."""


def _unmatched(query_placeables, source_placeables):
    query = Counter(placeable.marker for placeable in query_placeables)
    source = Counter(placeable.marker for placeable in source_placeables)
    return (query - source).total() + (source - query).total()


def adapt(query, source, target, raw_quality, accel_markers=(), lang=None):
    """A TM match for query (with source and target) adapted to it, as an
    L{Adapted}: the target rebuilt with query's own placeables and
    accelerator, and a quality that is the better of raw_quality and the
    abstract forms' similarity less the penalties for the rebuild and for
    placeables that query and source don't share, in query's language
    lang. Only a source identical to query scores 100."""
    query, source, target = str(query), str(source), str(target)
    query_abstract, query_placeables = abstract(query)
    source_abstract, source_placeables = abstract(source)
    result = rebuild(target, source_placeables, query_placeables, accelerator(query), accel_markers)
    adjusted = round(
        similarity(query_abstract, source_abstract, lang)
        - REBUILT_PENALTY * result.rebuilt
        - UNRESOLVED_PENALTY * result.unresolved
        - UNMATCHED_PENALTY * _unmatched(query_placeables, source_placeables)
    )
    if query != source:
        adjusted = min(adjusted, 99)
    raw_quality = int(raw_quality or 0)
    return Adapted(result.text, max(raw_quality, adjusted), raw_quality, result)
