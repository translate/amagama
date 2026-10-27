#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""What the commands writing sources-file blocks share: naming, checking
blocks against their repositories, and writing them for review."""

import contextlib
import json
import os
import re
import tempfile
import time
import tomllib
import urllib.parse

from amagama.licences import normalise_licence
from amagama.updater.fetchers import FetchError, run_tool
from amagama.updater.filemask import compile_mask
from amagama.updater.sources import Block, sparse_patterns


# Hosts run by and for a community project, whose load a mirror takes off:
# a block from one fetches from its mirror (see Block.fingerprint).
COMMUNITY_HOSTS = {
    "codeberg.org", "git.savannah.gnu.org", "git.savannah.nongnu.org", "git.sv.gnu.org",
    "gitlab.freedesktop.org", "gitlab.gnome.org", "gitlab.xfce.org", "invent.kde.org",
    "salsa.debian.org",
}
# Projects' own mirrors of their repositories: (pattern of the repository,
# the mirror with the pattern's group).
KNOWN_MIRRORS = [
    (r"https://gitlab\.gnome\.org/GNOME/([\w.-]+?)(?:\.git)?/?", "https://github.com/GNOME/{0}"),
    (r"https://invent\.kde\.org/[\w.-]+/([\w.-]+?)(?:\.git)?/?", "https://github.com/KDE/{0}"),
    (r"https://gitlab\.xfce\.org/[\w.-]+/([\w.-]+?)(?:\.git)?/?",
     "https://github.com/xfce-mirror/{0}"),
]


def exists(url):
    """Whether url is a git repository that can be read."""
    try:
        run_tool("git", "ls-remote", url, "HEAD")
    except FetchError:
        return False
    return True


def mirror(url, copy=None, exists=exists):
    """The mirror a block of url fetches from, or None: copy (one a search
    found) or a known mirror that exists, if url is on a community host."""
    if urllib.parse.urlsplit(url).hostname not in COMMUNITY_HOSTS:
        return None
    if copy:
        return copy
    for pattern, template in KNOWN_MIRRORS:
        match = re.fullmatch(pattern, url)
        if match and exists(template.format(*match.groups())):
            return template.format(*match.groups())
    return None


def with_mirror(block, copy=None, exists=exists):
    """block with its mirror() after its url, if it has one."""
    found = block.get("mirror") or mirror(block["url"], copy, exists)
    if not found:
        return block
    settings = {}
    for key, value in block.items():
        if key != "mirror":
            settings[key] = value
        if key == "url":
            settings["mirror"] = found
    return settings


def add_mirrors(blocks, pause=1, exists=exists):
    """blocks with mirrors where they have one; each repository is looked up once."""
    known = {}

    def cached(url):
        if url not in known:
            known[url] = exists(url)
            time.sleep(pause)
        return known[url]
    return [with_mirror(block, exists=cached) for block in blocks]


def normal_url(repo):
    """A repository URL without scheme, credentials, trailing .git or /."""
    parts = urllib.parse.urlsplit(repo)
    return (parts.hostname or "") + re.sub(r"(\.git)?/*$", "", parts.path)


def identity(url, ref, template, segment, licence):
    """What a block reads, for keeping its name when it's regenerated: not
    its format, so a corrected reader keeps the name."""
    try:
        licence = normalise_licence(licence) if licence else None
    except ValueError:
        pass
    return (normal_url(url), ref or None, template or None, bool(segment), licence)


def previous_names(blocks):
    """{identity(): name} of loaded sources-file blocks, for Names."""
    return {identity(block.fetcher.settings.get("url", ""), block.fetcher.settings.get("ref"),
                     block.template, block.segment, block.licence): block.name
            for block in blocks if block.source == "git"}


class Names(object):
    """The names blocks being written may take.

    taken are other blocks' names; previous maps the identity() of blocks
    generated before to their names, which are kept: a block's name keys its
    imports.
    """

    def __init__(self, taken=(), previous=None):
        self.taken = set(taken)
        self.previous = previous or {}

    def __contains__(self, name):
        return name in self.taken

    def kept(self, identity):
        """The name a block with identity had before, if it's still free."""
        name = self.previous.get(identity)
        return name if name and name not in self.taken else None

    def unique(self, name):
        """name, or name-2, name-3... if it's taken."""
        unique, number = name, 2
        while unique in self.taken:
            unique, number = "%s-%d" % (name, number), number + 1
        return unique

    def take(self, name):
        self.taken.add(name)

    def release(self, name):
        self.taken.discard(name)


