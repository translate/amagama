#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' Crowdin configurations, for review.

A repository translated on Crowdin commits crowdin.yml, which gives each
source file and where its translations go, with placeholders for the
language. Crowdin keeps the language list on its server, behind a login, so
the languages are the files in the repository.
"""

import collections
import posixpath
import re

import yaml

from amagama.updater.fetchers import FetchError, run_tool
from amagama.updater.formats import is_monolingual
from amagama.updater.generate import clone, matches, matching
from amagama.updater.languages import normalise_language
from amagama.updater.repositories import VENDORED
from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.sources import sparse_patterns

CONFIG = re.compile(r"(?:^|/)\.?crowdin\.ya?ml\Z")

# Language placeholders, as their code appears in a path.
LANGUAGE = {
    "locale": "{lang}",
    "locale_with_underscore": "{lang}",
    "two_letters_code": "{lang}",
    "three_letters_code": "{lang}",
    "android_code": "{lang}",
    "osx_locale": "{lang}",
    "osx_code": "{lang}.lproj",
}

# Crowdin file types amaGama reads, as amaGama's format ("po" for none).
TYPES = {
    "gettext": "po",
    "qtts": "ts",
    "xliff": "xliff",
    "android": "android",
    "json": "json",
    "i18next_json": "json",
    "properties": "properties",
    "php": "php",
    "ftl": "fluent",
}
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
}


def _path(path):
    """A configuration's path, relative to its base path."""
    path = re.sub(r"/+", "/", str(path).strip().replace("\\", "/"))
    while path.startswith(("./", "/")):
        path = path[1:] if path.startswith("/") else path[2:]
    return path


def _format(source, given):
    """amaGama's format for a file, or None if amaGama doesn't read it."""
    if given:
        return TYPES.get(str(given).lower())
    extension = posixpath.splitext(source)[1].lower()
    if extension == ".xml" and re.search(r"(?:^|/)values[^/]*/[^/]+\.xml\Z", source):
        return "android"
    return EXTENSIONS.get(extension)


def mask(translation, source):
    """(filemask, placeholder) for a translation pattern of source, or (None,
    reason). The source's own placeholders become the matching part of its
    path, so a template and its translations have the same wildcards; the
    first language placeholder is the language, any other the same code."""
    name = posixpath.basename(source)
    stem, extension = posixpath.splitext(name)
    own = {"original_file_name": name, "file_name": stem,
           "file_extension": extension[1:], "original_path": posixpath.dirname(source)}
    language = None

    def replace(match):
        nonlocal language
        placeholder = match.group(1)
        if placeholder in own:
            return own[placeholder]
        if placeholder in LANGUAGE:
            if language is None:
                language = placeholder
                return LANGUAGE[placeholder]
            return "*"
        raise ValueError("placeholder %%%s%%" % placeholder)

    try:
        text = re.sub(r"%(\w+)%", replace, _path(translation))
    except ValueError as e:
        return None, str(e)
    if language is None:
        return None, "translation without a language"
    return posixpath.normpath(text).lstrip("/") if text else text, language


def _languages(mapping, placeholder):
    """{code in the paths: Crowdin's code} from a file's languages_mapping, for
    the codes that wouldn't otherwise be the language Crowdin means."""
    codes = (mapping or {}).get(placeholder) if isinstance(mapping, dict) else None
    found = {}
    for remote, local in (codes or {}).items() if isinstance(codes, dict) else ():
        local = str(local).removesuffix(".lproj") if placeholder == "osx_code" else str(local)
        if normalise_language(local) != normalise_language(str(remote)):
            found[local] = normalise_language(str(remote)) or ""
    return found


