#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Pair a monolingual translation file with its English by message id.

Monolingual formats (Android strings.xml, PHP and INI language files, JSON
message catalogues) keep each language in its own file, keyed by an id. A
reader gives each message's text, with no English; the English is the same
id in the template file.
"""

import fnmatch
import re

from translate.storage.base import TranslationStore, TranslationUnit


def text(unit):
    """A monolingual unit's text: some readers put it in target, some in source."""
    return unit.target or unit.source


def is_plural(unit):
    return any(len(getattr(value, "strings", [])) > 1 for value in (unit.source, unit.target))


def excluded(unit, patterns):
    """Whether unit's id matches one of patterns (globs, compared without the
    leading "." JSON ids have)."""
    uid = unit.getid() or ""
    return any(fnmatch.fnmatchcase(uid.lstrip("."), pattern) for pattern in patterns)


def without_ids(store, patterns):
    """store, leaving out units whose ids match patterns."""
    store.units = [unit for unit in store.units if not excluded(unit, patterns)]
    return store


def language_segment(ids):
    """The position of the "."-separated id segment that is the same English
    language code in every one of a template's ids, or None."""
    split = [uid.split(".") for uid in ids]
    if not split:
        return None
    for position in range(min(len(segments) for segments in split)):
        values = {segments[position] for segments in split}
        if len(values) == 1 and re.fullmatch(r"en(?:[-_][A-Za-z0-9]+)*", values.pop()):
            return position
    return None


def without_segment(uid, position):
    segments = uid.split(".")
    return ".".join(segments[:position] + segments[position + 1:])


def paired_store(translation, template, exclude_ids=(), language_in_ids=False):
    """A store of English source and translated target units.

    Leaves out messages with no English or no text, plurals, and ids matching
    exclude_ids. With language_in_ids, ids name their file's language (CLDR's
    main.de...., Rails' de....): the segment where the template's ids name
    English is left out of every id before they are matched.
    """
    def key(uid):
        return uid
    if language_in_ids:
        position = language_segment([unit.getid() or "" for unit in template.units])
        if position is not None:
            def key(uid):
                return without_segment(uid or "", position)
    english = {key(unit.getid()): unit for unit in template.units}
    store = TranslationStore()
    for unit in translation.units:
        if excluded(unit, exclude_ids):
            continue
        source = english.get(key(unit.getid()))
        if source is None or is_plural(unit) or is_plural(source):
            continue
        target = text(unit)
        if not target or not text(source):
            continue
        paired = TranslationUnit(str(text(source)))
        paired.target = str(target)
        store.addunit(paired)
    return store
