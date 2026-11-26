#
# Copyright (C) Virtaal contributors.
#
# This file is part of Virtaal. It is distributed under the GPL2 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Comparing texts past case and Unicode form, as translation memory
matches sources: both in NFKC and case folded, so "OPEN FILE" matches
"Open file", "STRASSE" "Straße" and a full-width "ＯＫ" "OK". A
difference in case or form only costs L{CASE_PENALTY}. Accents are kept:
"café" and "cafe" stay a fuzzy match.

The language is that of the texts, and only changes the rule for:
  - Latin alphabets with a dotted and a dotless i (Turkish and
    Azerbaijani, as Unicode's SpecialCasing; Crimean Tatar and Gagauz;
    Kazakh and Tatar written in Latin): I folds to ı and İ to i.
  - Greek and Ancient Greek: accents are ignored when either text is in
    capitals, which are normally written without them.

Needs only translate-toolkit and the standard library, for the local TM
server process.
"""

import re
import unicodedata
from functools import lru_cache

from translate.search.lshtein import LevenshteinComparer

CASE_PENALTY = 3
"""What a difference only in case or Unicode form takes off a score."""

TURKIC_I = 'turkic-i'
GREEK_CAPITALS = 'greek-capitals'

_RULES = {
    ('tr', 'latn'): TURKIC_I,
    ('az', 'latn'): TURKIC_I,
    ('crh', 'latn'): TURKIC_I,
    ('gag', 'latn'): TURKIC_I,
    ('kk', 'latn'): TURKIC_I,
    ('tt', 'latn'): TURKIC_I,
    ('el', 'grek'): GREEK_CAPITALS,
    ('grc', 'grek'): GREEK_CAPITALS,
}
"""The rule of each (language, script)."""

_DEFAULT_SCRIPTS = {
    'tr': 'latn',
    'az': 'latn',
    'crh': 'latn',
    'gag': 'latn',
    'kk': 'cyrl',
    'tt': 'cyrl',
    'el': 'grek',
    'grc': 'grek',
}
"""The script of a language code without one."""

_SCRIPT_NAMES = {'latin': 'latn', 'cyrillic': 'cyrl'}
"""gettext's script modifiers, as in sr@latin."""

_TURKIC_CASE = str.maketrans({'I': 'ı', 'İ': 'i'})
_TURKIC_UPPER = str.maketrans({'i': 'İ', 'ı': 'I'})
_MARKS = dict.fromkeys(
    c
    for start, end in ((0x300, 0x370), (0x1AB0, 0x1B00), (0x1DC0, 0x1E00), (0x20D0, 0x2100))
    for c in range(start, end)
)
"""The combining diacritical marks."""
_LOOSE = {**_MARKS, ord('ı'): 'i'}


def _script(subtags):
    for subtag in subtags:
        subtag = _SCRIPT_NAMES.get(subtag, subtag)
        if len(subtag) == 4 and subtag.isalpha():
            return subtag
    return None


@lru_cache(maxsize=64)
def _rule(lang):
    primary, *rest = re.split('[-_@.]', lang.lower())
    return _RULES.get((primary, _script(rest) or _DEFAULT_SCRIPTS.get(primary)))


def rule(lang):
    """The rule (L{TURKIC_I}, L{GREEK_CAPITALS}) for texts in lang, a
    language code or a list whose first is the language, or None."""
    if isinstance(lang, (list, tuple)):
        lang = lang[0] if lang else None
    return _rule(str(lang)) if lang else None


def is_capitals(text):
    return text.isupper()


def strip_accents(text):
    """text without its combining marks."""
    return unicodedata.normalize('NFC', unicodedata.normalize('NFD', text).translate(_MARKS))


def fold(text, lang=None, accents=True):
    """text in NFKC and case folded with lang's rule; without its
    accents unless accents."""
    return _fold(text, rule(lang), accents)


def _fold(text, rule, accents):
    if rule == TURKIC_I:
        text = unicodedata.normalize('NFKC', text).translate(_TURKIC_CASE)
    elif text.isascii():
        return text.lower()
    text = unicodedata.normalize('NFKC', unicodedata.normalize('NFKC', text).casefold())
    return text if accents else strip_accents(text)


def fold_pair(a, b, lang=None):
    """a and b folded to compare them: in Greek without accents if
    either is in capitals."""
    return _fold_pair(a, b, rule(lang))


def _fold_pair(a, b, rule):
    accents = not (rule == GREEK_CAPITALS and (is_capitals(a) or is_capitals(b)))
    return _fold(a, rule, accents), _fold(b, rule, accents)


def loose(text):
    """text folded for every language at once, accents and the dot of i
    taken out: the same for any texts that fold the same in some
    language. For a cheap test first."""
    if text is None:
        return None
    text = unicodedata.normalize('NFKD', unicodedata.normalize('NFKD', text).casefold())
    return text.translate(_LOOSE)


def case_only_difference(a, b, lang=None):
    """Whether a and b differ but fold the same."""
    a, b = unicodedata.normalize('NFC', a), unicodedata.normalize('NFC', b)
    if a == b:
        return False
    fa, fb = fold_pair(a, b, lang)
    return fa == fb


def lower(text, lang=None):
    if rule(lang) == TURKIC_I:
        text = text.translate(_TURKIC_CASE)
    return text.lower()


def upper(text, lang=None):
    if rule(lang) == TURKIC_I:
        text = text.translate(_TURKIC_UPPER)
    return text.upper()


def case_forms(word, lang=None):
    """The forms in which word may be written in another case or form
    (lower, upper, capitalised, ß for ss and the other way), word first."""
    word = unicodedata.normalize('NFC', word)
    forms = [word]
    for base in dict.fromkeys((word, unicodedata.normalize('NFKC', word))):
        low = lower(base, lang)
        cased = [low, upper(base, lang), upper(base[:1], lang) + low[1:]]
        cased += [form.replace('ss', 'ß') for form in cased if 'ss' in form]
        if rule(lang) == GREEK_CAPITALS:
            cased.append(strip_accents(cased[1]))
        forms += cased
    return list(dict.fromkeys(forms))


class Comparer:
    """A LevenshteinComparer on folded texts for lang: a difference in
    case or form only costs L{CASE_PENALTY}; only identical texts score
    100."""

    def __init__(self, lang=None, max_len=1000) -> None:
        self.lang = lang
        self.rule = rule(lang)
        self.levenshtein = LevenshteinComparer(max_len)

    def similarity(self, a, b, stoppercentage=40):
        """The similarity of a and b, from 0 to 100; below
        stoppercentage, any value below it."""
        a, b = _nfc(str(a)), _nfc(str(b))
        if a == b:
            return 100.0 if a else 0
        similarity = self.levenshtein.similarity
        fa, fb = _fold_pair(a, b, self.rule)
        if fa == a and fb == b:
            return similarity(a, b, stoppercentage)
        folded = similarity(fa, fb, stoppercentage)
        if folded < stoppercentage:
            return folded
        floor = folded - CASE_PENALTY
        return max(floor, similarity(a, b, max(stoppercentage, floor)))


def _nfc(text):
    return text if text.isascii() else unicodedata.normalize('NFC', text)


_comparers = {}


def comparer(lang=None):
    """A shared L{Comparer} for lang, with texts up to 1000 long."""
    key = rule(lang)
    if key not in _comparers:
        _comparers[key] = Comparer(lang)
    return _comparers[key]
