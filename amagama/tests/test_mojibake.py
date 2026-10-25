#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import re

import pytest

from amagama.mojibake import CANDIDATE_REGEX, unmangle


def mangle(text, codec='latin-1'):
    """Decode text's UTF-8 as codec, the way a misconfigured import did.

    Bytes Windows-1252 leaves undefined become the matching control
    character, as most such decoders do.
    """
    out = []
    for byte in text.encode('utf-8'):
        try:
            out.append(bytes([byte]).decode(codec))
        except UnicodeDecodeError:
            out.append(chr(byte))
    return ''.join(out)


@pytest.mark.parametrize('text', [
    'Lêer',
    'Reëleindes',
    'Fichier créé',
    'Không thể mở tập tin',
    'கோப்பு',
])
def test_unmangle_latin1(text):
    assert unmangle(mangle(text)) == text


def test_unmangle_windows_1252():
    assert unmangle(mangle('Don’t “quote” — …', 'cp1252')) == 'Don’t “quote” — …'


def test_unmangle_twice_mangled():
    assert unmangle(mangle(mangle('Lêer'))) == 'Lêer'


@pytest.mark.parametrize('text', [
    'âge',
    'pâte à crêpes',
    'Lêer',
    'São Paulo',
    'Ã',
    'plain ASCII',
    '',
])
def test_genuine_text_is_left_alone(text):
    assert unmangle(text) is None


def test_mixed_text_is_left_alone():
    # A character outside Latin-1 and Windows-1252 means it wasn't mangled
    # as a whole.
    assert unmangle(mangle('Lêer') + ' ạ') is None


@pytest.mark.parametrize('text', ['LÃªer', 'crÃ©Ã©', 'Donâ€™t', 'à®•'])
def test_candidate_regex_finds_mojibake(text):
    assert re.search(CANDIDATE_REGEX, text)


@pytest.mark.parametrize('text', ['âge', 'Lêer', 'São Paulo', 'plain'])
def test_candidate_regex_skips_genuine_text(text):
    assert not re.search(CANDIDATE_REGEX, text)
