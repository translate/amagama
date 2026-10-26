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


@pytest.mark.parametrize('text', ['Järgmine', 'Balsa: sõnumi trükkimise eelvaade', 'jäta'])
def test_unmangle_latin9(text):
    # ISO-8859-15 has € and Œ where Windows-1252 puts them at 80 and 8C.
    assert unmangle(mangle(text, 'iso8859_15')) == text


def test_unmangle_latin9_as_found():
    assert unmangle('JÃ€rgmine') == 'Järgmine'
    assert unmangle('trÃŒkkimise') == 'trükkimise'


def test_ambiguous_codec_is_left_alone():
    # "À propos" through Windows-1252 or "ä propos" through ISO-8859-15.
    assert unmangle('Ã€ propos') is None


def test_lost_letters_are_left_alone():
    # The replacement character, mangled: the letter was gone before.
    assert unmangle('mudanï¿½as') is None


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
