#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' Lingui configurations, for review.

A JavaScript project translated with Lingui commits lingui.config.js (or
.ts, .mjs, .cjs, .linguirc, or a "lingui" key in package.json), whose
catalogs give where each language's messages go, with {locale}. A JS or TS
configuration is read with patterns, never run: one whose catalog paths
aren't plain strings is counted and left out. Catalogs are PO unless the
configuration names a JSON format.
"""

import collections
import json
import posixpath
import re

from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.repositories import candidates, is_english, join
from amagama.updater.repositories import read as read_configs

# The root package.json only: a monorepo has hundreds.
CONFIG = re.compile(r"(?:^|/)(?:lingui\.config\.[cm]?[jt]s|\.linguirc)\Z|\Apackage\.json\Z")
# Lingui's catalog formats, as amaGama's (others, such as csv, it doesn't read).
FORMATS = {"po": "po", "po-gettext": "po", "minimal": "json", "lingui": "json", "json": "json"}
STRING = r"""(?:"([^"\n]*)"|'([^'\n]*)'|`([^`$\n]*)`)"""


def _strings(key, text):
    """The string literals given for key in a JS or TS configuration."""
    return [next(part for part in match.groups() if part is not None)
            for match in re.finditer(r"""["']?%s["']?\s*:\s*%s""" % (key, STRING), text)]


def settings(path, text):
    """(catalog paths, format, source locale) of a configuration, or None
    if it isn't Lingui's or can't be read."""
    if path.endswith((".linguirc", "package.json")):
        try:
            config = json.loads(text)
        except ValueError:
            return None
        if path.endswith("package.json"):
            config = config.get("lingui") if isinstance(config, dict) else None
        if not isinstance(config, dict):
            return None
        catalogs = [catalog.get("path") for catalog in config.get("catalogs") or []
                    if isinstance(catalog, dict) and isinstance(catalog.get("path"), str)]
        file_format = config.get("format") if isinstance(config.get("format"), str) else "po"
        file_format = FORMATS.get(file_format, file_format)
        source = config.get("sourceLocale") if isinstance(config.get("sourceLocale"), str) \
            else None
        return catalogs, file_format, source
    catalogs = _strings("path", text)
    if re.search(r"@lingui/format-json|format\s*:\s*[\"'](?:minimal|lingui)[\"']", text):
        file_format = "json"
    elif re.search(r"@lingui/format-csv|format\s*:\s*[\"']csv[\"']", text):
        file_format = "csv"
    else:
        file_format = "po"
    source = (_strings("sourceLocale", text) or [None])[0]
    return catalogs, file_format, source


def files(path, text):
    """(files, skipped) of a Lingui configuration at path in the repository:
    the dicts repositories.candidates() takes."""
    found, skipped = [], collections.Counter()
    read_back = settings(path, text)
    if read_back is None:
        return found, skipped
    catalogs, file_format, source = read_back
    catalogs = [catalog for catalog in catalogs if "{locale}" in catalog]
    if not catalogs:
        skipped["no catalog path with {locale} the patterns can read"] += 1
        return found, skipped
    if file_format not in set(FORMATS.values()):
        skipped["format %s" % file_format] += 1
        return found, skipped
    source = source or "en"
    if not is_english(source):
        skipped["source not English"] += 1
        return found, skipped
    extension = ".po" if file_format == "po" else ".json"
    for catalog in catalogs:
        catalog = join(posixpath.dirname(path), catalog.replace("<rootDir>", "."))
        if catalog is None:
            skipped["catalog outside the repository"] += 1
            continue
        catalog = catalog.replace("{name}", "*")
        mask = catalog.replace("{locale}", "{lang}", 1).replace("{locale}", "*") + extension
        english = catalog.replace("{locale}", source) + extension
        name = posixpath.basename(catalog.split("{locale}")[0].rstrip("/")) or "messages"
        found.append({"name": name, "mask": mask, "format": file_format,
                      "template": english if file_format == "json" else None,
                      "source": english if file_format == "po" else None, "languages": {}})
    return found, skipped


def repository(url):
    """repositories.read() of url's Lingui configurations."""
    return read_configs(url, CONFIG)


def blocks(repositories, prefix, covered=(), names=None, pause=1, read=None, licences=None,
           mirrors=None, min_units=None):
    """(blocks, skipped) for repositories' Lingui catalogs; see
    repositories.blocks() for the arguments and what skipped counts. Units
    aren't counted (min_units)."""
    def resolve(url):
        read_back = (read or repository)(url)
        if read_back is None:
            return None
        paths, texts = read_back
        skipped = collections.Counter()
        entries = []
        for config, text in sorted(texts.items()):
            if text is None:
                skipped["catalogs", "unreadable configuration"] += 1
                continue
            found, reasons = files(config, text)
            skipped.update({("catalogs", reason): count for reason, count in reasons.items()})
            entries += found
        if not entries and not skipped:
            return [], {("repositories", "no Lingui configuration"): 1}
        found, reasons = candidates(entries, paths, "catalogs", "path matches nothing")
        return found, skipped + reasons

    return repository_blocks(repositories, prefix, resolve, "catalogs", covered, names, pause,
                             licences, mirrors)
