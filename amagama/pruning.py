#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Fewer words for a lookup's full-text match, with the same results.

A lookup matches sources containing any word of its query, then keeps those
whose TS_RANK passes the minimum. A common word ("file", "use") matches
hundreds of thousands of sources that are read only to be discarded. But
TS_RANK, for a query of words joined by OR, is at most 0.1 per word found
(with the weight TO_TSVECTOR gives every position, D, however often the word
occurs) over the number of distinct words in the query, and normalisation 32
makes that r / (r + 1). So passing the minimum needs at least some number of
the query's words, and any source with that many has at least one of the
rarest ones: matching only those finds the same sources, and reads far fewer.
More strongly, a source with m of the query's N words has at least two of its
N - m + 2 rarest, so matching pairs of those reads fewer again: the index
finds the sources with both words of a pair without reading the others.
"""

import itertools
import re

# What TS_RANK's normalisation 32 result is multiplied by: a query matching
# every word of the source ranks 100.
RANK_SCALE = 1744.93406073519
# The most a word adds to TS_RANK before dividing by the query's words: the
# weight of D, as sum(w / j^2) / 1.64493406685 (pi^2 / 6) is below w.
WORD_RANK = 0.1
# TS_RANK computes in single precision: allow for its rounding.
SLACK = 1e-4

_OPERAND = re.compile(r"'((?:[^']|'')*)'")


def operands(query):
    """The distinct words of query, a tsquery's text, as TS_RANK counts them."""
    return sorted({word.replace("''", "'") for word in _OPERAND.findall(query)})


def or_at_top(query):
    """Whether query, a tsquery's text, joins its parts with OR at the top:
    TS_RANK ranks only that way as above (AND and phrases differently)."""
    depth, quoted = 0, False
    for char in query:
        if char == "'":
            # A doubled quote inside a word toggles twice: no change.
            quoted = not quoted
        elif not quoted:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "|" and depth == 0:
                return True
    return False


def words_needed(words, min_rank):
    """The fewest of a query's words a source must contain to rank above
    min_rank."""
    # x / (1 + x) * RANK_SCALE > min_rank needs x > min_rank / (RANK_SCALE - min_rank),
    # where x = WORD_RANK * found / words.
    if min_rank >= RANK_SCALE:
        return words + 1
    least = min_rank / (RANK_SCALE - min_rank) * words / (WORD_RANK * (1 + SLACK))
    needed = int(least) + 1
    return max(1, min(needed, words))


# More pairs than this cost more to match in the index than they save: on
# the live data (#3476) pairs were faster up to 66 (13 words), single words
# from 78 (14 words).
MAX_PAIRS = 66
# Fewer words than this always need just one (with a minimum of 35): nothing
# to prune.
FEWEST_WORDS = 5


def quote(word):
    return "'%s'" % word.replace("'", "''")


def pruned(query, counts, min_rank, together=1):
    """A tsquery's text that every source of query (a tsquery's text) ranking
    above min_rank matches: any together of the rarest words, by the number of
    sources with each word (counts, a word missing being rare), that each such
    source has that many of; or None when that is any one word of query."""
    if not or_at_top(query):
        return None
    words = operands(query)
    needed = words_needed(len(words), min_rank)
    together = min(together, needed)
    keep = len(words) - needed + together
    if together == 1 and keep >= len(words):
        return None
    rarest = sorted(sorted(words, key=lambda word: (counts.get(word, 0), word))[:keep])
    if together == 1:
        return " | ".join(quote(word) for word in rarest)
    return " | ".join("( %s )" % " & ".join(quote(word) for word in group)
                      for group in itertools.combinations(rarest, together))


def lookup_filter(query, counts, min_rank):
    """pruned(query, ...) of pairs while there are at most MAX_PAIRS of them,
    else of single words: whichever the live data found faster."""
    words = operands(query)
    keep = len(words) - words_needed(len(words), min_rank) + 2
    if keep * (keep - 1) // 2 <= MAX_PAIRS:
        found = pruned(query, counts, min_rank, together=2)
        if found is not None and "&" in found:
            return found
    return pruned(query, counts, min_rank)
