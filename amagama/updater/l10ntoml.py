#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' Mozilla l10n.toml, for review.

A project localised with Mozilla's tools (Pontoon, compare-locales) commits
l10n.toml, whose [[paths]] each give the English files (reference) and
where their translations go (l10n, with {locale}), after the [env]
variables are put in. Translations kept in another repository
({l10n_base} elsewhere) match nothing here and are left out.
"""

import collections
import posixpath
import re
import tomllib

from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.repositories import candidates, format_of, join
from amagama.updater.repositories import read as read_configs

CONFIG = re.compile(r"(?:^|/)l10n\.toml\Z")
LANGUAGE = ("{locale}", "{android_locale}")


def _expand(text, env):
    """text with the [env] variables put in."""
    for _ in range(10):
        expanded = re.sub(r"\{(\w+)\}", lambda m: env.get(m.group(1), m.group(0)), text)
        if expanded == text:
            break
        text = expanded
    return text


def _files(reference, l10n, paths):
    """[(translation mask, English template)] of a path pair: a "**" reads
    every file below, one pair per extension found under the reference."""
    for placeholder in LANGUAGE:
        if placeholder in l10n:
            l10n = l10n.replace(placeholder, "{lang}", 1).replace(placeholder, "*")
    if "{lang}" not in l10n:
        return []
    if not reference.endswith("**"):
        return [(l10n, reference)]
    below = reference[:-2]
    extensions = sorted({posixpath.splitext(path)[1] for path in paths
                         if path.startswith(below) and posixpath.splitext(path)[1]})
    return [(l10n[:-2] + "**/*" + extension, below + "**/*" + extension)
            for extension in extensions if l10n.endswith("**")]


def files(text, directory, paths):
    """(files, skipped) of an l10n.toml in directory of the repository: the
    dicts repositories.candidates() takes."""
    found, skipped = [], collections.Counter()
    try:
        config = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        skipped["unreadable l10n.toml"] += 1
        return found, skipped
    basepath = config.get("basepath", ".")
    base = join(directory, basepath if isinstance(basepath, str) else ".")
    env = {key: value for key, value in (config.get("env") or {}).items()
           if isinstance(value, str)}
    # Paths are relative to the base path; the translations are taken to be
    # in this repository.
    env["l10n_base"] = ""
    for entry in config.get("paths") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("reference"), str) \
                or not isinstance(entry.get("l10n"), str):
            skipped["path without reference or l10n"] += 1
            continue
        reference = join(base or "", _expand(entry["reference"], env))
        l10n = join(base or "", _expand(entry["l10n"], env))
        if reference is None or l10n is None:
            skipped["path outside the repository"] += 1
            continue
        pairs = _files(reference, l10n, paths)
        if not pairs:
            skipped["l10n without {locale}"] += 1
        for mask, template in pairs:
            file_format = format_of(template)
            if file_format is None:
                skipped["type %s" % (posixpath.splitext(template)[1] or "unknown")] += 1
                continue
            # Named by the file, or by the directory a pattern's languages are in.
            name = posixpath.splitext(posixpath.basename(template))[0]
            if "*" in name:
                name = posixpath.basename(mask.split("{lang}")[0].rstrip("/")) or "files"
            found.append({"name": name, "mask": mask,
                          "format": file_format,
                          "template": template if file_format != "po" else None,
                          "source": None, "languages": {}})
    return found, skipped


def repository(url):
    """repositories.read() of url's l10n.toml files."""
    return read_configs(url, CONFIG)


def blocks(repositories, prefix, covered=(), names=None, pause=1, read=None, licences=None,
           mirrors=None, min_units=None):
    """(blocks, skipped) for repositories' l10n.toml; see repositories.blocks()
    for the arguments and what skipped counts. Units aren't counted
    (min_units): monolingual files need their template to count."""
    def resolve(url):
        read_back = (read or repository)(url)
        if read_back is None:
            return None
        paths, texts = read_back
        if not texts:
            return [], {("repositories", "no l10n.toml"): 1}
        skipped = collections.Counter()
        entries = []
        for config, text in sorted(texts.items()):
            if text is None:
                skipped["paths", "unreadable l10n.toml"] += 1
                continue
            found, reasons = files(text, posixpath.dirname(config), paths)
            skipped.update({("paths", reason): count for reason, count in reasons.items()})
            entries += found
        found, reasons = candidates(entries, paths, "paths", "l10n matches nothing")
        return found, skipped + reasons

    return repository_blocks(repositories, prefix, resolve, "paths", covered, names, pause,
                             licences, mirrors)
