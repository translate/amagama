#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Which sources a lookup's suggestions may come from, and how much each counts."""

from fnmatch import fnmatchcase
import functools
import math

from license_expression import ExpressionError, LicenseSymbol, LicenseWithExceptionSymbol, Licensing

MAX_WEIGHT = 10
LICENSING = Licensing()


def _matches(name, globs):
    return any(fnmatchcase(name, glob) for glob in globs)


@functools.lru_cache(maxsize=1024)
def licence_allowed(expression, allowed):
    """Whether the SPDX expression can be met with only the allowed licences.

    allowed is globs, those starting with ! leaving licences out; with only
    those, every other licence is allowed. SPDX identifiers ignore case. An
    exception only adds permissions, so WITH needs just its licence.
    """
    globs = [glob.lower() for glob in allowed]
    excluded = [glob[1:] for glob in globs if glob.startswith('!')]
    included = [glob for glob in globs if not glob.startswith('!')] or ['*']

    def met(node):
        if isinstance(node, LicenseWithExceptionSymbol):
            node = node.license_symbol
        if isinstance(node, LicenseSymbol):
            key = node.key.lower()
            return _matches(key, included) and not _matches(key, excluded)
        if isinstance(node, LICENSING.OR):
            return any(met(arg) for arg in node.args)
        return all(met(arg) for arg in node.args)
    if not expression:
        return False
    try:
        return met(LICENSING.parse(expression))
    except ExpressionError:
        return False


class SourceOptions(object):
    """Block-name globs to exclude or keep only, (glob, weight) pairs and licence globs."""

    def __init__(self, exclude=(), only=(), weights=(), licences=()):
        self.exclude = list(exclude)
        self.only = list(only)
        self.weights = list(weights)
        self.licences = tuple(licences)

    @classmethod
    def from_args(cls, args):
        """From ?exclude=, ?only=, ?weight= and ?licence=; ValueError for a bad weight."""
        def items(name):
            return [item.strip() for item in args.get(name, '').split(',') if item.strip()]

        weights = []
        for item in items('weight'):
            glob, colon, value = item.rpartition(':')
            weight = float(value) if colon and glob else math.nan
            if not 0 <= weight <= MAX_WEIGHT:
                raise ValueError("weight must be glob:number from 0 to %d, not %s"
                                 % (MAX_WEIGHT, item))
            weights.append((glob, weight))
        return cls(items('exclude'), items('only'), weights, items('licence'))

    def __bool__(self):
        return bool(self.exclude or self.only or self.weights or self.licences)

    def kept(self, names, licences=None):
        """The names a suggestion keeps, or None if it is left out.

        licences gives each block's licence, for the licence globs. A
        suggestion stays while any of its sources does; one with no recorded
        source is left out only by only or licence globs.
        """
        licences = licences or {}
        kept = [name for name in names
                if (not self.only or _matches(name, self.only))
                and not _matches(name, self.exclude)
                and (not self.licences or licence_allowed(licences.get(name), self.licences))]
        if not kept and (names or self.only or self.licences):
            return None
        return kept

    def weight(self, names, defaults=None):
        """The best weight of names: each one's first matching glob's, else its default, else 1."""
        defaults = defaults or {}
        return max((next((weight for glob, weight in self.weights if fnmatchcase(name, glob)),
                         defaults.get(name, 1.0))
                    for name in names), default=1.0)

    def choose(self, results, max_candidates, defaults=None, licences=None):
        """The results kept, at most max_candidates: best quality times weight
        first, then the one more sources supplied, then in the given order."""
        chosen = []
        for result in results:
            kept = self.kept(result['sources'], licences)
            if kept is not None:
                chosen.append(dict(result, sources=kept))
        chosen.sort(key=lambda result: (
            result['quality'] * self.weight(result['sources'], defaults), len(result['sources'])),
            reverse=True)
        return chosen[:max_candidates]
