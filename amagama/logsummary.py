#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Count the API requests in nginx access logs by their Sec-Purpose, and
measure each client's lookup rate."""

import collections
import gzip
import re
from datetime import datetime, timedelta, timezone

ORDINARY = "ordinary"
PREFETCH = "prefetch"
BULK = "bulk"
KINDS = (ORDINARY, PREFETCH, BULK)

API_PREFIXES = ("/api/", "/tmserver/")

# The combined format, optionally followed by the quoted Sec-Purpose header
# (deploy/nginx-amagama.conf's "amagama" format). nginx escapes quotes in
# variables as \x22, so a quoted field has none.
# $time_local, parsed by hand: strptime's %b follows the locale.
TIME = re.compile(
    r"^(\d\d)/(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)/(\d{4}):"
    r"(\d\d):(\d\d):(\d\d) ([+-])(\d\d)(\d\d)$")
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()

LOG_LINE = re.compile(
    r'^(?P<client>\S+) \S+ \S+ \[(?P<time>[^\]]*)\] "(?P<request>[^"]*)" '
    r'(?P<status>\d{3}) \S+ '
    r'"[^"]*" "[^"]*"(?: "(?P<purpose>[^"]*)")?')


def _split(value, sep):
    """Split value on sep outside double-quoted strings."""
    parts, start, quoted, i = [], 0, False, 0
    while i < len(value):
        char = value[i]
        if quoted and char == "\\":
            i += 1
        elif char == '"':
            quoted = not quoted
        elif char == sep and not quoted:
            parts.append(value[start:i])
            start = i + 1
        i += 1
    parts.append(value[start:])
    return parts


def classify(sec_purpose):
    """Return ORDINARY, PREFETCH or BULK for a Sec-Purpose header value.

    The value is a Structured Fields list (RFC 8941): a "prefetch" item is a
    prefetch, and with a true "bulk" parameter (";bulk" or ";bulk=?1") a bulk
    fetch. Case and spacing are taken loosely, as in the nginx map.
    """
    kind = ORDINARY
    for member in _split(sec_purpose or "", ","):
        item, *params = (part.strip() for part in _split(member, ";"))
        if item.lower() != "prefetch":
            continue
        kind = PREFETCH
        for param in params:
            key, _, value = param.partition("=")
            if key.strip().lower() == "bulk" and value.strip() in ("", "?1"):
                return BULK
    return kind


def _open(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, encoding="utf-8", errors="replace")


def _requests(paths):
    """Yield (match, kind) for each API request in the logs."""
    for path in paths:
        with _open(path) as log:
            for line in log:
                match = LOG_LINE.match(line)
                if not match:
                    continue
                request = match.group("request").split(" ")
                if len(request) < 2 or not request[1].startswith(API_PREFIXES):
                    continue
                purpose = match.group("purpose")
                yield match, classify(None if purpose == "-" else purpose)


def _seconds(time_local):
    """Seconds since the epoch for a $time_local, or None."""
    match = TIME.match(time_local)
    if not match:
        return None
    day, month, year, hour, minute, second, sign, tzh, tzm = match.groups()
    offset = timedelta(hours=int(tzh), minutes=int(tzm)) * (-1 if sign == "-" else 1)
    try:
        stamp = datetime(int(year), MONTHS.index(month) + 1, int(day), int(hour),
                         int(minute), int(second), tzinfo=timezone(offset))
    except ValueError:
        return None
    return int(stamp.timestamp())


def summarise(paths):
    """Count API requests and 429s per kind: {kind: [requests, limited]}."""
    counts = {kind: [0, 0] for kind in KINDS}
    for match, kind in _requests(paths):
        count = counts[kind]
        count[0] += 1
        if match.group("status") == "429":
            count[1] += 1
    return counts


def lookups_per_second(paths):
    """Count each client's lookups (ordinary and prefetch, what nginx's
    lookup limit counts) per second: [{second: requests}], one per client,
    in no order. Clients aren't named."""
    clients = collections.defaultdict(collections.Counter)
    last_time, second = None, None
    for match, kind in _requests(paths):
        if kind == BULK:
            continue
        time = match.group("time")
        if time != last_time:
            last_time, second = time, _seconds(time)
        if second is not None:
            clients[match.group("client")][second] += 1
    return list(clients.values())


def peaks(seconds):
    """The most requests in one second, and in any 60 seconds."""
    times = sorted(seconds)
    per_minute, total, start = 0, 0, 0
    for time in times:
        total += seconds[time]
        while times[start] <= time - 60:
            total -= seconds[times[start]]
            start += 1
        per_minute = max(per_minute, total)
    return max(seconds.values(), default=0), per_minute


def over_limit(seconds, rate, burst):
    """How many requests nginx's limit_req (rate a second, burst) would
    refuse. The logs time requests to the second, so it's close, not
    exact."""
    # nginx's leaky bucket, in thousandths of a request.
    excess, last, refused = 0, None, 0
    for time in sorted(seconds):
        for _n in range(seconds[time]):
            if last is None:
                new = 0
            else:
                new = max(excess - rate * (time - last) * 1000 + 1000, 0)
            if new > burst * 1000:
                refused += 1
                continue
            excess, last = new, time
    return refused


def percentile(values, share):
    """The nearest-rank percentile of sorted values."""
    return values[max(0, -(-len(values) * share // 100) - 1)]
