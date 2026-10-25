#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Choosing suggestions by source with ?exclude=, ?only= and ?weight= (#3406)."""

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.source_options import SourceOptions, licence_allowed


class TestSourceOptions(object):

    def test_from_args(self):
        options = SourceOptions.from_args({'exclude': 'hosted-*, ', 'only': 'gnome-*,kde',
                                           'weight': 'gnome-*:1.5,hosted-*:0'})
        assert options.exclude == ['hosted-*']
        assert options.only == ['gnome-*', 'kde']
        assert options.weights == [('gnome-*', 1.5), ('hosted-*', 0.0)]

    def test_none(self):
        assert not SourceOptions.from_args({})

    def test_licence_args(self):
        options = SourceOptions.from_args({'licence': 'GPL-*, MIT'})
        assert options and options.licences == ('GPL-*', 'MIT')

    @pytest.mark.parametrize('expression, allowed, result', [
        ('GPL-2.0-or-later', ('GPL-*',), True),
        # A glob is matched whole: GPL-* isn't LGPL.
        ('LGPL-2.1-or-later', ('GPL-*',), False),
        # Either side of OR will do; every side of AND is needed.
        ('LGPL-2.1-or-later OR GPL-3.0-only', ('GPL-*',), True),
        ('MIT AND (Apache-2.0 OR BSD-3-Clause)', ('MIT', 'BSD-*'), True),
        ('MIT AND (Apache-2.0 OR BSD-3-Clause)', ('BSD-*',), False),
        # An exception only adds permissions.
        ('GPL-2.0-or-later WITH Classpath-exception-2.0', ('GPL-2.0-or-later',), True),
        # SPDX identifiers ignore case.
        ('GPL-2.0-or-later', ('gpl-*',), True),
        # ! leaves licences out; on its own, it allows every other licence.
        ('AGPL-3.0-only', ('*', '!AGPL-*'), False),
        ('AGPL-3.0-only', ('!AGPL-*',), False),
        ('MIT', ('!AGPL-*',), True),
        ('MIT OR AGPL-3.0-only', ('!AGPL-*',), True),
        ('MIT AND AGPL-3.0-only', ('!AGPL-*',), False),
        ('GPL-3.0-only', ('GPL-*', '!GPL-3.0-*'), False),
        ('GPL-2.0-only', ('GPL-*', '!GPL-3.0-*'), True),
        (None, ('*',), False),
        ('(MIT', ('*',), False),
    ])
    def test_licence_allowed(self, expression, allowed, result):
        assert licence_allowed(expression, allowed) is result

    @pytest.mark.parametrize('names, kept', [
        (['gnome-51', 'kde'], ['gnome-51']),
        (['kde'], None),
        # Unknown licences and unrecorded sources don't count as allowed.
        (['mixed'], None),
        ([], None),
    ])
    def test_kept_by_licence(self, names, kept):
        licences = {'gnome-51': 'GPL-2.0-or-later', 'kde': 'LGPL-2.1-or-later', 'mixed': None}
        assert SourceOptions(licences=['GPL-*']).kept(names, licences) == kept

    @pytest.mark.parametrize('weight', ['gnome', ':2', 'gnome:', 'gnome:x', 'gnome:-1',
                                        'gnome:11', 'gnome:nan', 'gnome:inf'])
    def test_bad_weight(self, weight):
        with pytest.raises(ValueError):
            SourceOptions.from_args({'weight': weight})

    @pytest.mark.parametrize('options, names, kept', [
        # A suggestion stays while any of its sources does.
        ({'exclude': ['hosted-*']}, ['gnome-51', 'hosted-x'], ['gnome-51']),
        ({'exclude': ['hosted-*']}, ['hosted-x'], None),
        # Unrecorded sources are left out only by only.
        ({'exclude': ['hosted-*']}, [], []),
        ({'only': ['gnome-*']}, [], None),
        ({'only': ['gnome-*']}, ['gnome-51', 'kde'], ['gnome-51']),
        ({'only': ['gnome-*'], 'exclude': ['gnome-50']}, ['gnome-50'], None),
    ])
    def test_kept(self, options, names, kept):
        assert SourceOptions(**options).kept(names) == kept

    def test_default_weights(self):
        defaults = {'hosted-x': 0.5, 'kde': 3}
        assert SourceOptions().weight(['hosted-x'], defaults) == 0.5
        assert SourceOptions().weight(['hosted-x', 'gnome-51'], defaults) == 1.0
        # A client's weight replaces the default.
        assert SourceOptions(weights=[('kde', 0.1)]).weight(['kde'], defaults) == 0.1

    def test_weight(self):
        options = SourceOptions(weights=[('hosted-*', 0.5), ('*', 2)])
        assert options.weight([]) == 1.0
        assert options.weight(['hosted-x']) == 0.5
        # The first matching glob counts, and the best source.
        assert options.weight(['hosted-x', 'kde']) == 2
        assert SourceOptions(weights=[('hosted-*', 0.5)]).weight(['hosted-x', 'kde']) == 1.0


