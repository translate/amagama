#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Language names for the lookup page's lists."""

import pytest

from amagama import languagenames


@pytest.mark.parametrize('code, lang, expected', [
    ('af', 'en', {'tag': 'af', 'name': 'Afrikaans'}),
    ('nso', 'en', {'tag': 'nso', 'name': 'Pedi'}),
    ('pt_br', 'en', {'tag': 'pt-BR', 'name': 'Portuguese (Brazil)'}),
    ('pt_br', 'af', {'tag': 'pt-BR', 'name': 'Portugees (Brasilië)'}),
    ('es_419', 'en', {'tag': 'es-419', 'name': 'Spanish (419)'}),
    ('en_zz', 'en', {'tag': 'en-ZZ', 'name': 'English (ZZ)'}),
    ('sr_latin', 'en', {'tag': 'sr-Latn', 'name': 'Serbian (Latin)'}),
    ('cdo_hant', 'en', {'tag': 'cdo-Hant', 'name': 'Min Dong Chinese (Han (Traditional variant))'}),
    ('ca_valencia', 'en', {'tag': 'ca-valencia', 'name': 'Catalan (valencia)'}),
    ('roa_tara', 'en', {'tag': 'roa-tara'}),
])
def test_describe(code, lang, expected):
    assert languagenames.describe(code, lang) == expected


def test_pattern():
    assert languagenames.describe('pt_br', 'en', '{detail}: {language}')['name'] == \
        'Brazil: Portuguese'


def test_describe_all():
    assert languagenames.describe_all(('af', 'zz'), 'en') == {
        'af': {'tag': 'af', 'name': 'Afrikaans'}, 'zz': {'tag': 'zz'}}
