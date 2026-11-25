#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Lookups matching fewer of their words (#3476) find what they did."""

import random

import pytest
from click.testing import CliRunner

from amagama import pruning, tmdb
from amagama.application import amagama_server_factory
from amagama.cli import manage

WORDS = ["file", "name", "open", "save", "close", "folder", "document", "printer", "network",
         "server", "password", "account", "window", "settings", "language", "keyboard",
         "selected", "required", "original", "machine"]


@pytest.fixture
def deployed(amagama_config, tmp_path, monkeypatch):
    """A deployed TM of sentences over WORDS, every word counted."""
    generator = random.Random(3476)
    sentences = sorted({" ".join(generator.choice(WORDS) for _ in range(generator.randint(3, 14)))
                        .capitalize() for _ in range(400)})
    po = tmp_path / "af.po"
    po.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n' +
                  "".join('msgid "%s"\nmsgstr "%d %s"\n\n' % (s, n, s.upper())
                          for n, s in enumerate(sentences)), encoding="utf-8")
    monkeypatch.setattr(tmdb, "WORD_COUNT_MIN", 1)
    runner = CliRunner()
    for args in (("initdb", "-s", "en"), ("build_tmdb", "-s", "en", "-t", "af", "-i", str(po)),
                 ("deploy_db",)):
        result = runner.invoke(manage, args, input="y\n")
        assert result.exit_code == 0, result.output
    return sentences, generator


def lookups(texts):
    app = amagama_server_factory()
    found = []
    with app.test_client() as client:
        for text in texts:
            response = client.get("/api/v1/en/af/unit/%s" % text)
            found.append([(c["source"], c["target"], c["quality"]) for c in response.get_json()])
    return found


def test_same_suggestions(deployed, pg_connection, monkeypatch):
    sentences, generator = deployed
    queries = generator.sample(sentences, 30) + [
        " ".join(generator.sample(WORDS, generator.randint(5, 14))) for _ in range(10)] + [
        # Enough words for more than pruning.MAX_PAIRS pairs.
        " ".join(generator.sample(WORDS, generator.randint(16, 20))) for _ in range(5)]
    filters = []
    choose = pruning.lookup_filter

    def counted(*args):
        filters.append(choose(*args))
        return filters[-1]
    monkeypatch.setattr(pruning, "lookup_filter", counted)
    pruned = lookups(queries)
    # Pairs and single words both ran.
    assert any(f and "&" in f for f in filters) and any(f and "&" not in f for f in filters)
    assert any(pruned)
    with pg_connection.cursor() as cursor:
        cursor.execute("DROP TABLE en.word_counts")
    pg_connection.commit()
    filters.clear()
    assert lookups(queries) == pruned
    assert filters == []


def test_word_counts(deployed, pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT count(*), min(sources) FROM en.word_counts")
        words, least = cursor.fetchone()
    # Every word but those TO_TSVECTOR drops (none here), stemmed.
    assert words == len(WORDS) and least >= 1


def test_banded_indexes(deployed, pg_connection):
    """The two longer bands' indexes hold the length; the shortest's doesn't."""
    with pg_connection.cursor() as cursor:
        cursor.execute("""SELECT c.relname, pg_get_indexdef(c.oid) FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            WHERE i.indrelid = 'en.sources'::regclass AND c.relname LIKE '%%text_idx'
                OR i.indrelid = 'en.sources'::regclass AND c.relname = 'sources_long_idx'
            ORDER BY 1""")
        found = dict(cursor.fetchall())
        cursor.execute("""SELECT n.nspname FROM pg_extension e
            JOIN pg_namespace n ON n.oid = e.extnamespace WHERE e.extname = 'btree_gin'""")
        installed = cursor.fetchone()
    assert "gin (vector)" in found["sources_up_to_28_text_idx"]
    if installed is None:
        # A PostgreSQL without the extension, or a user who can't add it:
        # test_without_btree_gin covers that.
        pytest.skip("btree_gin couldn't be installed")
    assert installed[0] == "public"
    assert "gin (length, vector)" in found["sources_long_idx"]
    assert "gin (length, vector)" in found["sources_up_to_93_text_idx"]
    assert "gin (vector)" in found["sources_up_to_28_text_idx"]


def test_without_btree_gin(amagama_config, tmp_path, monkeypatch, pg_connection):
    """Where the extension can't be had, the indexes hold the vector alone."""
    monkeypatch.setattr(tmdb.TMDB, "_btree_gin", staticmethod(lambda cursor: False))
    runner = CliRunner()
    for args in (("initdb", "-s", "en"), ("deploy_db",)):
        result = runner.invoke(manage, args, input="y\n")
        assert result.exit_code == 0, result.output
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT pg_get_indexdef('en.sources_long_idx'::regclass)")
        assert "gin (vector)" in cursor.fetchone()[0]
