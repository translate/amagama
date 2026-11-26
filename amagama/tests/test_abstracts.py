#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Sources are indexed by their abstracts (#1967)."""

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tmdb import indexed


def run(*args, input=None):
    result = CliRunner().invoke(manage, args, input=input)
    assert result.exit_code == 0, result.output
    return result


def test_indexed():
    assert indexed("&File") == ("File", 4)
    assert indexed("Copy {0} to {1}")[0] == indexed("Copy %s to %s")[0]
    assert indexed("Fish &amp; chips")[0] == "Fish  chips"


@pytest.fixture
def client(amagama_config, tmp_path):
    path = tmp_path / "af.po"
    path.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                    'msgid "Copy {0} to {1}"\nmsgstr "Kopieer {0} na {1}"\n\n'
                    'msgid "&Open file"\nmsgstr "Maak lêer &oop"\n', encoding="utf-8")
    run("initdb", "-s", "en")
    run("build_tmdb", "-s", "en", "-t", "af", "-i", str(path))
    app = amagama_server_factory()
    with app.test_client() as client:
        yield client


def test_v1_as_before(client):
    found = client.get("/api/v1/en/af/unit/Copy {0} to {1}").get_json()
    assert [(s["source"], s["quality"]) for s in found] == [("Copy {0} to {1}", 100)]
    # The accelerator isn't indexed: "Open file" finds "&Open file".
    found = client.get("/api/v1/en/af/unit/Open file").get_json()
    assert [s["source"] for s in found] == ["&Open file"]


def test_snapshot_reindexed(amagama_config, tmp_path, pg_connection):
    """A snapshot's sources, indexed otherwise, are indexed by their abstracts
    when loaded."""
    path = tmp_path / "af.po"
    path.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                    'msgid "Copy {0} to {1}"\nmsgstr "Kopieer {0} na {1}"\n', encoding="utf-8")
    run("initdb", "-s", "en")
    run("build_tmdb", "-s", "en", "-t", "af", "-i", str(path))
    # As an older indexing left it: the raw text.
    with pg_connection.cursor() as cursor:
        cursor.execute("""UPDATE en.sources SET vector = TO_TSVECTOR('english', text),
            length = LENGTH(text)""")
    pg_connection.commit()
    run("snapshot_db", "-s", "en", "--name", "earlier")
    run("dropdb", "-s", "en", input="y\n")
    run("initdb", "-s", "en")
    app = amagama_server_factory()
    with app.app_context():
        app.tmdb.load_snapshot("en", "earlier")
    with pg_connection.cursor() as cursor:
        cursor.execute("""SELECT length, vector = TO_TSVECTOR('english', %s) FROM en.sources""",
                       (indexed("Copy {0} to {1}")[0],))
        assert cursor.fetchall() == [(len(indexed("Copy {0} to {1}")[0]), True)]
    pg_connection.commit()
