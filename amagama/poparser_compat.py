#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Read PO files in a legacy charset with Translate Toolkit 3.19.0 and later.

PoParseState.set_encoding() restarts the parse in the header's charset but
leaves _current_encoding, which read_line() decodes with, at UTF-8, so a
file in ISO-8859-1, KOI8-R, ... with any non-ASCII text fails with
UnicodeDecodeError. Drop this once Translate Toolkit fixes set_encoding().
"""

from translate.storage import poparser


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
