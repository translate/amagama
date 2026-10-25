#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Undo UTF-8 text that was decoded as Latin-1 or Windows-1252."""

# A UTF-8 lead byte followed by a continuation byte, as Latin-1 or
# Windows-1252 shows them. Used to find candidates in the database.
CANDIDATE_REGEX = (
    '[Â-ô][\u0080-¿'
    'ŒœŠšŸŽžƒˆ˜'
    '–—‘-‚“-„†-•…‰'
    '‹›€™]'
)

_MAX_ROUNDS = 3


def _to_bytes(text):
    """The bytes text came from if it was decoded as Latin-1/Windows-1252."""
    out = bytearray()
    for char in text:
        if ord(char) < 256:
            out.append(ord(char))
        else:
            try:
                out += char.encode('cp1252')
            except UnicodeEncodeError:
                return None
    return bytes(out)


def unmangle(text):
    """Return text with mojibake undone, or None if it doesn't convert.

    Text converts only if all of it reads as UTF-8 once turned back into
    bytes, so genuine Latin-1 text such as "âge" is left alone. Text
    mangled more than once is converted until it stops changing.
    """
    result = text
    for _ in range(_MAX_ROUNDS):
        data = _to_bytes(result)
        if data is None:
            break
        try:
            decoded = data.decode('utf-8')
        except UnicodeDecodeError:
            break
        if decoded == result:
            break
        result = decoded
    return result if result != text else None
