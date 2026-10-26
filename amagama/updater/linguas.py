#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' gettext po/LINGUAS, for review.

A gettext project lists its languages in po/LINGUAS and keeps each one's
translations beside it, in po/<lang>.po. The block's languages are the files
there, not the list: a language without a file has nothing to import.
"""

import collections
import os
import posixpath
import re

from amagama.updater.fetchers import FetchError, run_tool
from amagama.updater.formats import open_store
from amagama.updater.languages import normalise_language
from amagama.updater.sources import sparse_patterns
from amagama.updater.repositories import VENDORED
from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.weblate import clone

LINGUAS = re.compile(r"(?:^|/)po/LINGUAS\Z")


def candidates(tree):
    """{directory: (.po files, fingerprint)} for each po/LINGUAS in a tree of
    (path, blob id), but vendored ones and those without a language's .po
    beside them. The fingerprint, the POT's blob ids else the .po files',
    tells a copy of another repository's translations."""
    by_directory = {}
    for path, blob in tree:
        by_directory.setdefault(posixpath.dirname(path), {})[posixpath.basename(path)] = blob
    found = {}
    for directory, files in sorted(by_directory.items()):
        if "LINGUAS" not in files or not LINGUAS.search(posixpath.join(directory, "LINGUAS")) \
                or VENDORED.search(directory + "/"):
            continue
        translations = sorted(name for name in files
                              if name.endswith(".po") and normalise_language(name[:-3]))
        if translations:
            pots = sorted(blob for name, blob in files.items() if name.endswith(".pot"))
            found[directory] = ([posixpath.join(directory, name) for name in translations],
                                tuple(pots) or tuple(files[name] for name in translations))
    return found


def translated(directory, paths):
    """Translated units, not fuzzy, in the files at paths of a clone made
    without a checkout; only those files are checked out."""
    patterns = ["/" + re.sub(r"([*?\[\\])", r"\\\1", path) for path in paths]
    run_tool("git", "-C", directory, "sparse-checkout", "set", "--no-cone", *patterns,
             trusted=directory)
    run_tool("git", "-C", directory, "checkout", "--quiet", "HEAD", trusted=directory)
    count = 0
    for path in paths:
        try:
            store = open_store(os.path.join(directory, path))
        except (ValueError, UnicodeError, OSError):
            continue
        count += sum(1 for unit in store.units if not unit.isheader() and unit.istranslated())
    return count


def repository(url, seen=()):
    """{directory: (.po files, fingerprint, translated units)} of each of
    candidates() in url's default branch, or None if the repository isn't
    there. A directory whose fingerprint is in seen isn't read: units None."""
    with clone(url, None) as cloned:
        if cloned is None:
            return None
        directory = cloned[0]
        try:
            listing = run_tool("git", "-C", directory, "ls-tree", "-r", "HEAD", trusted=directory)
        except FetchError:
            return None
        tree = []
        for line in listing.splitlines():
            meta, _, path = line.partition("\t")
            if meta.split()[1:2] == ["blob"]:
                tree.append((path, meta.split()[2]))
        found = {}
        for po_dir, (files, fingerprint) in candidates(tree).items():
            try:
                units = None if fingerprint in seen else translated(directory, files)
            except FetchError:
                continue
            found[po_dir] = (files, fingerprint, units)
        return found


def blocks(repositories, prefix, covered=(), taken=(), previous=None, pause=1, read=None,
           licences=None, min_units=100, mirrors=None):
    """(blocks, skipped) for repositories' po/LINGUAS; see
    repositories.blocks() for the arguments and what skipped counts.

    A directory with fewer than min_units translated units, or with the
    translations of a repository before it, is left out: give the original
    repositories first. A repository's only block has its name; more are
    named by directory.
    """
    fingerprints = set()

    def resolve(url):
        directories = (read or repository)(url, frozenset(fingerprints))
        if directories is None:
            return None
        if not directories:
            return [], {("repositories", "no po/LINGUAS with translations beside it"): 1}
        skipped = collections.Counter()
        kept = []
        for directory, (files, fingerprint, units) in sorted(directories.items()):
            if fingerprint in fingerprints:
                skipped["directories", "copy of another repository's translations"] += 1
            elif units < min_units:
                skipped["directories", "fewer than %d translated units" % min_units] += 1
            else:
                fingerprints.add(fingerprint)
                kept.append(directory)
        candidates = []
        for directory in kept:
            mask = posixpath.join(directory, "{lang}.po")
            suffix = directory.removesuffix("/po") if len(kept) > 1 and directory != "po" else None
            candidates.append({"suffix": suffix, "files": [mask], "sparse": sparse_patterns([mask])})
        return candidates, skipped

    return repository_blocks(repositories, prefix, resolve, "directories", covered, taken,
                             previous, pause, licences, mirrors)
