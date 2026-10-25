#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""SPDX licence expressions."""

import functools

import spdx_license_list
from license_expression import LicenseSymbol, Licensing


@functools.cache
def spdx_licensing():
    """Current (non-deprecated) SPDX License List identifiers."""
    symbols = [LicenseSymbol(key, aliases=(key.lower(),))
               for key, licence in spdx_license_list.LICENSES.items()
               if not licence.deprecated_id]
    symbols += [LicenseSymbol(key, aliases=(key.lower(),), is_exception=True)
                for key, exception in spdx_license_list.EXCEPTIONS.items()
                if not exception.deprecated_id]
    return Licensing(symbols)


def normalise_licence(value):
    """Return value as a normalised SPDX expression, or raise ValueError."""
    info = spdx_licensing().validate(value, strict=True)
    if info.errors:
        raise ValueError("not a current SPDX licence expression: %s" % "; ".join(info.errors))
    return info.normalized_expression
