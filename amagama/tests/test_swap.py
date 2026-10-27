#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage


PO_FILE = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open file"
msgstr "%s"
'''


@pytest.fixture
def run(amagama_config):
    runner = CliRunner()

    def run(*args, input=None):
        return runner.invoke(manage, args, input=input)
    return run


@pytest.fixture
def load(run, tmp_path):
    """Import "Open file" with the given translation, live or into the build."""
    def load(target, *options):
        path = tmp_path / 'af.po'
        path.write_text(PO_FILE % target, encoding='utf-8')
        result = run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(path), *options)
        assert result.exit_code == 0, result.output
    return load


def lookup(client):
    response = client.get('/api/v1/en/af/unit/Open file')
    return [c['target'] for c in response.get_json()]


@pytest.fixture
def live_and_build(run, load):
    run('initdb', '-s', 'en')
    load('Maak lêer oop')
    run('deploy_db', input='y\n')
    run('initdb', '-s', 'en', '--build')
    load('Open lêer', '--build')
    result = run('deploy_db', '--build', input='y\n')
    assert result.exit_code == 0, result.output


class TestBuildSchemas(object):

    def test_build_schema_is_not_served(self, run):
        run('initdb', '-s', 'en', '--build')
        assert run('tmdb_stats').exit_code == 1
        result = run('tmdb_stats', '--build')
        assert result.exit_code == 0, result.output
        assert "Source language: en" in result.output
        app = amagama_server_factory()
        with app.test_client() as client:
            languages = client.get('/api/v1/languages/').get_json()
        assert languages['sourceLanguages'] == []

    def test_build_leaves_live_data(self, live_and_build):
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Maak lêer oop']


class TestSwap(object):

    def test_swap(self, run, live_and_build):
        result = run('swap_db', '-s', 'en')
        assert result.exit_code == 0, result.output
        assert "the replaced copy is _old_en" in result.output
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Open lêer']
        assert run('tmdb_stats', '--build').exit_code == 1

    def test_rollback(self, run, live_and_build):
        run('swap_db', '-s', 'en')
        result = run('swap_db', '-s', 'en', '--rollback')
        assert result.exit_code == 0, result.output
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Maak lêer oop']
        assert run('tmdb_stats', '--build').exit_code == 0

    def test_first_swap_has_no_live_schema(self, run, load):
        run('initdb', '-s', 'en', '--build')
        load('Open lêer', '--build')
        result = run('swap_db', '-s', 'en')
        assert result.exit_code == 0, result.output
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Open lêer']

    def test_open_connection_sees_swap(self, run, live_and_build):
        # A connection that already prepared the lookup statement keeps it.
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Maak lêer oop']
            connection = app.tmdb.connection
            run('swap_db', '-s', 'en')
            assert lookup(client) == ['Open lêer']
            assert app.tmdb.connection is connection

    def test_needs_build_schema(self, run):
        run('initdb', '-s', 'en')
        result = run('swap_db', '-s', 'en')
        assert result.exit_code == 1
        assert "Schema _build_en doesn't exist." in result.output

    def test_old_schema_in_the_way(self, run, live_and_build, load):
        run('swap_db', '-s', 'en')
        run('initdb', '-s', 'en', '--build')
        load('Lêer oopmaak', '--build')
        result = run('swap_db', '-s', 'en')
        assert result.exit_code == 1
        assert "Schema _old_en already exists." in result.output

    def test_rollback_needs_old_schema(self, run, live_and_build):
        result = run('swap_db', '-s', 'en', '--rollback')
        assert result.exit_code == 1
        assert "Schema _old_en doesn't exist." in result.output

    def test_drop_old(self, run, live_and_build):
        run('swap_db', '-s', 'en')
        result = run('dropdb', '-s', 'en', '--old', input='y\n')
        assert result.exit_code == 0, result.output
        assert run('swap_db', '-s', 'en', '--rollback').exit_code == 1
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Open lêer']

    def test_drop_old_without_asking(self, run, live_and_build):
        run('swap_db', '-s', 'en')
        result = run('dropdb', '-s', 'en', '--old', '--yes')
        assert result.exit_code == 0, result.output
        assert "Successfully dropped the database for 'en'." in result.output
        assert run('swap_db', '-s', 'en', '--rollback').exit_code == 1

    def test_drop_old_when_there_is_none(self, run, live_and_build):
        # Nothing was swapped in this week: nothing to drop is no failure.
        result = run('dropdb', '-s', 'en', '--old', '--yes')
        assert result.exit_code == 0, result.output
        assert "No replaced copy of 'en' to drop." in result.output
        app = amagama_server_factory()
        with app.test_client() as client:
            assert lookup(client) == ['Maak lêer oop']

    def test_drop_build_and_old_together(self, run):
        result = run('dropdb', '-s', 'en', '--build', '--old')
        assert result.exit_code == 2
        assert "Use only one of --build and --old." in result.output
