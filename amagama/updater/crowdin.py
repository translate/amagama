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

from amagama.updater import repositories
from amagama.updater.fetchers import FetchError
from amagama.updater.filemask import compile_mask
from amagama.updater.formats import is_monolingual
from amagama.updater.generate import clone
from amagama.updater.languages import normalise_language
from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.repositories import candidates as repository_candidates

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
    "macosx": "strings",
    "arb": "arb",
    "chrome": "webextension",
    "resx": "resx",
}


# An .xml file that is Android's string resources, by its root element.
ANDROID_ROOT = re.compile(r"\A\ufeff?\s*(?:(?:<\?.*?\?>|<!--.*?-->|<!DOCTYPE[^>]*>)\s*)*"
                          r"<resources[\s/>]", re.DOTALL)


def _path(path):
    """A configuration's path, relative to its base path."""
    path = re.sub(r"/+", "/", str(path).strip().replace("\\", "/"))
    while path.startswith(("./", "/")):
        path = path[1:] if path.startswith("/") else path[2:]
    return path


def _format(source, given, xml=None):
    """amaGama's format for a file, or None if amaGama doesn't read it; xml
    gives the format of typeless .xml sources read from the repository."""
    if given:
        return TYPES.get(str(given).lower())
    return repositories.format_of(source) or (xml or {}).get(source)


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


def _entries(text, directory):
    """(base, [(entry, source)]) of a crowdin.yml, None for the base of one
    that can't be read; source is None for an entry without one."""
    try:
        # Every value as text: YAML 1.1 reads a language code no as false.
        config = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        config = None
    if not isinstance(config, dict) or not isinstance(config.get("files"), list):
        return None, []
    base = posixpath.normpath(posixpath.join(directory, _path(config.get("base_path") or ".")))
    base = "" if base == "." else base
    entries = []
    for entry in config["files"]:
        if not isinstance(entry, dict) or not entry.get("source") or not entry.get("translation"):
            entries.append((entry, None))
        else:
            entries.append((entry, posixpath.join(base, _path(entry["source"])) if base
                            else _path(entry["source"])))
    return base, entries


def typeless_xml(text, directory=""):
    """The .xml sources of a crowdin.yml that give no type and no format by name."""
    return [source for entry, source in _entries(text, directory)[1]
            if source and not entry.get("type") and source.lower().endswith(".xml")
            and not repositories.format_of(source)]


def files(text, directory="", xml=None):
    """(files, skipped) of a crowdin.yml in directory of the repository.

    Each file is a dict of the translations it reads (mask), its format,
    template (a monolingual source), source and language overrides;
    skipped counts files left out, by reason. xml maps typeless .xml sources
    to their format, read from the repository.
    """
    found, skipped = [], collections.Counter()
    base, entries = _entries(text, directory)
    if base is None:
        skipped["unreadable crowdin.yml"] += 1
        return found, skipped
    for entry, source in entries:
        if source is None:
            skipped["file without source or translation"] += 1
            continue
        file_format = _format(source, entry.get("type"), xml)
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
    return repositories.configs(paths, CONFIG)


def xml_format(directory, source, paths):
    """The format of a typeless .xml source, from the first file it matches in
    a clone: "android" for string resources, else None."""
    try:
        mask = compile_mask(source, allow_lang=False)
    except ValueError:
        return None
    path = next((path for path in paths if mask.match(path)), None)
    if path is None:
        return None
    try:
        text = repositories.read_file(directory, path)
    except (FetchError, UnicodeError):
        return None
    return "android" if ANDROID_ROOT.match(text) else None


def repository(url):
    """(paths, {config: text, or None}, {typeless .xml source: format}) of
    url's Crowdin configurations, or None if the repository isn't there."""
    with clone(url, None) as cloned:
        if cloned is None:
            return None
        directory, paths = cloned
        texts, xml = {}, {}
        for path in configs(paths):
            try:
                texts[path] = repositories.read_file(directory, path)
            except FetchError:
                texts[path] = None
                continue
            for source in typeless_xml(texts[path], posixpath.dirname(path)):
                if source not in xml:
                    xml[source] = xml_format(directory, source, paths)
        return paths, texts, xml


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
        paths, texts, *xml = read_back
        xml = xml[0] if xml else {}
        if not texts:
            return [], {("repositories", "no crowdin.yml"): 1}
        skipped = collections.Counter()
        entries = []
        for config, text in sorted(texts.items()):
            if text is None:
                skipped["files", "unreadable crowdin.yml"] += 1
                continue
            found_files, reasons = files(text, posixpath.dirname(config), xml)
            skipped.update({("files", reason): count for reason, count in reasons.items()})
            entries += found_files
        found, reasons = repository_candidates(entries, paths, "files",
                                               "translation matches nothing")
        return found, skipped + reasons

    return repository_blocks(repositories, prefix, resolve, "files", covered, names, pause,
                             licences, mirrors)
