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
import posixpath
import re
import time
import urllib.parse

from amagama.updater.fetchers import FetchError, run_tool
from amagama.updater.generate import (Names, check_block, clone, identity, matches, matching,
                                      normal_url, with_mirror)
from amagama.updater.languages import normalise_language
from amagama.updater.sources import sparse_patterns

# Copies of other projects' trees.
VENDORED = re.compile(r"(?:^|/)(?:vendor|node_modules|wp-content|subprojects|third_party)/")
# Tests' own configurations, of made-up projects.
TESTS = re.compile(r"(?:^|/)(?:tests?|__tests__|testdata|fixtures?)/")
# amaGama's format of a file, by its extension ("po" for none).
EXTENSIONS = {
    ".po": "po",
    ".pot": "po",
    ".ts": "ts",
    ".xliff": "xliff",
    ".xlf": "xliff",
    ".json": "json",
    ".properties": "properties",
    ".php": "php",
    ".ftl": "fluent",
    ".dtd": "dtd",
}


def format_of(path):
    """amaGama's format for a file by its name, or None if amaGama doesn't read it."""
    extension = posixpath.splitext(path)[1].lower()
    if extension == ".xml" and re.search(r"(?:^|/)values[^/]*/[^/]+\.xml\Z", path):
        return "android"
    return EXTENSIONS.get(extension)


def is_english(code):
    lang = normalise_language(code) if code else None
    return bool(lang) and re.split(r"[_@]", lang)[0] == "en"


def join(directory, path):
    """A configuration's path, relative to its directory, as relative to the
    repository; None if it leads outside."""
    path = re.sub(r"/+", "/", str(path).strip().replace("\\", "/"))
    while path.startswith("./"):
        path = path[2:]
    joined = posixpath.normpath(posixpath.join(directory, path.lstrip("/")))
    if joined == ".":
        return ""
    return None if joined.startswith("../") or joined == ".." else joined


def slug(text):
    """text as part of a block's name."""
    return re.sub(r"[^\w.-]+", "-", text).strip("-")


def blocks(repositories, prefix, resolve, what, covered=(), names=None, pause=1, licences=None,
           mirrors=None):
    """(blocks, skipped) for repositories, as resolve reads them.

    resolve(url) gives None if the repository isn't there, else (candidates,
    skipped): a candidate is a block's file settings with a "suffix" for its
    name (None for the repository's name alone), and skipped counts what the
    resolver left out, {(kind, reason): count}. Blocks are dicts of
    sources-file settings; skipped adds repositories and the candidates (what)
    left out here. covered are repositories other blocks already import;
    names is the generate.Names the blocks take theirs from; licences maps
    normal_url() to a repository's SPDX licence. mirrors maps a repository
    to a copy of it for resolve to read; the block is the repository's, and
    fetches from the copy if the repository is on a community host.
    """
    covered = {normal_url(url) for url in covered}
    licences = licences or {}
    names = names or Names()
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
            block = with_mirror(dict({"name": None, "title": title, "source": "git", "url": url},
                                     **settings), (mirrors or {}).get(url), exists=lambda url: False)
            if licence:
                block["licence"] = licence
            # A previous name is kept only when nothing else here reads the same.
            kept = names.kept(identity_of(settings)) \
                if identities[identity_of(settings)] == 1 else None
            name = kept or (
                "%s-%s" % (base, slug(candidate["suffix"])) if candidate.get("suffix") else base)
            unique = names.unique(name)
            block["name"] = unique
            try:
                check_block(block)
            except ValueError as e:
                skipped[what, "not a valid block: %s" % str(e).split(": ", 1)[-1]] += 1
                continue
            names.take(unique)
            found.append(block)
    return found, dict(skipped)


def _read(directory, path):
    return run_tool("git", "-C", directory, "show", "HEAD:" + path, trusted=directory)


def configs(paths, configuration):
    """The paths configuration (a compiled pattern) matches, but vendored
    copies and tests'."""
    return [path for path in paths if configuration.search(path)
            and not VENDORED.search(path) and not TESTS.search(path)]


def read(url, configuration):
    """(paths, {configs() path: text, or None if unreadable}) of url's
    default branch, or None if the repository isn't there."""
    with clone(url, None) as cloned:
        if cloned is None:
            return None
        directory, paths = cloned
        texts = {}
        for path in configs(paths, configuration):
            try:
                texts[path] = _read(directory, path)
            except FetchError:
                texts[path] = None
        return paths, texts


def candidates(files, paths, what, nothing):
    """(candidates, skipped) for repositories.blocks() from the files a
    repository's configurations give, and its paths.

    Each file is a dict: its translations (mask), amaGama format ("po" for
    none), template (a monolingual source), source (a bilingual one, which
    the mask mustn't take for a translation), language overrides and name.
    Files of one format and language overrides share a block, but a
    monolingual file has its own, for its template, named by the file. A
    mask matching no file is dropped; skipped counts the groups left, as
    (what, nothing).
    """
    groups, skipped = {}, collections.Counter()
    for found in files:
        key = (found["format"], found["template"] or "", tuple(sorted(found["languages"].items())))
        group = groups.setdefault(key, dict(found, masks=set(), sources=set()))
        group["masks"].add(found["mask"])
        if found["source"]:
            group["sources"].add(found["source"])
    found_candidates = []
    # The repository's shared block first, then a block per template.
    for (file_format, template, languages), group in sorted(
            groups.items(), key=lambda item: (bool(item[0][1]), item[0])):
        template = template or None
        found_masks = matching(group["masks"], paths, template)
        if not found_masks:
            skipped[what, nothing] += 1
            continue
        candidate = {"suffix": group["name"] if template else None, "files": found_masks}
        # A source file a mask also matches isn't a translation.
        exclude = sorted(source for source in group["sources"]
                         if any(matches(mask, source) for mask in found_masks))
        if exclude:
            candidate["exclude"] = exclude
        candidate["sparse"] = sparse_patterns(found_masks, template)
        if file_format != "po":
            candidate["format"] = file_format
        if template:
            candidate["template"] = template
        if languages:
            candidate["languages"] = dict(languages)
        found_candidates.append(candidate)
    return found_candidates, skipped
