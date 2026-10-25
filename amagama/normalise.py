#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Functionality for text normalisation."""


def indexing_version(s, checker=None):
    """Transform the given string to something more suitable for indexing."""
    s = strip_accelerator(s, checker)
    s = fix_ellipses(s)
    return s


def strip_accelerator(s, checker):
    if not (checker and checker.config.accelmarkers):
        return s

    accel = checker.config.accelmarkers[0]
    if s.count(accel) != 1:
        # TODO: in mozilla, XML entities could mess this up.
        return s

    accel_pos = s.find(accel)
    accel_char = s[accel_pos+1:accel_pos+2]
    valid_accel = checker.config.sourcelang.validaccel
    if (valid_accel and accel_char in valid_accel) or accel_char.isalnum():
        return s.replace(accel, u"", 1)

    return s


def fix_ellipses(s):
    """Fix inconsistent ellipses."""
    # Note that the space before the ellipsis will cause postgres to index it
    # as a lexeme.
    if s.endswith(u'...'):
        return s[:-3] + u' …'
    else:
        return s.replace(u'…', u' …')
