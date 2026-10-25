#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Each target remembers the imports that supplied it (#2348)."""

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tmdb import BUILD_PREFIX
from amagama.updater.pipeline import import_files


def po(units):
    return 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n' + "".join(
        '\nmsgid "%s"\nmsgstr "%s"\n' % unit for unit in units)


def run(*args, input=None):
    result = CliRunner().invoke(manage, args, input=input)
    assert result.exit_code == 0, result.output
    return result


@pytest.fixture
def load(amagama_config, tmp_path):
    """Import units for lang as block, with any build_tmdb options."""
    def load(block, lang, units, *options):
        path = tmp_path / ('%s-%s.po' % (block, lang))
        path.write_text(po(units), encoding='utf-8')
        run('build_tmdb', '-s', 'en', '-t', lang, '-i', str(path), '--block', block, *options)
    return load


def query(pg_connection, sql, params=None):
    with pg_connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
    pg_connection.commit()
    return rows


def lookup(client, source, lang='af'):
    response = client.get('/api/v1/en/%s/unit/%s' % (lang, source))
    assert response.status_code == 200
    return [(c['target'], c['sources']) for c in response.get_json()]


def client():
    app = amagama_server_factory()
    app.testing = True
    return app.test_client()


class TestImports(object):

    def test_one_target_from_two_blocks(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        load('debian', 'af', [('Open file', 'Maak lêer oop'), ('Save file', 'Stoor lêer')])
        assert query(pg_connection, "SELECT COUNT(*) FROM en.targets") == [(2,)]
        assert query(pg_connection, "SELECT COUNT(*) FROM en.target_imports") == [(3,)]
        with client() as c:
            assert lookup(c, 'Open file') == [('Maak lêer oop', ['debian', 'gnome-51'])]
            assert lookup(c, 'Save file') == [('Stoor lêer', ['debian'])]

    def test_reimport_adds_no_link(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        # Two imports, so two links, but one block.
        assert query(pg_connection, "SELECT COUNT(*) FROM en.target_imports") == [(2,)]
        with client() as c:
            assert lookup(c, 'Open file') == [('Maak lêer oop', ['gnome-51'])]

    def test_survives_deploy(self, load):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        load('kde', 'zu', [('Open file', 'Vula ifayela')])
        run('deploy_db', input='y\n')
        with client() as c:
            assert lookup(c, 'Open file') == [('Maak lêer oop', ['gnome-51'])]
            assert lookup(c, 'Open file', 'zu') == [('Vula ifayela', ['kde'])]

    def test_import_without_block(self, amagama_config, tmp_path):
        run('initdb', '-s', 'en')
        path = tmp_path / 'af.po'
        path.write_text(po([('Open file', 'Maak lêer oop')]), encoding='utf-8')
        run('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(path))
        with client() as c:
            assert lookup(c, 'Open file') == [('Maak lêer oop', [])]

    def test_counts_recorded_after_units(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop'), ('Save file', '')])
        [(units, skipped, checksum)] = query(
            pg_connection, "SELECT units, skipped, checksum FROM en.imports")
        assert (units, skipped) == (1, 1)
        assert len(checksum) == 64

    def test_parallel_workers_link_shared_targets(self, amagama_config, tmp_path,
                                                  pg_connection):
        # Every file has the same targets, so the workers keep conflicting.
        units = [("String %d" % n, "String %d af" % n) for n in range(500)]
        tasks = []
        for n in range(8):
            path = tmp_path / ('%d.po' % n)
            path.write_text(po(units), encoding='utf-8')
            tasks.append((str(path), 'en', 'af', 'block-%d' % n, None))
        run('initdb', '-s', 'en', '--build')
        app = amagama_server_factory()
        with app.app_context():
            app.tmdb.use_schemas(BUILD_PREFIX)
            assert import_files(tasks, 4) == [(500, 0)] * 8
        assert query(pg_connection, "SELECT COUNT(*) FROM _build_en.targets") == [(500,)]
        assert query(pg_connection, """SELECT COUNT(DISTINCT tid), COUNT(*)
            FROM _build_en.target_imports""") == [(500, 4000)]


class TestSnapshot(object):

    def test_loaded_targets_link_to_the_snapshot(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        run('deploy_db', input='y\n')
        run('snapshot_db', '-s', 'en', '--name', 'legacy')
        run('initdb', '-s', 'en', '--build')
        run('load_snapshot', '-s', 'en', '--name', 'legacy', '--block', 'legacy-2019')
        load('debian', 'af', [('Open file', 'Maak lêer oop')], '--build')
        run('deploy_db', '--build', input='y\n')
        run('swap_db', '-s', 'en')
        with client() as c:
            assert lookup(c, 'Open file') == [('Maak lêer oop', ['debian', 'legacy-2019'])]


class TestOldSchema(object):
    """A schema made before target_imports existed still works."""

    @pytest.fixture
    def old(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        with pg_connection.cursor() as cursor:
            cursor.execute("DROP TABLE en.target_imports")
            cursor.execute("ALTER TABLE en.targets DROP COLUMN tid")
        pg_connection.commit()

    def test_lookup(self, old):
        with client() as c:
            assert lookup(c, 'Open file') == [('Maak lêer oop', [])]

    def test_import(self, old, load):
        load('debian', 'af', [('Save file', 'Stoor lêer')])
        with client() as c:
            assert lookup(c, 'Save file') == [('Stoor lêer', [])]

    def test_write_api(self, old, amagama_config, pg_connection):
        amagama_config(ENABLE_DATA_ALTERING_API=True)
        with client() as c:
            response = c.put('/api/v1/en/af/unit/Save file',
                             json={'source': 'Save file', 'target': 'Stoor lêer'})
            assert response.status_code == 200
            assert lookup(c, 'Save file') == [('Stoor lêer', [])]
        assert query(pg_connection, "SELECT block FROM en.imports") == [('gnome-51',)]


class TestWriteAPI(object):

    @pytest.fixture
    def deployed(self, load, amagama_config):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', 'Maak lêer oop')])
        run('deploy_db', input='y\n')
        amagama_config(ENABLE_DATA_ALTERING_API=True)

    @pytest.mark.parametrize('method', ['put', 'post'])
    def test_unit(self, deployed, pg_connection, method):
        with client() as c:
            response = getattr(c, method)('/api/v1/en/af/unit/Open file',
                                          json={'source': 'Open file', 'target': 'Maak lêer oop'})
            assert response.status_code == 200
            response = getattr(c, method)('/api/v1/en/af/unit/Save file',
                                          json={'source': 'Save file', 'target': 'Stoor lêer'})
            assert response.status_code == 200
            assert lookup(c, 'Open file') == [('Maak lêer oop', ['api', 'gnome-51'])]
            assert lookup(c, 'Save file') == [('Stoor lêer', ['api'])]
        # The deployed schema has no unique index, but gets no duplicate.
        assert query(pg_connection, "SELECT COUNT(*) FROM en.targets") == [(2,)]
        assert query(pg_connection, """SELECT block, filename, units, skipped
            FROM en.imports WHERE block = 'api'""") == [('api', 'unit', 1, 0)] * 2

    def test_store_list(self, deployed, pg_connection):
        with client() as c:
            response = c.post('/api/v1/en/af/store/upload.po', json=[
                {'source': 'Save file', 'target': 'Stoor lêer'},
                {'source': 'Close file', 'target': 'Maak lêer toe'}])
            assert response.status_code == 200
            assert lookup(c, 'Close file') == [('Maak lêer toe', ['api'])]
        assert query(pg_connection, """SELECT filename, units, skipped
            FROM en.imports WHERE block = 'api'""") == [('upload.po', 2, 0)]

    def test_store_upload(self, deployed, pg_connection):
        data = po([('Save file', 'Stoor lêer'), ('Close file', '')]).encode('utf-8')
        with client() as c:
            response = c.put('/api/v1/en/af/store/upload.po', data=data)
            assert response.status_code == 200
            assert lookup(c, 'Save file') == [('Stoor lêer', ['api'])]
        assert query(pg_connection, """SELECT filename, units, skipped
            FROM en.imports WHERE block = 'api'""") == [('upload.po', 1, 1)]


class TestRepairMojibake(object):

    def mangle(self, text):
        return text.encode('utf-8').decode('latin-1')

    def links(self, pg_connection):
        return query(pg_connection, """SELECT s.text, t.text, i.block
            FROM en.targets t JOIN en.sources s ON s.sid = t.sid
            JOIN en.target_imports l ON l.tid = t.tid JOIN en.imports i ON i.iid = l.iid
            ORDER BY 1, 2, 3""")

    def test_fixed_in_place(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', self.mangle('Maak lêer oop'))])
        run('repair_mojibake', '--apply')
        assert self.links(pg_connection) == [('Open file', 'Maak lêer oop', 'gnome-51')]

    def test_merged_into_the_fixed_target(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [('Open file', self.mangle('Maak lêer oop'))])
        load('debian', 'af', [('Open file', 'Maak lêer oop')])
        run('repair_mojibake', '--apply')
        assert self.links(pg_connection) == [
            ('Open file', 'Maak lêer oop', 'debian'),
            ('Open file', 'Maak lêer oop', 'gnome-51')]
        assert query(pg_connection, "SELECT COUNT(*) FROM en.target_imports") == [(2,)]

    def test_merged_source(self, load, pg_connection):
        run('initdb', '-s', 'en')
        load('gnome-51', 'af', [(self.mangle('Résumé'), 'Opsomming')])
        load('debian', 'af', [('Résumé', 'Opsomming')])
        run('repair_mojibake', '--apply')
        assert self.links(pg_connection) == [
            ('Résumé', 'Opsomming', 'debian'),
            ('Résumé', 'Opsomming', 'gnome-51')]
        assert query(pg_connection, "SELECT COUNT(*) FROM en.target_imports") == [(2,)]
