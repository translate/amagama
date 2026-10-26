#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks for repositories, from the localisation configuration
they commit, for review.

Each kind of configuration has a resolver, which reads a repository and gives
its blocks' file settings; this names the blocks, checks them and counts what's
left out.
"""

import collections
import re
import time
import urllib.parse

from amagama.updater.weblate import check_block, identity, normal_url

# Copies of other projects' trees.
VENDORED = re.compile(r"(?:^|/)(?:vendor|node_modules|wp-content|subprojects|third_party)/")


def slug(text):
    """text as part of a block's name."""
    return re.sub(r"[^\w.-]+", "-", text).strip("-")


def blocks(repositories, prefix, resolve, what, covered=(), taken=(), previous=None, pause=1,
           licences=None, mirrors=None):
    """(blocks, skipped) for repositories, as resolve reads them.

    resolve(url) gives None if the repository isn't there, else (candidates,
    skipped): a candidate is a block's file settings with a "suffix" for its
    name (None for the repository's name alone), and skipped counts what the
    resolver left out, {(kind, reason): count}. Blocks are dicts of
    sources-file settings; skipped adds repositories and the candidates (what)
    left out here. covered are repositories other blocks already import, and
    taken their names; previous maps the identity() of blocks generated before
    to their names, which are kept; licences maps normal_url() to a
    repository's SPDX licence. mirrors maps a repository to a copy of it for
    resolve to read; the block is the repository's.
    """
    covered = {normal_url(url) for url in covered}
    licences = licences or {}
    names = set(taken)
    found, skipped = [], collections.Counter()
    for number, url in enumerate(repositories):
        if number:
            time.sleep(pause)
        if normal_url(url) in covered:
            skipped["repositories", "imported by another block"] += 1
            continue
        resolved = resolve((mirrors or {}).get(url, url))
        if resolved is None:
            skipped["repositories", "not found"] += 1
            continue
        candidates, reasons = resolved
        skipped.update(reasons)
        licence = licences.get(normal_url(url))
        owner_repo = urllib.parse.urlsplit(url).path.strip("/").removesuffix(".git")
        title = owner_repo.rsplit("/", 1)[-1]
        base = "%s-%s" % (prefix, slug(title))
        if base in names:
            base = "%s-%s" % (prefix, slug(owner_repo))

        def identity_of(settings):
            return identity(url, None, settings.get("template"), settings.get("segment", False),
                            licence)
        identities = collections.Counter(identity_of(candidate) for candidate in candidates)
        for candidate in candidates:
            settings = {key: value for key, value in candidate.items() if key != "suffix"}
            block = dict({"name": None, "title": title, "source": "git", "url": url}, **settings)
            if licence:
                block["licence"] = licence
            # A previous name is kept only when nothing else here reads the same.
            kept = (previous or {}).get(identity_of(settings)) \
                if identities[identity_of(settings)] == 1 else None
            name = kept if kept and kept not in names else (
                "%s-%s" % (base, slug(candidate["suffix"])) if candidate.get("suffix") else base)
            unique, count = name, 2
            while unique in names:
                unique, count = "%s-%d" % (name, count), count + 1
            block["name"] = unique
            try:
                check_block(block)
            except ValueError as e:
                skipped[what, "not a valid block: %s" % str(e).split(": ", 1)[-1]] += 1
                continue
            names.add(unique)
            found.append(block)
    return found, dict(skipped)
