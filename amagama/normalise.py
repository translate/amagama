# -*- coding: utf-8 -*-
#
# Copyright 2012 F Wolff
#
# This file is part of amagama.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, see <http://www.gnu.org/licenses/>.

"""Functionality for text normalisation."""


def indexing_version(s, checker=None):
    """Trasform the given string to something more suitable for indexing."""
    s = strip_accelerator(s, checker)
    return s


def strip_accelerator(s, checker):
    if not (checker and checker.config.accelmarkers):
        return s

    accel = checker.config.accelmarkers[0]
    if s.count(accel) != 1:
        # TODO: in mozilla, XML entities could mess this up
        return s

    accel_pos = s.find(accel)
    accel_char = s[accel_pos+1:accel_pos+2]
    valid_accel = checker.config.sourcelang.validaccel
    if (valid_accel and accel_char in valid_accel) or accel_char.isalnum():
        return s.replace(accel, u"", 1)

    return s
