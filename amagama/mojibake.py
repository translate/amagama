#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Undo UTF-8 text that was decoded as Latin-1, Windows-1252 or ISO-8859-15."""

# A UTF-8 lead byte followed by a continuation byte, as Latin-1 or
# Windows-1252 shows them. Used to find candidates in the database.
CANDIDATE_REGEX = (
    '[Â-ô][\u0080-¿'
    'ŒœŠšŸŽžƒˆ˜'
    '–—‘-‚“-„†-•…‰'
    '‹›€™]'
)

_MAX_ROUNDS = 3


# Where the text was decoded: Windows-1252 (Latin-1 with its extras), or
# ISO-8859-15, which puts € Š š Ž ž Œ œ Ÿ where Latin-1 has ¤ ¦ ¨ ´ ¸ ¼ ½ ¾.
_CODECS = ('cp1252', 'iso8859_15')


def _to_bytes(text, codec='cp1252'):
    """The bytes text came from if it was decoded as Latin-1 or codec."""
    out = bytearray()
    for char in text:
        if codec == 'cp1252' and ord(char) < 256:
            out.append(ord(char))
        else:
            try:
                out += char.encode(codec)
            except UnicodeEncodeError:
                return None
    return bytes(out)


def _capitals_inside_words(text):
    """How many capitals sit between a letter and a small letter: JÀrgmine."""
    return sum(1 for before, char, after in zip(text, text[1:], text[2:])
               if before.isalpha() and char.isupper() and after.islower())


def _decoded(text):
    """text read back as UTF-8 once, or None if no reading is clearly it.

    Readings through the two codecs differ only in the characters they
    don't share; the one without capitals inside words wins, and a tie
    leaves the text alone.
    """
    readings = set()
    for codec in _CODECS:
        data = _to_bytes(text, codec)
        if data is None:
            continue
        try:
            readings.add(data.decode('utf-8'))
        except UnicodeDecodeError:
            pass
    ranked = sorted(readings, key=_capitals_inside_words)
    if len(ranked) > 1 and (_capitals_inside_words(ranked[0])
                            == _capitals_inside_words(ranked[1])):
        return None
    return ranked[0] if ranked else None


def unmangle(text):
    """Return text with mojibake undone, or None if it doesn't convert.

    Text converts only if all of it reads as UTF-8 once turned back into
    bytes, so genuine Latin-1 text such as "âge" is left alone. Text
    mangled more than once is converted until it stops changing. Text
    whose letters were lost before it was mangled (it decodes to the
    replacement character) is left alone too.
    """
    result = text
    for _ in range(_MAX_ROUNDS):
        decoded = _decoded(result)
        if decoded is None or decoded == result:
            break
        result = decoded
    if result == text or ('\ufffd' in result and '\ufffd' not in text):
        return None
    return result
