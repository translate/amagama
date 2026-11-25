#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import itertools
import random

import pytest

from amagama import pruning

RANK = "TS_RANK(TO_TSVECTOR('english', %s), %s::tsquery, 32) * 1744.93406073519"


def test_operands():
    assert pruning.operands("'file' | 'name' | 'file'") == ["file", "name"]
    assert pruning.operands("'don''t' | 'x'") == ["don't", "x"]
    assert pruning.operands("'foo-bar' <-> 'foo' <-> 'bar' | 'baz'") == [
        "bar", "baz", "foo", "foo-bar"]


@pytest.mark.parametrize("query, expected", [
    ("'a' | 'b'", True),
    ("'a'", False),
    ("'a' & 'b'", False),
    ("'foo-bar' <-> 'foo' <-> 'bar'", False),
    ("'foo-bar' <-> 'foo' <-> 'bar' | 'baz'", True),
    ("( 'a' | 'b' ) & 'c'", False),
    ("'a|b' & 'c'", False),
    ("'it''s' | 'x'", True),
])
def test_or_at_top(query, expected):
    assert pruning.or_at_top(query) is expected


def test_words_needed():
    # Measured on PostgreSQL: one of 6 words ranks 17.5, two 34.7, so 35 needs 2...
    assert pruning.words_needed(6, 35) == 2
    # ...and 4 of 18 (6 of them rank 34.7).
    assert pruning.words_needed(18, 35) == 4
    assert pruning.words_needed(1, 35) == 1
    assert pruning.words_needed(3, 2000) == 4


def test_pruned():
    counts = {"file": 170270, "name": 93295, "need": 36638, "origin": 10679,
              "machin": 9721, "xml": 5156}
    query = " | ".join("'%s'" % word for word in sorted(counts))
    # Two of the six are needed, so the five rarest find every one.
    assert pruning.pruned(query, counts, 35) == "'machin' | 'name' | 'need' | 'origin' | 'xml'"
    # A word not counted is rare.
    assert pruning.pruned(query + " | 'osmand'", counts, 35) == \
        "'machin' | 'name' | 'need' | 'origin' | 'osmand' | 'xml'"
    assert pruning.pruned("'file'", counts, 35) is None
    assert pruning.pruned("'file' & 'name'", counts, 35) is None
    assert pruning.pruned("'a' | 'b'", {}, 35) is None


def test_pruned_pairs():
    counts = {"a": 9, "b": 8, "c": 7, "d": 6, "e": 5, "f": 4}
    query = " | ".join("'%s'" % word for word in sorted(counts))
    # Two of six are needed: pairs of all six.
    pairs = pruning.pruned(query, counts, 35, together=2)
    assert pairs.count("&") == 15 and pairs.startswith("( 'a' & 'b' ) | ( 'a' & 'c' )")
    # With two words one is enough (it ranks 83), so nothing is pruned.
    assert pruning.pruned("'a' | 'b'", counts, 35, together=2) is None
    assert pruning.pruned("'a'", counts, 35, together=2) is None
    # Four of 18 are needed: pairs of the 16 rarest.
    many = {"w%02d" % n: 100 - n for n in range(18)}
    pairs = pruning.pruned(" | ".join("'%s'" % w for w in sorted(many)), many, 35, together=2)
    assert pairs.count("&") == 16 * 15 // 2 and "'w00'" not in pairs and "'w01'" not in pairs


class TestOnPostgreSQL:
    """The bound against PostgreSQL's own TS_RANK."""

    def rank(self, cursor, text, query):
        cursor.execute("SELECT " + RANK, (text, query))
        return cursor.fetchone()[0]

    def test_bound_holds_with_repeats(self, pg_connection):
        """However often a word occurs, a source with fewer than words_needed()
        of the query's words ranks at or below the minimum."""
        cursor = pg_connection.cursor()
        words = ["file", "name", "need", "origin", "machin", "xml"]
        query = " | ".join("'%s'" % word for word in words)
        needed = pruning.words_needed(len(words), 35)
        for found in range(1, needed):
            for chosen in itertools.combinations(words, found):
                text = " ".join(" ".join([word] * 40) for word in chosen)
                assert self.rank(cursor, text, query) <= 35
        # And words_needed() of them can pass.
        assert self.rank(cursor, "file name machin", query) > 35

    def test_same_sources(self, pg_connection):
        """Random sources: those ranking above the minimum all match the pruned
        query."""
        cursor = pg_connection.cursor()
        vocabulary = ["file", "name", "need", "origin", "machin", "xml", "open", "save",
                      "close", "edit", "view", "help", "tool", "data", "user", "list"]
        generator = random.Random(3476)
        counts = {word: generator.randint(0, 1000) for word in vocabulary}
        for together, _ in itertools.product((1, 2, 3), range(150)):
            query_words = generator.sample(vocabulary, generator.randint(2, 12))
            query = " | ".join("'%s'" % word for word in query_words)
            pruned = pruning.pruned(query, counts, 35, together)
            if pruned is None:
                continue
            for _ in range(5):
                text = " ".join(generator.choice(vocabulary)
                                for _ in range(generator.randint(1, 20)))
                cursor.execute("SELECT %s AND TO_TSVECTOR('english', %%s) @@ %%s::tsquery"
                               % (RANK + " > 35"), (text, query, text, query))
                if cursor.fetchone()[0]:
                    cursor.execute("SELECT TO_TSVECTOR('english', %s) @@ %s::tsquery",
                                   (text, pruned))
                    assert cursor.fetchone()[0], (query, pruned, text)
