#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Plural messages as amaGama stores them (#3351).

A plural (PO msgid_plural, Qt numerus, XLIFF gettext plurals) has a singular
and a plural English text and a translation for each of the target
language's forms. It's stored as two ordinary translations, so a lookup by
either English text finds it: the singular with the form for one, the plural
with the form the language uses for most counts. Both keep all the forms, in
the file's order, and how many the language has.
"""

import collections
import functools
import gettext

from translate.lang import data

# The counts a form is chosen over: the plural English is for "%d files" with
# any of them.
COUNTS = range(2, 1001)


@functools.lru_cache(maxsize=256)
def _forms_of(expression):
    """(form for one, form for most counts) under a Plural-Forms expression;
    None if it isn't one."""
    try:
        plural = gettext.c2py(expression)
        counts = collections.Counter(int(plural(n)) for n in COUNTS)
        one, most = int(plural(1)), counts.most_common(1)[0][0]
        return (one, most) if one >= 0 and most >= 0 else None
    except (ValueError, ArithmeticError, RecursionError, TypeError):
        return None


def _table_rule(lang):
    """(nplurals, expression) of lang in Translate Toolkit's table, trying it
    as given, with its region in capitals, and simpler codes."""
    code = str(lang or "")
    candidates = [code]
    base, _, region = code.partition("_")
    if region:
        candidates.append("%s_%s" % (base, region.upper()))
    while code:
        simpler = data.simplercode(code)
        if not simpler or simpler == code:
            break
        candidates.append(simpler)
        code = simpler
    for candidate in candidates:
        found = data.languages.get(candidate)
        if found:
            return int(found[1]), found[2]
    return None


def rule(store, lang):
    """(nplurals, expression) for store's plurals: its Plural-Forms header if
    it has one that works, else lang's in Translate Toolkit's table; None if
    neither is known."""
    getheaderplural = getattr(store, "getheaderplural", None)
    if getheaderplural:
        nplurals, expression = getheaderplural()
        try:
            forms = _forms_of(expression) if expression else None
            # Only one whose forms are among its nplurals.
            if forms and 0 < int(nplurals) and max(forms) < int(nplurals):
                return int(nplurals), expression
        except ValueError:
            pass
    return _table_rule(lang)


def entries(unit, plural_rule):
    """The (source, target) pairs to store for a plural unit, and its forms
    and nplurals; None for a unit that isn't a plural."""
    try:
        targets = getattr(unit.target, "strings", None)
    except ValueError:
        return None
    sources = getattr(unit.source, "strings", None) or [str(unit.source)]
    # Only a plural has more than one form (whatever the store's hasplural()).
    if not targets or len(targets) < 2:
        return None
    forms = [str(form) for form in targets]
    one, most = (_forms_of(plural_rule[1]) if plural_rule else None) or (0, 1)
    # A form the file hasn't (fewer than its rule has) leaves its pair out,
    # rather than pairing another form.
    pairs = [(str(sources[0]), forms[one])] if one < len(forms) else []
    # Qt numerus has one English text: the singular pair only.
    if len(sources) > 1 and str(sources[1]) != str(sources[0]) and most < len(forms):
        pairs.append((str(sources[1]), forms[most]))
    nplurals = plural_rule[0] if plural_rule else len(forms)
    return pairs, forms, nplurals
