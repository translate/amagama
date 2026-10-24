"""Split multi-sentence messages into sentence pairs, so sentences match.

A message whose source and target have the same number of sentences adds
each pair, in order, beside the whole message. Others stay whole.
"""

import re

from translate.lang import factory
from translate.storage.base import TranslationStore, TranslationUnit

_TAG = re.compile(r"<(/?)([A-Za-z][\w:.-]*)[^<>]*?(/?)>")
_WRAPPED = re.compile(r"\s*<([A-Za-z][\w:.-]*)(?:\s[^<>]*)?>(.*)</\1>\s*", re.DOTALL)


def _balanced(text):
    """Whether every tag opened in text is closed in it, in order."""
    open_tags = []
    for match in _TAG.finditer(text):
        closing, name, self_closing = match.groups()
        if self_closing:
            continue
        if not closing:
            open_tags.append(name)
        elif not open_tags or open_tags.pop() != name:
            return False
    return not open_tags


def _unwrap(text):
    """text without an element that encloses all of it (LibreOffice's <ahelp>)."""
    match = _WRAPPED.fullmatch(text)
    if match and _balanced(match.group(2)):
        return match.group(2)
    return text


def _sentences(language, text):
    return [sentence.strip() for sentence in language.sentences(text) if sentence.strip()]


def sentence_pairs(source, target, source_lang, target_lang):
    """The (source, target) sentence pairs of a message, or [] to keep it whole."""
    sources = _sentences(factory.getlanguage(source_lang), _unwrap(source))
    targets = _sentences(factory.getlanguage(target_lang), _unwrap(target))
    if len(sources) < 2 or len(sources) != len(targets):
        return []
    if not all(_balanced(sentence) for sentence in sources + targets):
        return []
    return list(zip(sources, targets))


def segmented(store, source_lang, target_lang, usable):
    """The usable units of store, each followed by its sentence pairs."""
    result = TranslationStore()
    result.filename = getattr(store, "filename", None)
    for unit in store.units:
        if not usable(unit):
            continue
        pairs = [(str(unit.source), str(unit.target))]
        pairs += sentence_pairs(str(unit.source), str(unit.target), source_lang, target_lang)
        for source, target in pairs:
            plain = TranslationUnit(source)
            plain.target = target
            result.addunit(plain)
    return result
