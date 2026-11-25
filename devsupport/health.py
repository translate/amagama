#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Say what needs attention on the live server, from its statistics.

    python devsupport/health.py STATS [NOW]

STATS is /api/v1/stats/ saved to a file, missing or empty if it couldn't be
fetched; NOW an ISO time, the current time if not given. Prints a Markdown
list of the problems, nothing if there are none. Each line names a date
rather than an age, so it reads the same each day a problem lasts.
Only the standard library is used.
"""

import datetime
import json
import sys

# A weekly rebuild, with a day for a long one.
REBUILD_DAYS = 8
# A daily fetch, which skips a day while a rebuild holds the cache.
FETCH_DAYS = 3
# The daily count of lookups.
USE_DAYS = 2


def parse(stamp):
    """stamp as a time, in UTC if it names no time zone."""
    parsed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def problems(stats, now):
    """The problems stats shows at now, as Markdown lines."""
    if not isinstance(stats, dict):
        return ["The statistics couldn't be fetched: the site or its API isn't answering."]
    found = []
    for source, figures in sorted((stats.get("sourceLanguages") or {}).items()):
        # When the last rebuild swapped in; built, its newest import, for one
        # from before rebuilds were recorded. A rebuild copying every block
        # imports nothing, so built alone can be old.
        swapped = (figures.get("rebuild") or {}).get("swapped") or figures.get("built")
        if swapped and now - parse(swapped) > datetime.timedelta(days=REBUILD_DAYS):
            found.append("No rebuild of `%s` has swapped in since %s: the last weekly rebuild "
                         "failed, was skipped or is still running (`journalctl -u "
                         "amagama-update`)." % (source, swapped[:10]))
    fetch = stats.get("fetch")
    if fetch:
        if now - parse(fetch["finished"]) > datetime.timedelta(days=FETCH_DAYS):
            found.append("No fetch has finished since %s (`journalctl -u amagama-fetch`)."
                         % fetch["finished"][:10])
        failing = fetch.get("failing") or []
        if failing:
            found.append("%d blocks have failed to fetch 4 or more runs in a row (`amagama-manage "
                         "sources_status --failing`): %s." % (len(failing), ", ".join(
                             "%s (since %s)" % (block["name"], block["since"])
                             for block in failing)))
    use = stats.get("use")
    if use and now - parse(use["computed"]) > datetime.timedelta(days=USE_DAYS):
        found.append("The lookups per day were last counted on %s (`journalctl -u "
                     "amagama-stats`)." % use["computed"][:10])
    return found


def main(argv):
    if len(argv) not in (2, 3):
        sys.exit(__doc__)
    now = parse(argv[2]) if len(argv) == 3 else datetime.datetime.now(datetime.timezone.utc)
    try:
        with open(argv[1], encoding="utf-8") as f:
            stats = json.load(f)
    except (OSError, ValueError):
        stats = None
    for line in problems(stats, now):
        print("- " + line)


if __name__ == "__main__":
    main(sys.argv)