def po(*units):
    return 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n' + ''.join(
        '\nmsgid "%s"\nmsgstr "%s"\n' % unit for unit in units)


LICENCES = {'gnome-51': 'GPL-2.0-or-later', 'hosted-x': 'MIT',
            'kde': 'LGPL-2.1-or-later OR GPL-3.0-only'}


@pytest.fixture
def client(amagama_config, tmp_path):
    runner = CliRunner()
    assert runner.invoke(manage, ('initdb', '-s', 'en')).exit_code == 0
    for block, units in (
            ('gnome-51', [('Open file', 'Maak lêer oop')]),
            ('hosted-x', [('Open file', 'Maak lêer oop'), ('Open file', 'Open lêer')]),
            ('kde', [('Open files', 'Maak lêers oop')])):
        for n, unit in enumerate(units):
            path = tmp_path / ('%s-%d.po' % (block, n))
            path.write_text(po(unit), encoding='utf-8')
            result = runner.invoke(manage, ('build_tmdb', '-s', 'en', '-t', 'af', '-i', str(path),
                                            '--block', block, '--licence', LICENCES[block]))
            assert result.exit_code == 0, result.output
    with amagama_server_factory().test_client() as client:
        yield client


def lookup(client, query=''):
    response = client.get('/api/v1/en/af/unit/Open file?' + query)
    assert response.status_code == 200
    return [(c['target'], c['sources']) for c in response.get_json()]


class TestLookup(object):

    def test_default(self, client):
        assert sorted(lookup(client)) == [
            ('Maak lêer oop', ['gnome-51', 'hosted-x']), ('Maak lêers oop', ['kde']),
            ('Open lêer', ['hosted-x'])]

    def test_exclude(self, client):
        assert sorted(lookup(client, 'exclude=hosted-*')) == [
            ('Maak lêer oop', ['gnome-51']), ('Maak lêers oop', ['kde'])]

    def test_only(self, client):
        assert lookup(client, 'only=kde') == [('Maak lêers oop', ['kde'])]

    def test_weight_reorders(self, client):
        assert lookup(client, 'weight=kde:2')[0] == ('Maak lêers oop', ['kde'])
        response = client.get('/api/v1/en/af/unit/Open file?weight=kde:2')
        # Quality is the similarity, not weighted.
        assert response.get_json()[0]['quality'] < 100

    def test_max_candidates_after_choosing(self, client):
        assert lookup(client, 'only=kde&max_candidates=1') == [('Maak lêers oop', ['kde'])]

    def test_bad_weight(self, client):
        assert client.get('/api/v1/en/af/unit/Open file?weight=kde').status_code == 400

    def test_licence(self, client):
        assert sorted(lookup(client, 'licence=GPL-*')) == [
            ('Maak lêer oop', ['gnome-51']), ('Maak lêers oop', ['kde'])]
        assert lookup(client, 'licence=MIT') == [
            ('Maak lêer oop', ['hosted-x']), ('Open lêer', ['hosted-x'])]
        assert lookup(client, 'licence=Apache-2.0') == []
        assert sorted(lookup(client, 'licence=!MIT')) == [
            ('Maak lêer oop', ['gnome-51']), ('Maak lêers oop', ['kde'])]


def record_weights(weights):
    app = amagama_server_factory()
    with app.app_context():
        app.tmdb.record_blocks('en', [(name, name, None, weight) for name, weight in weights])


class TestDefaultWeights(object):

    def test_reorder_lookups(self, client):
        record_weights([('kde', 2), ('gnome-51', 1)])
        with amagama_server_factory().test_client() as fresh:
            assert lookup(fresh)[0] == ('Maak lêers oop', ['kde'])
            # A client's weight replaces the default.
            assert lookup(fresh, 'weight=kde:1')[0][0] != 'Maak lêers oop'

    def test_listed_in_sources(self, client):
        record_weights([('kde', 2)])
        with amagama_server_factory().test_client() as fresh:
            weights = {s['name']: s['weight']
                       for s in fresh.get('/api/v1/sources/').get_json()['sources']}
        assert weights == {'gnome-51': 1.0, 'hosted-x': 1.0, 'kde': 2.0}

    def test_blocks_table_without_weights(self, client, pg_connection):
        record_weights([('kde', 2)])
        with pg_connection.cursor() as cursor:
            cursor.execute("ALTER TABLE en.blocks DROP COLUMN weight")
        pg_connection.commit()
        with amagama_server_factory().test_client() as fresh:
            assert len(lookup(fresh)) == 3
            assert {s['weight'] for s in fresh.get('/api/v1/sources/').get_json()['sources']} \
                == {1.0}
