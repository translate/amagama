#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' inlang projects, for review.

A project translated with inlang (Paraglide, Fink, Sherlock) commits
project.inlang/settings.json: the source language (sourceLanguageTag, or
baseLocale) and, for the plugin storing its messages, a pathPattern with
{languageTag} or {locale}, relative to the directory holding
project.inlang. The messages are JSON, so the source language's file is
the template.
"""

import collections
import json
import posixpath
import re

from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.repositories import candidates, is_english, join
from amagama.updater.repositories import read as read_configs

CONFIG = re.compile(r"(?:^|/)project\.inlang/settings\.json\Z")
LANGUAGE = ("{languageTag}", "{locale}")


def files(path, text):
    """(files, skipped) of a project.inlang/settings.json at path in the
    repository: the dicts repositories.candidates() takes."""
    found, skipped = [], collections.Counter()
    try:
        settings = json.loads(text)
    except ValueError:
        settings = None
    if not isinstance(settings, dict):
        skipped["unreadable settings.json"] += 1
        return found, skipped
    source = settings.get("sourceLanguageTag") or settings.get("baseLocale")
    if not isinstance(source, str) or not is_english(source):
        skipped["source not English"] += 1
        return found, skipped
    # The directory holding project.inlang.
    root = posixpath.dirname(posixpath.dirname(path))
    patterns = []
    for key, plugin in sorted(settings.items()):
        if key.startswith("plugin.") and isinstance(plugin, dict):
            pattern = plugin.get("pathPattern")
            # i18next's namespaces: {namespace: pattern}.
            patterns += [(key, value) for value in (
                pattern.values() if isinstance(pattern, dict) else
                pattern if isinstance(pattern, list) else [pattern]) if isinstance(value, str)]
    if not patterns:
        skipped["no plugin with a pathPattern"] += 1
    for key, pattern in patterns:
        placeholder = next((tag for tag in LANGUAGE if tag in pattern), None)
        relative = join(root, pattern)
        if placeholder is None or relative is None or not relative.endswith(".json"):
            skipped["pathPattern without a language or not JSON"] += 1
            continue
        relative = relative.replace("{namespace}", "*")
        mask = relative.replace(placeholder, "{lang}", 1).replace(placeholder, "*")
        name = posixpath.basename(relative.split(placeholder)[0].rstrip("/")) or "messages"
        found.append({"name": name, "mask": mask, "format": "json",
                      "template": relative.replace(placeholder, source), "source": None,
                      "languages": {}})
    return found, skipped


def repository(url):
    """repositories.read() of url's inlang settings."""
    return read_configs(url, CONFIG)


def blocks(repositories, prefix, covered=(), names=None, pause=1, read=None, licences=None,
           mirrors=None, min_units=None):
    """(blocks, skipped) for repositories' inlang projects; see
    repositories.blocks() for the arguments and what skipped counts. Units
    aren't counted (min_units)."""
    def resolve(url):
        read_back = (read or repository)(url)
        if read_back is None:
            return None
        paths, texts = read_back
        if not texts:
            return [], {("repositories", "no project.inlang/settings.json"): 1}
        skipped = collections.Counter()
        entries = []
        for config, text in sorted(texts.items()):
            if text is None:
                skipped["patterns", "unreadable settings.json"] += 1
                continue
            found, reasons = files(config, text)
            skipped.update({("patterns", reason): count for reason, count in reasons.items()})
            entries += found
        found, reasons = candidates(entries, paths, "patterns", "pathPattern matches nothing")
        return found, skipped + reasons

    return repository_blocks(repositories, prefix, resolve, "patterns", covered, names, pause,
                             licences, mirrors)
