#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest

from amagama.normalise import indexing_version
from amagama.tmdb import project_checker


@pytest.fixture
def gnome():
    return project_checker('gnome', 'en')


class TestIndexingVersion(object):

    @pytest.mark.parametrize('text, indexed', [
        ('_Open', 'Open'),
        # More than one accelerator marker: not an accelerator.
        ('_Save _As', '_Save _As'),
        # The marker isn't before a valid accelerator key.
        ('Open_.', 'Open_.'),
        ('Plain', 'Plain'),
    ])
    def test_accelerators(self, gnome, text, indexed):
        assert indexing_version(text, gnome) == indexed

    def test_no_checker_keeps_accelerators(self):
        assert indexing_version('_Open') == '_Open'

    @pytest.mark.parametrize('text', ['Wait...', 'Wait…'])
    def test_ellipses(self, text):
        assert indexing_version(text) == 'Wait …'
