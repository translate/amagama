#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Repositories with committed translations, found by the localisation
configuration they commit, for review.

GitHub's code search finds the configuration files. It needs a token, allows
10 requests a minute and gives at most 1,000 results a query, so a query is
split by file size until each part is under that. The repositories' stats
then select which are kept, and a signal's resolver turns each into blocks.
"""

import datetime
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

import spdx_license_list

from amagama.updater import crowdin, inlang, l10ntoml, lingui, linguas, wlc
from amagama.updater.fetchers import TIMEOUT, USER_AGENT, FetchError

SEARCH = "https://api.github.com/search/code"
ISSUES = "https://api.github.com/search/issues"
GRAPHQL = "https://api.github.com/graphql"
# Code search's most results a query, and per page.
CAP = 1000
PAGE = 100
# Code search indexes files under 384 KB.
MAX_SIZE = 384 * 1024
# The longest wait for a rate limit to reset.
MAX_WAIT = 900

# Each signal: its code search query and the resolver writing its blocks.
SIGNALS = {
    "linguas": {"queries": ["filename:LINGUAS path:po"], "blocks": linguas.blocks},
    "crowdin": {"queries": ["filename:crowdin.yml -path:.github", "filename:crowdin.yaml -path:.github",
                            "filename:.crowdin.yml -path:.github"],
                # Opened by the account that set up Crowdin's GitHub integration.
                "bot_prs": ['"New Crowdin updates" in:title'],
                "blocks": crowdin.blocks},
    "l10n-toml": {"queries": ["filename:l10n.toml paths"], "blocks": l10ntoml.blocks},
    "lingui": {"queries": ["filename:lingui.config.js", "filename:lingui.config.ts",
                           "filename:.linguirc"],
               "blocks": lingui.blocks},
    "inlang": {"queries": ["filename:settings.json path:project.inlang"], "blocks": inlang.blocks},
    "weblate": {"queries": ["filename:.weblate translation"], "blocks": wlc.blocks},
}

FIELDS = ("url stargazerCount pushedAt isArchived isFork licenseInfo { spdxId } description "
          "homepageUrl hasIssuesEnabled")


def _wait(headers, default):
    """Seconds until a rate limit resets, from the response's headers."""
    if headers.get("Retry-After", "").isdigit():
        # Rounded down: "1" for 1.8 seconds.
        return min(int(headers["Retry-After"]) + 1, MAX_WAIT)
    if headers.get("X-RateLimit-Remaining") == "0" and headers.get("X-RateLimit-Reset", "").isdigit():
        return min(max(int(headers["X-RateLimit-Reset"]) - time.time(), 0) + 1, MAX_WAIT)
    return default


def github(url, token, data=None, retries=6, wait=60):
    """A GitHub API request's JSON, POSTing data if given; waits out rate
    limits, and retries server errors and timeouts."""
    headers = {"User-Agent": USER_AGENT, "Authorization": "Bearer " + token,
               "Accept": "application/vnd.github+json"}
    body = None if data is None else json.dumps(data).encode("utf-8")
    for attempt in range(retries + 1):
        request = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            limited = e.code == 429 or (e.code == 403 and (
                "Retry-After" in e.headers or e.headers.get("X-RateLimit-Remaining") == "0"))
            if (limited or e.code >= 500) and attempt < retries:
                time.sleep(_wait(e.headers, wait))
                continue
            raise FetchError("%s: %s" % (url, e), e.code)
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries:
                time.sleep(wait)
                continue
            raise FetchError("%s: %s" % (url, e))
        except ValueError as e:
            raise FetchError("%s: %s" % (url, e))


def _search(url, token, parts, text, split, pause, get):
    """(items, whether any part gave only some of its results) of a GitHub
    search over parts: text(part) is a part's query, and split(part) its
    halves when it has more than 1,000 results, or None if it can't split."""
    items, incomplete, requests = [], False, 0

    def page(query, number):
        nonlocal requests
        if requests:
            time.sleep(pause)
        requests += 1
        return get("%s?%s" % (url, urllib.parse.urlencode(
            {"q": query, "per_page": PAGE, "page": number})), token)

    parts = list(parts)
    while parts:
        part = parts.pop(0)
        query = text(part)
        data = page(query, 1)
        total = data.get("total_count", 0)
        halves = split(part) if total > CAP else None
        if halves:
            parts[:0] = halves
            continue
        incomplete = incomplete or bool(data.get("incomplete_results")) or total > CAP
        found, number = list(data.get("items", [])), 1
        while data.get("items") and number * PAGE < min(total, CAP):
            number += 1
            data = page(query, number)
            found += data.get("items", [])
        items += found
    return items, incomplete


