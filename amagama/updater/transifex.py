#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' Transifex configurations, for review.

A repository translated on Transifex commits its client's .tx/config, which
gives each resource's source file, file type, source language and where its
translations go (file_filter, with <lang> for the language). Transifex needs
a login, so the translations are read from the repository, and its languages
are the files there.
"""

import collections
import configparser
import posixpath
import re

from amagama.updater import repositories
from amagama.updater.formats import is_monolingual
from amagama.updater.languages import normalise_language
from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.repositories import candidates as repository_candidates
from amagama.updater.weblate import FORMATS

# Transifex file types amaGama reads, as the Weblate format each one is.
TYPES = {
    "PO": "po",
    "QT": "ts",
    "XLIFF": "xliff",
    "ANDROID": "aresource",
    "PROPERTIES": "properties",
    "UNICODEPROPERTIES": "properties-utf8",
    "KEYVALUEJSON": "json",
    "PHP_ARRAY": "php",
    "PHP_ALT_ARRAY": "php",
}

CONFIG = re.compile(r"(?:^|/)\.tx/config\Z")


def _lang_map(text):
    """{code in the paths: Transifex's code} from a lang_map setting."""
    mapping = {}
    for pair in (text or "").split(","):
        remote, _, local = pair.partition(":")
        if remote.strip() and local.strip():
            mapping[local.strip()] = remote.strip()
    return mapping


def _path(directory, path):
    """A configuration's path, relative to the repository."""
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return posixpath.join(directory, path) if directory else path


def _resource(section):
    """A section's resource slug: [o:org:p:project:r:resource] or [project.resource]."""
    parts = section.split(":")
    if len(parts) >= 6 and parts[-2] == "r":
        return parts[-1]
    return section.split(".", 1)[-1]


def resources(text, directory=""):
    """(resources, skipped) of a .tx/config in directory of the repository.

    Each resource is a dict of the files it reads (mask), its Weblate format,
    template, source file and language overrides; skipped counts resources
    left out, by reason.
    """
    found, skipped = [], {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        skip("unreadable .tx/config")
        return found, skipped
    main = parser["main"] if parser.has_section("main") else {}
    for section in parser.sections():
        if section == "main":
            continue
        settings = parser[section]

        def setting(key):
            return (settings.get(key) or main.get(key) or "").strip()

        file_filter = setting("file_filter")
        file_type = setting("type").upper()
        if "<lang>" not in file_filter:
            skip("file_filter without <lang>")
        elif not repositories.is_english(setting("source_lang")):
            skip("source not English")
        elif file_type not in TYPES:
            skip("type %s" % (file_type or "not given"))
        else:
            weblate_format = TYPES[file_type]
            source = _path(directory, setting("source_file")) if setting("source_file") else None
            monolingual = is_monolingual(FORMATS[weblate_format] or "po")
            if monolingual and not source:
                skip("monolingual without a source_file")
                continue
            # The first <lang> is the language; any other is the same code.
            mask = _path(directory, file_filter.replace("<lang>", "{lang}", 1).replace("<lang>", "*"))
            mapping = dict(_lang_map(main.get("lang_map")), **_lang_map(settings.get("lang_map")))
            # Only the codes that wouldn't otherwise be the language Transifex means.
            languages = {local: normalise_language(remote) or ""
                         for local, remote in mapping.items()
                         if normalise_language(local) != normalise_language(remote)}
            found.append({"resource": _resource(section), "mask": mask, "format": weblate_format,
                          "template": source if monolingual else None,
                          "source": None if monolingual else source, "languages": languages})
    return found, skipped


def configs(paths):
    """The .tx/config files among a repository's paths, but vendored copies."""
    return repositories.configs(paths, CONFIG)


def repository(url):
    """repositories.read() of url's .tx/config files."""
    return repositories.read(url, CONFIG)


def blocks(repositories, prefix, covered=(), names=None, pause=1, read=None, licences=None,
           mirrors=None, min_units=None):
    """(blocks, skipped) for repositories' Transifex resources; see
    repositories.blocks() for the arguments and what skipped counts.

    A repository's resources of one format and language overrides share a
    block, but a monolingual resource has its own, for its template, named
    by its resource. Masks matching no file in the repository are dropped.
    Translated units aren't counted (min_units): a monolingual resource needs
    its template to count.
    """
    def resolve(url):
        read_back = (read or repository)(url)
        if read_back is None:
            return None
        paths, texts = read_back
        if not texts:
            return [], {("repositories", "no .tx/config"): 1}
        skipped = collections.Counter()
        entries = []
        for config, text in sorted(texts.items()):
            if text is None:
                skipped["resources", "unreadable .tx/config"] += 1
                continue
            found_resources, reasons = resources(text, posixpath.dirname(posixpath.dirname(config)))
            skipped.update({("resources", reason): count for reason, count in reasons.items()})
            entries += [dict(resource, name=resource["resource"],
                           format=FORMATS[resource["format"]] or "po")
                      for resource in found_resources]
        found, reasons = repository_candidates(entries, paths, "resources",
                                               "file_filter matches nothing")
        return found, skipped + reasons

    return repository_blocks(repositories, prefix, resolve, "resources", covered, names, pause,
                             licences, mirrors)
