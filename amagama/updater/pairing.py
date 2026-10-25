"""Pair a monolingual translation file with its English by message id.

Monolingual formats (Android strings.xml, PHP and INI language files, JSON
message catalogues) keep each language in its own file, keyed by an id. A
reader gives each message's text, with no English; the English is the same
id in the template file.
"""

import fnmatch

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


def paired_store(translation, template, exclude_ids=()):
    """A store of English source and translated target units.

    Leaves out messages with no English or no text, plurals, and ids matching
    exclude_ids.
    """
    english = {unit.getid(): unit for unit in template.units}
    store = TranslationStore()
    for unit in translation.units:
        if excluded(unit, exclude_ids):
            continue
        source = english.get(unit.getid())
        if source is None or is_plural(unit) or is_plural(source):
            continue
        target = text(unit)
        if not target or not text(source):
            continue
        paired = TranslationUnit(str(text(source)))
        paired.target = str(target)
        store.addunit(paired)
    return store
