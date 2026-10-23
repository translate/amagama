import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage


PO_FILE = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "Open file"
msgstr "Maak lêer oop"
'''


@pytest.fixture
def run(amagama_config):
    """Run amagama-manage against the test database."""
    runner = CliRunner()

    def run(*args, input=None):
        return runner.invoke(manage, args, input=input)
    return run


@pytest.fixture
def po_dir(tmp_path):
    (tmp_path / 'af.po').write_text(PO_FILE, encoding='utf-8')
    return tmp_path


def lookup(source, tlang='af'):
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get('/api/v1/en/%s/unit/%s' % (tlang, source))
        return [(c['source'], c['target']) for c in response.get_json()]


class TestCLI(object):

    def test_initdb(self, run):
        result = run('initdb', '-s', 'en')
        assert result.exit_code == 0, result.output
        assert "Successfully initialized the database for 'en'." in result.output

    def test_needs_initdb(self, run):
        result = run('tmdb_stats')
        assert result.exit_code == 1
        assert "No source language is configured" in result.output

    def test_build_tmdb(self, run, po_dir):
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-t', 'af',
                     '-i', str(po_dir / 'af.po'))
        assert result.exit_code == 0, result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_language_from_file_name(self, run, po_dir):
        # Without -t, the target language comes from the file name.
        run('initdb', '-s', 'en')
        result = run('build_tmdb', '-s', 'en', '-i', str(po_dir))
        assert result.exit_code == 0, result.output
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_profile(self, run, po_dir, tmp_path):
        run('initdb', '-s', 'en')
        profile = tmp_path / 'build.profile'
        result = run('build_tmdb', '-s', 'en', '-t', 'af',
                     '-i', str(po_dir / 'af.po'), '-p', str(profile))
        assert result.exit_code == 0, result.output
        assert profile.read_text().startswith('events: Ticks\nsummary: ')
        assert lookup('Open file') == [('Open file', 'Maak lêer oop')]

    def test_build_tmdb_needs_input(self, run):
        result = run('build_tmdb', '-s', 'en', '-t', 'af')
        assert result.exit_code == 2
        assert "Missing option '--input'" in result.output

    def test_tmdb_stats(self, run):
        run('initdb', '-s', 'en')
        result = run('tmdb_stats')
        assert result.exit_code == 0, result.output
        assert "Source language: en" in result.output

    def test_deploy_db(self, run):
        run('initdb', '-s', 'en')
        result = run('deploy_db', input='y\n')
        assert result.exit_code == 0, result.output
        assert "Successfully altered the database for deployment." in result.output

    def test_dropdb(self, run):
        run('initdb', '-s', 'en')
        result = run('dropdb', '-s', 'en', input='y\n')
        assert result.exit_code == 0, result.output
        assert "Successfully dropped the database for 'en'." in result.output
        assert run('tmdb_stats').exit_code == 1

    def test_dropdb_declined(self, run):
        run('initdb', '-s', 'en')
        result = run('dropdb', '-s', 'en', input='n\n')
        assert result.exit_code == 0, result.output
        assert "Successfully dropped" not in result.output
        assert run('tmdb_stats').exit_code == 0

    def test_dropdb_needs_source_language(self, run):
        result = run('dropdb')
        assert result.exit_code == 2
        assert "Missing option '--source-language'" in result.output

    def test_benchmark_tmdb(self, run, po_dir):
        run('initdb', '-s', 'en')
        po = str(po_dir / 'af.po')
        run('build_tmdb', '-s', 'en', '-t', 'af', '-i', po)
        result = run('benchmark_tmdb', '-s', 'en', '-t', 'af', '-i', po)
        assert result.exit_code == 0, result.output
        assert "Percentiles:" in result.output