def files(text, directory=""):
    """(files, skipped) of a crowdin.yml in directory of the repository.

    Each file is a dict of the translations it reads (mask), its format,
    template (a monolingual source), source and language overrides;
    skipped counts files left out, by reason.
    """
    found, skipped = [], collections.Counter()
    try:
        # Every value as text: YAML 1.1 reads a language code no as false.
        config = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        config = None
    if not isinstance(config, dict) or not isinstance(config.get("files"), list):
        skipped["unreadable crowdin.yml"] += 1
        return found, skipped
    base = posixpath.normpath(posixpath.join(directory, _path(config.get("base_path") or ".")))
    base = "" if base == "." else base
    for entry in config["files"]:
        if not isinstance(entry, dict) or not entry.get("source") or not entry.get("translation"):
            skipped["file without source or translation"] += 1
            continue
        source = posixpath.join(base, _path(entry["source"])) if base else _path(entry["source"])
        file_format = _format(source, entry.get("type"))
        if file_format is None:
            skipped["type %s" % (entry.get("type") or posixpath.splitext(source)[1] or "unknown")] \
                += 1
            continue
        filemask, placeholder = mask(entry["translation"], _path(entry["source"]))
        if filemask is None:
            skipped[placeholder] += 1
            continue
        if base:
            filemask = posixpath.join(base, filemask)
        monolingual = is_monolingual(file_format)
        # A block is named by its file, or the directory of a file pattern.
        name = posixpath.splitext(posixpath.basename(source))[0].replace("*", "") or \
            posixpath.basename(posixpath.dirname(filemask.split("{lang}")[0]) or "files")
        found.append({"name": name,
                      "mask": filemask, "format": file_format,
                      "template": source if monolingual else None,
                      "source": None if monolingual else source,
                      "languages": _languages(entry.get("languages_mapping"), placeholder)})
    return found, skipped


def configs(paths):
    """The Crowdin configurations among a repository's paths, but vendored copies."""
    return [path for path in paths if CONFIG.search(path) and not VENDORED.search(path)]


def _read(directory, path):
    return run_tool("git", "-C", directory, "show", "HEAD:" + path, trusted=directory)


def repository(url):
    """(paths, {crowdin.yml path: text, or None if unreadable}) of url's
    default branch, or None if the repository isn't there."""
    with clone(url, None) as cloned:
        if cloned is None:
            return None
        directory, paths = cloned
        texts = {}
        for path in configs(paths):
            try:
                texts[path] = _read(directory, path)
            except FetchError:
                texts[path] = None
        return paths, texts


def blocks(repositories, prefix, covered=(), names=None, pause=1, read=None, licences=None,
           mirrors=None, min_units=None):
    """(blocks, skipped) for repositories' Crowdin files; see
    repositories.blocks() for the arguments and what skipped counts.

    A repository's bilingual files of one format and language overrides
    share a block, but a monolingual file has its own, for its template,
    named by it. Masks matching no file in the repository are dropped.
    Translated units aren't counted (min_units): a monolingual file needs its
    template to count.
    """
    def resolve(url):
        read_back = (read or repository)(url)
        if read_back is None:
            return None
        paths, texts = read_back
        if not texts:
            return [], {("repositories", "no crowdin.yml"): 1}
        skipped = collections.Counter()
        groups = {}
        for config, text in sorted(texts.items()):
            if text is None:
                skipped["files", "unreadable crowdin.yml"] += 1
                continue
            found_files, reasons = files(text, posixpath.dirname(config))
            skipped.update({("files", reason): count for reason, count in reasons.items()})
            for found in found_files:
                key = (found["format"], found["template"] or "",
                       tuple(sorted(found["languages"].items())))
                group = groups.setdefault(key, dict(found, masks=set(), sources=set()))
                group["masks"].add(found["mask"])
                if found["source"]:
                    group["sources"].add(found["source"])
        candidates = []
        # The repository's shared block first, then a block per template.
        for (file_format, template, languages), group in sorted(
                groups.items(), key=lambda item: (bool(item[0][1]), item[0])):
            template = template or None
            found_masks = matching(group["masks"], paths, template)
            if not found_masks:
                skipped["files", "translation matches nothing"] += 1
                continue
            candidate = {"suffix": group["name"] if template else None, "files": found_masks}
            # A source file the pattern also matches isn't a translation.
            exclude = sorted(source for source in group["sources"]
                             if any(matches(filemask, source) for filemask in found_masks))
            if exclude:
                candidate["exclude"] = exclude
            candidate["sparse"] = sparse_patterns(found_masks, template)
            if file_format != "po":
                candidate["format"] = file_format
            if template:
                candidate["template"] = template
            if languages:
                candidate["languages"] = dict(languages)
            candidates.append(candidate)
        return candidates, skipped

    return repository_blocks(repositories, prefix, resolve, "files", covered, names, pause,
                             licences, mirrors)
