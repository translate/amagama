#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Which sources a lookup's suggestions may come from, and how much each counts."""

from fnmatch import fnmatchcase
import math

MAX_WEIGHT = 10


def _matches(name, globs):
    return any(fnmatchcase(name, glob) for glob in globs)


class SourceOptions(object):
    """Block-name globs to leave out (exclude) or keep (only), and (glob, weight) pairs."""

    def __init__(self, exclude=(), only=(), weights=()):
        self.exclude = list(exclude)
        self.only = list(only)
        self.weights = list(weights)

    @classmethod
    def from_args(cls, args):
        """From ?exclude=, ?only= and ?weight=; ValueError for a weight that isn't glob:number."""
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
        return cls(items('exclude'), items('only'), weights)

    def __bool__(self):
        return bool(self.exclude or self.only or self.weights)

    def kept(self, names):
        """The names a suggestion keeps, or None if it is left out.

        A suggestion stays while any of its sources does; one with no
        recorded source is left out only by only.
        """
        kept = [name for name in names
                if (not self.only or _matches(name, self.only))
                and not _matches(name, self.exclude)]
        if not kept and (names or self.only):
            return None
        return kept

    def weight(self, names):
        """The best weight of names: that of each name's first matching glob, else 1."""
        return max((next((weight for glob, weight in self.weights if fnmatchcase(name, glob)), 1.0)
                    for name in names), default=1.0)

    def choose(self, results, max_candidates):
        """The results kept, best quality times weight first, at most max_candidates."""
        chosen = []
        for result in results:
            kept = self.kept(result['sources'])
            if kept is not None:
                chosen.append(dict(result, sources=kept))
        chosen.sort(key=lambda result: result['quality'] * self.weight(result['sources']),
                    reverse=True)
        return chosen[:max_candidates]
