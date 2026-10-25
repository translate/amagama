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
from amagama.source_options import SourceOptions


class TestSourceOptions(object):

    def test_from_args(self):
        options = SourceOptions.from_args({'exclude': 'hosted-*, ', 'only': 'gnome-*,kde',
                                           'weight': 'gnome-*:1.5,hosted-*:0'})
        assert options.exclude == ['hosted-*']
        assert options.only == ['gnome-*', 'kde']
        assert options.weights == [('gnome-*', 1.5), ('hosted-*', 0.0)]

    def test_none(self):
        assert not SourceOptions.from_args({})

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
                                            '--block', block))
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