def search(query, token, pause=6, get=github):
    """(sorted "owner/name" of the repositories with a file query matches,
    whether any part gave only some of its results)."""
    def split(part):
        low, high = part
        return [(low, (low + high) // 2), ((low + high) // 2 + 1, high)] if high > low else None
    items, incomplete = _search(SEARCH, token, [(0, MAX_SIZE)],
                                lambda part: "%s size:%d..%d" % ((query,) + part), split, pause, get)
    return sorted({item["repository"]["full_name"] for item in items}), incomplete


def search_prs(query, token, since, until=None, pause=2, get=github):
    """(sorted "owner/name" of the repositories with a pull request matching
    query merged from since to until (default today), whether any part gave
    only some of its results). A merge pushes, so the stats' last push is at
    least as recent as the merge."""
    def split(part):
        low, high = part
        if high <= low:
            return None
        middle = low + (high - low) // 2
        return [(low, middle), (middle + datetime.timedelta(days=1), high)]
    items, incomplete = _search(
        ISSUES, token, [(since, until or datetime.date.today())],
        lambda part: "%s is:pr is:merged merged:%s..%s" % (query, part[0].isoformat(),
                                                         part[1].isoformat()),
        split, pause, get)
    return sorted({item["repository_url"].split("/repos/", 1)[-1] for item in items}), incomplete


def github_name(url):
    """A GitHub repository's "owner/name", from its URL, or None."""
    parts = urllib.parse.urlsplit(url or "")
    path = parts.path.strip("/").removesuffix(".git").split("/")
    return "/".join(path) if parts.hostname == "github.com" and len(path) == 2 and all(path) \
        else None


def stats(names, token, batch=50, pause=1, get=github):
    """{"owner/name": stats} of GitHub repositories, a GraphQL query per
    batch; one that isn't there is left out."""
    names = sorted(names)
    found = {}
    for start in range(0, len(names), batch):
        if start:
            time.sleep(pause)
        part = names[start:start + batch]
        query = "{%s}" % " ".join(
            "r%d: repository(owner: %s, name: %s) {%s}" % (
                number, json.dumps(name.split("/", 1)[0]), json.dumps(name.split("/", 1)[-1]), FIELDS)
            for number, name in enumerate(part))
        data = get(GRAPHQL, token, {"query": query})
        if not data.get("data"):
            raise FetchError("%s: %s" % (GRAPHQL, data.get("errors") or "no data"))
        for number, name in enumerate(part):
            if data["data"].get("r%d" % number):
                found[name] = data["data"]["r%d" % number]
    return found


def licence(repository):
    """A repository's SPDX licence, or None if GitHub didn't detect one.

    GitHub gives the deprecated GPL-3.0 and its like, which SPDX defines as
    GPL-3.0-only: a licence file doesn't say "or later".
    """
    spdx = (repository.get("licenseInfo") or {}).get("spdxId")
    if not spdx or spdx == "NOASSERTION":
        return None
    listed = spdx_license_list.LICENSES.get(spdx)
    if listed and listed.deprecated_id and spdx + "-only" in spdx_license_list.LICENSES:
        return spdx + "-only"
    return spdx


def upstream(repository):
    """The repository a GitHub mirror copies, or None.

    GitHub's own isMirror is only for mirrors it pulls. A project's mirror
    (GNOME's, Xfce's) says "mirror" or has its issues off, and links the
    repository of the same name elsewhere in its description, or (if it
    says "mirror") as its homepage, which is otherwise often a website.
    """
    description = repository.get("description") or ""
    mirror = "mirror" in description.lower()
    if not mirror and repository.get("hasIssuesEnabled", True):
        return None
    name = repository["url"].rstrip("/").rsplit("/", 1)[-1].lower()
    links = re.findall(r"(?:https?|git)://[^\s,;()<>\[\]]+", description)
    if mirror and repository.get("homepageUrl"):
        links.append(repository["homepageUrl"])
    for link in links:
        parts = urllib.parse.urlsplit(link.rstrip(".").rstrip("/"))
        path = parts.path.strip("/")
        if parts.scheme in ("http", "https", "git") and parts.hostname not in (None, "github.com") \
                and path.removesuffix(".git").rsplit("/", 1)[-1].lower() == name:
            return "%s://%s/%s" % (parts.scheme, parts.netloc, path)
    return None


def default_pushed_since(today=None):
    """1 January of last year."""
    return datetime.date((today or datetime.date.today()).year - 1, 1, 1)


def select(found, min_stars=100, pushed_since=None, licensed=True):
    """(repositories kept, skipped) of stats(): kept are the stats of those
    not archived or forks, with at least min_stars, pushed since the date
    and (if licensed) a licence, most stars first; skipped counts the rest,
    {("repositories", reason): count}."""
    kept, skipped = [], {}

    def skip(reason):
        skipped["repositories", reason] = skipped.get(("repositories", reason), 0) + 1

    for repository in found.values():
        pushed = repository.get("pushedAt")
        pushed = datetime.date.fromisoformat(pushed[:10]) if pushed else None
        if repository.get("isArchived"):
            skip("archived")
        elif repository.get("isFork"):
            skip("fork")
        elif repository.get("stargazerCount", 0) < min_stars:
            skip("fewer than %d stars" % min_stars)
        elif pushed_since and (pushed is None or pushed < pushed_since):
            skip("not pushed since %s" % pushed_since.isoformat())
        elif licensed and not licence(repository):
            skip("no licence detected")
        else:
            kept.append(repository)
    kept.sort(key=lambda repository: (-repository.get("stargazerCount", 0), repository["url"].lower()))
    return kept, skipped
