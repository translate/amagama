#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""ISO 639-3's retired codes, from SIL's table kept in the updater cache.

The retired codes come from ISO 639-3, iso639-3.sil.org, whose terms
(https://iso639-3.sil.org/code_tables/download_tables#termsofuse) let a
product download and use the code tables but not redistribute them. So the
table is downloaded into UPDATER_CACHE_DIR, never committed, and never
served.
"""

import collections
import datetime
import os
import re

from amagama.updater.fetchers import FetchError, http_text

URL = "https://iso639-3.sil.org/sites/iso639-3/files/downloads/iso-639-3_Retirements.tab"
FILENAME = "iso-639-3_Retirements.tab"
# The table changes a few times a year.
MAX_AGE_DAYS = 90
HEADER = "Id\tRef_Name\tRet_Reason\tChange_To\tRet_Remedy\tEffective"

Retirement = collections.namedtuple("Retirement", "name reason successors date")


def path(config, cache_dir=None):
    """ISO639_RETIREMENTS_FILE, else the table in cache_dir or UPDATER_CACHE_DIR."""
    return os.path.expanduser(config.get("ISO639_RETIREMENTS_FILE") or os.path.join(
        cache_dir or config.get("UPDATER_CACHE_DIR") or "~/.cache/amagama-updater", FILENAME))


def parse(text):
    """{retired code: Retirement} of the table's text. successors is the one
    Change_To, or for a split the codes its remedy names."""
    lines = text.lstrip("\ufeff").splitlines()
    if not lines or lines[0].rstrip("\r") != HEADER:
        raise ValueError("not ISO 639-3's retirements table")
    found = {}
    for line in lines[1:]:
        fields = line.rstrip("\r").split("\t")
        if len(fields) < 6 or not re.fullmatch(r"[a-z]{3}", fields[0]):
            continue
        code, name, reason, change_to, remedy, date = fields[:6]
        successors = (change_to,) if change_to else tuple(
            dict.fromkeys(re.findall(r"\[([a-z]{3})\]", remedy)))
        found[code] = Retirement(name, reason, successors, date)
    return found


def load(filename):
    """parse() of filename, or {} if there's none or it can't be read."""
    try:
        with open(filename, encoding="utf-8") as f:
            return parse(f.read())
    except (OSError, UnicodeError, ValueError):
        return {}


def refresh(filename, fetch=http_text, now=None):
    """Download the table to filename unless a copy younger than
    MAX_AGE_DAYS is there. Returns what to report, or None. A failed
    download keeps the copy there is."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    try:
        modified = datetime.datetime.fromtimestamp(os.path.getmtime(filename),
                                                   datetime.timezone.utc)
    except OSError:
        modified = None
    if modified and now - modified < datetime.timedelta(days=MAX_AGE_DAYS):
        return None
    try:
        text = fetch(URL)
        parse(text)
    except (FetchError, UnicodeError, ValueError) as e:
        if modified:
            return "Couldn't download ISO 639-3's retired codes (%s); using the copy of %s." % (
                e, modified.date())
        return "Couldn't download ISO 639-3's retired codes (%s); they aren't recognised." % e
    try:
        os.makedirs(os.path.dirname(os.path.abspath(filename)), exist_ok=True)
        with open(filename + ".new", "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(filename + ".new", filename)
    except OSError as e:
        return "Couldn't save ISO 639-3's retired codes in %s: %s" % (filename, e)
    return None
