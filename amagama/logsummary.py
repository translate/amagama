#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Count the API requests in nginx access logs by their Sec-Purpose."""

import gzip
import re

ORDINARY = "ordinary"
PREFETCH = "prefetch"
BULK = "bulk"
KINDS = (ORDINARY, PREFETCH, BULK)

API_PREFIXES = ("/api/", "/tmserver/")

# The combined format, optionally followed by the quoted Sec-Purpose header
# (deploy/nginx-amagama.conf's "amagama" format). nginx escapes quotes in
# variables as \x22, so a quoted field has none.
LOG_LINE = re.compile(
    r'^\S+ \S+ \S+ \[[^\]]*\] "(?P<request>[^"]*)" (?P<status>\d{3}) \S+ '
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


def summarise(paths):
    """Count API requests and 429s per kind: {kind: [requests, limited]}."""
    counts = {kind: [0, 0] for kind in KINDS}
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
                count = counts[classify(None if purpose == "-" else purpose)]
                count[0] += 1
                if match.group("status") == "429":
                    count[1] += 1
    return counts