def context(blocks, prefix):
    """(Names, covered repositories, disabled) for regenerating the blocks
    of loaded blocks whose names start with prefix.

    Those blocks keep their names and don't count as imported elsewhere.
    covered are the repositories other enabled blocks import; disabled maps
    the prefix's disabled blocks' names to their settings as written.
    """
    own = [block for block in blocks if block.name.startswith(prefix + "-")]
    others = [block for block in blocks if not block.name.startswith(prefix + "-")]
    disabled = {}
    for path in sorted({block.path for block in own if not block.enabled}):
        with open(path, "rb") as f:
            written = {entry.get("name"): entry for entry in tomllib.load(f).get("block", [])}
        disabled.update((block.name, dict(written[block.name], disabled=block.disabled))
                        for block in own if not block.enabled and block.path == path)
    covered = [block.fetcher.settings["url"] for block in others
               if block.enabled and block.source in ("git", "svn")]
    return Names({block.name for block in others}, previous_names(own)), covered, disabled


def check_block(block):
    """Check a block's settings as the sources file would; ValueError if not."""
    settings = dict(block)
    licence = settings.pop("licence", None)
    if licence is not None:
        licence = normalise_licence(licence)
    Block(settings.pop("name"), settings.pop("title"), settings.pop("source"),
          settings.pop("version", None), licence, **settings)


def toml(blocks, header, defaults=None):
    """blocks as sources-file text, after header's comment lines.

    defaults become the file's defaults table, left out of each block.
    """
    def value(setting):
        if isinstance(setting, dict):
            return "{%s}" % ", ".join("%s = %s" % (json.dumps(key, ensure_ascii=False), value(item))
                                      for key, item in setting.items())
        return json.dumps(setting, ensure_ascii=False)

    def settings(table):
        return ["%s = %s" % (key, value(setting)) for key, setting in table.items()]
    lines = ["# " + line if line else "#" for line in header.splitlines()]
    defaults = defaults or {}
    if defaults:
        lines += ["", "[defaults]"] + settings(defaults)
    for block in blocks:
        lines += ["", "[[block]]"]
        lines += settings({key: value for key, value in block.items()
                           if key not in defaults or defaults[key] != value})
    return "\n".join(lines) + "\n"


def with_disabled(found, disabled):
    """found with the blocks named in disabled marked so again, then those
    of disabled not found again as they were written."""
    names = {block["name"] for block in found}
    missing = [settings for name, settings in sorted(disabled.items()) if name not in names]
    return [dict({"name": block["name"], "disabled": disabled[block["name"]]["disabled"]}, **block)
            if block["name"] in disabled else block for block in found] + missing, len(missing)


@contextlib.contextmanager
def clone(url, ref):
    """(directory, file paths) of a clone of url at ref (None: its default
    branch), or None if the repository or ref isn't there. Only the trees
    are downloaded; a file's contents come when it's read."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = os.path.join(tmp, "repo")
        args = ["clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout"]
        if ref:
            args += ["--branch", ref]
        try:
            run_tool("git", *(args + [url, directory]))
            paths = run_tool("git", "-C", directory, "ls-tree", "-r", "--name-only", "HEAD",
                             trusted=directory).splitlines()
        except FetchError:
            yield None
        else:
            yield directory, paths


def tree(url, ref):
    """The file paths of url at ref, or None; see clone()."""
    with clone(url, ref) as cloned:
        return cloned and cloned[1]


def matches(mask, path):
    """Whether a filemask matches path; a mask that isn't valid matches nothing."""
    try:
        return bool(compile_mask(mask).match(path))
    except ValueError:
        return False


def has_template(template, paths):
    try:
        mask = compile_mask(template, allow_lang=False)
    except ValueError:
        return False
    return any(mask.match(path) for path in paths)


def matching(masks, paths, template=None):
    """The masks matching any of paths; none if template isn't among them."""
    if template and not has_template(template, paths):
        return []
    return sorted(mask for mask in masks if any(matches(mask, path) for path in paths))


def verify(blocks, pause=1, tree=tree):
    """(blocks, problems): blocks as their repositories have them now.

    A generator's data can be out of date. A missing branch falls back to
    the default branch; masks matching nothing are dropped; a block left
    with none, or whose template is missing, is left out. problems counts by
    reason.
    """
    trees, kept, seen, problems = {}, [], set(), {}

    def paths(url, ref):
        if (url, ref) not in trees:
            trees[url, ref] = tree(url, ref)
            time.sleep(pause)
        return trees[url, ref]

    def problem(reason):
        problems[reason] = problems.get(reason, 0) + 1

    for block in blocks:
        ref = block.get("ref")
        found = paths(block["url"], ref)
        if found is None and ref:
            ref, found = None, paths(block["url"], None)
        if found is None:
            problem("repository or branch not found")
            continue
        template = block.get("template")
        present = set(matching(block["files"], found, template))
        masks = [mask for mask in block["files"] if mask in present]
        key = (block["url"], ref, tuple(masks), template)
        if not masks or key in seen:
            problem("duplicate after branch fallback" if masks else "filemask matches nothing")
            continue
        seen.add(key)
        block = dict(block, files=masks)
        block["sparse"] = sparse_patterns(masks, template)
        if ref != block.get("ref"):
            problem("branch gone, default branch used")
            block.pop("ref", None)
            block.pop("version", None)
        kept.append(block)
    return kept, problems
