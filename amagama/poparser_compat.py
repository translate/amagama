#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Read PO files in a legacy charset with Translate Toolkit 3.19.0 and later,
and read C escapes as gettext does.

PoParseState.set_encoding() restarts the parse in the header's charset but
leaves _current_encoding, which read_line() decodes with, at UTF-8, so a
file in ISO-8859-1, KOI8-R, ... with any non-ASCII text fails with
UnicodeDecodeError. Drop this once Translate Toolkit fixes set_encoding().

pypo.unescape() knows only \\n \\t \\r \\" and \\\\, and drops the backslash of
any other escape: "\\033[1m" is read as "033[1m".
"""

import re

from translate.storage import poparser, pypo


def _patch(state_class):
    set_encoding = state_class.set_encoding

    def compat_set_encoding(self, encoding):
        self._current_encoding = encoding
        set_encoding(self, encoding)

    state_class.set_encoding = compat_set_encoding


# Older versions read these files correctly and have no _current_encoding.
_state_class = getattr(poparser, "PoParseState", None)
if _state_class is not None and "_current_encoding" in _state_class.__init__.__code__.co_names:
    _patch(_state_class)


_C_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "v": "\v"}
_escape = re.compile(r"\\(?:([0-7]{1,3})|x([0-9A-Fa-f]+)|.)")


def _unescape(match):
    octal, hexadecimal = match.groups()
    if octal or hexadecimal:
        code = int(octal, 8) if octal else int(hexadecimal, 16)
        # Past ASCII isn't a character in UTF-8, and PostgreSQL can't hold NUL:
        # left as they stand.
        return chr(code) if 0 < code < 0x80 else match.group(0)
    value = match.group(0)
    return pypo.po_unescape_map.get(value) or _C_ESCAPES.get(value[1], value[1])


if pypo.unescape("\\a") == "a":
    pypo.po_unescape_re = _escape
    pypo.unescapehandler = _unescape
    pypo._unquotefrompo.cache_clear()
