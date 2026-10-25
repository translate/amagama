"""The sources file: which blocks of translations to fetch, and from where."""

import datetime
import hashlib
import json
import os
import shutil
import tomllib

from amagama.licences import normalise_licence
from amagama.updater import chromium
from amagama.updater.fetchers import FETCHERS, FetchError
from amagama.updater.filemask import Selection, compile_mask, fill, wildcards
from amagama.updater.formats import (CHROMIUM, check_csv_options, check_format,
                                     check_xliff_states, is_monolingual)
from amagama.updater.languages import is_android_locale, map_language

DEFAULT_SOURCES = os.path.join(os.path.dirname(__file__), "sources.toml")
STATE_FILE = "fetched.json"


class Block(object):

    def __init__(self, name, title, source, version=None, licence=None, files=None,
                 exclude=None, keep=None, languages=None, format=None, template=None,
                 exclude_ids=None, columns=None, header_row=None, xliff_states=None,
                 max_age_days=30, min_age_days=0, segment=False, **settings):
        self.name = name
        self.title = title
        self.version = version
        self.source = source
        self.licence = licence
        self.fetcher = FETCHERS[source](settings)
        for key, value in (("files", files), ("exclude", exclude), ("keep", keep)):
            if value is not None and (not isinstance(value, list) or not value
                                      or not all(isinstance(mask, str) for mask in value)):
                raise ValueError("%s must be a list of filemasks" % key)
        self.selection = Selection(files, exclude)
        # Files the import needs beside the translations (.grd parts), not imported.
        self.keep = [compile_mask(mask, allow_lang=False) for mask in keep or []]
        # Without a template, each .xtb's .grd gives its English and language.
        self.grd_listed = format == CHROMIUM and template is None
        if not (self.fetcher.gives_language or self.selection.gives_language or self.grd_listed):
            raise ValueError("files must give each file's language with {lang}")
        if languages is not None and (not isinstance(languages, dict) or not all(
                isinstance(value, str) for value in languages.values())):
            raise ValueError("languages must map codes to codes, or to \"\" to skip")
        self.languages = languages or {}
        if format is not None:
            check_format(format)
        self.format = format
        self.template = template
        if format and is_monolingual(format) and format != CHROMIUM and not template:
            raise ValueError("format %s needs a template: the English file" % format)
        if template is not None:
            if not format or not is_monolingual(format):
                raise ValueError("template is for monolingual formats only")
            if not isinstance(template, str) or "{lang}" in template:
                raise ValueError("template must be a path, without {lang}")
            compile_mask(template, allow_lang=False)
            if not files or any(wildcards(mask) != wildcards(template) for mask in files):
                raise ValueError("template must have the same wildcards as each of files")
        if self.fetcher.needs_template:
            self.fetcher.use_template(template)
        if exclude_ids is not None and (not isinstance(exclude_ids, list) or not all(
                isinstance(pattern, str) for pattern in exclude_ids)):
            raise ValueError("exclude_ids must be a list of patterns")
        self.exclude_ids = exclude_ids or []
        check_csv_options(format, columns, header_row)
        check_xliff_states(format, xliff_states)
        self.reader_options = {"columns": columns, "header_row": bool(header_row),
                               "xliff_states": xliff_states}
        if type(max_age_days) is not int or max_age_days < 0:
            raise ValueError("max_age_days must be a whole number of days")
        self.max_age_days = max_age_days
        if type(min_age_days) is not int or min_age_days < 0:
            raise ValueError("min_age_days must be a whole number of days")
        self.min_age_days = min_age_days
        if not isinstance(segment, bool):
            raise ValueError("segment must be true or false")
        self.segment = segment

    def template_for(self, path):
        """The English template path for a selected file, if the block pairs."""
        if not self.template:
            return None
        for mask in self.selection.masks:
            match = mask.match(path.replace("\\", "/"))
            if match:
                return fill(self.template, match)

    @property
    def display_title(self):
        """The English title, with the version if there is one."""
        return "%s %s" % (self.title, self.version) if self.version else self.title

    def directory(self, cache_dir):
        return os.path.join(cache_dir, self.name)

    def state(self, cache_dir):
        """What the last successful fetch recorded, or None."""
        try:
            with open(os.path.join(self.directory(cache_dir), STATE_FILE), encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def changed(self, cache_dir):
        """Whether the source may differ from the last fetch.

        A copy younger than min_age_days isn't checked at all. A source
        without a usable fingerprint counts as changed once its copy is
        max_age_days old; fetch() then tells whether it really did.
        """
        state = self.state(cache_dir)
        if state is None:
            return True
        fetched = datetime.datetime.fromisoformat(state["fetched"])
        age = datetime.datetime.now(datetime.timezone.utc) - fetched
        if age < self.rest(state):
            return False
        fingerprint = self.fetcher.fingerprint()
        if fingerprint is None:
            return age >= datetime.timedelta(days=self.max_age_days)
        return state["fingerprint"] != fingerprint

    def rest(self, state):
        """How long after a fetch the block isn't checked again: min_age_days.

        Blocks first fetched together would all come due on one day, so the
        first refetch comes after a fraction of min_age_days taken from a hash
        of the block's name; they stay that far apart.
        """
        days = datetime.timedelta(days=self.min_age_days)
        if state.get("fetches", 1) > 1:
            return days
        part = int(hashlib.sha256(self.name.encode("utf-8")).hexdigest()[:8], 16) / 0x100000000
        return days * part

    def fetch(self, cache_dir):
        """Fetch into the cache, replacing the last fetch only on success.

        Returns the state written: fingerprint, time and files with their
        target languages.
        """
        fingerprint = self.fetcher.fingerprint()
        final = self.directory(cache_dir)
        new = final + ".new"
        shutil.rmtree(new, ignore_errors=True)
        os.makedirs(new)
        try:
            files = []
            unknown = set()
            codes = {}
            fetched = self.fetcher.fetch(new)
            listed = None
            if self.grd_listed:
                listed = chromium.listed_translations(new, [
                    path.replace("\\", "/") for path, lang in fetched if path.endswith(".grd")])
                templates = {grd for grd, lang in listed.values()}
            else:
                templates = {self.template_for(path) for path, lang in fetched} - {None}
            # Templates (.pot) are never imported; GNOME repeats them for
            # every language. An English template is kept but not imported.
            for path, lang in fetched:
                if listed is not None:
                    lang = listed.get(path.replace("\\", "/"), (None, None))[1]
                    lang = lang and self.selection.select(path, lang)
                else:
                    lang = self.selection.select(path, lang)
                if self.format == "android" and lang and not is_android_locale(lang):
                    lang = None
                code = lang and map_language(lang, self.languages)
                if path in templates or any(mask.match(path.replace("\\", "/"))
                                            for mask in self.keep):
                    continue
                if code is None:
                    if lang and lang not in self.languages:
                        unknown.add(lang)
                    os.remove(os.path.join(new, path))
                else:
                    codes.setdefault(code, set()).add(lang)
                    if listed is not None:
                        files.append((path, code, listed[path.replace("\\", "/")][0]))
                    elif self.template:
                        files.append((path, code, self.template_for(path)))
                    else:
                        files.append((path, code))
            for dirpath, dirnames, filenames in os.walk(new, topdown=False):
                if dirpath != new and not os.listdir(dirpath):
                    os.rmdir(dirpath)
            if self.selection.masks and not files:
                raise FetchError("no file matches %s" % ", ".join(self.selection.mask_texts))
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
        content = hashlib.sha256()
        for entry in sorted(files):
            content.update(json.dumps(entry).encode("utf-8"))
            with open(os.path.join(new, entry[0]), "rb") as f:
                content.update(hashlib.sha256(f.read()).digest())
        previous = self.state(cache_dir)
        state = {
            "block": self.name,
            "fingerprint": fingerprint,
            "content": content.hexdigest(),
            "same_as_last": bool(previous) and previous.get("content") == content.hexdigest(),
            "fetches": (previous or {}).get("fetches", 0) + 1,
            "fetched": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "files": sorted(files),
            "unknown_languages": sorted(unknown),
            "languages": {code: sorted(sources) for code, sources in sorted(codes.items())},
            # Different codes normalised to one, not by the block's languages.
            "merged_languages": {code: sorted(sources) for code, sources in sorted(codes.items())
                                 if len(sources - set(self.languages)) > 1},
        }
        with open(os.path.join(new, STATE_FILE), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        old = final + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(final):
            os.rename(final, old)
        os.rename(new, final)
        shutil.rmtree(old, ignore_errors=True)
        return state


def _fill(value, version):
    """value with {version} replaced in every string, also inside lists and tables."""
    if isinstance(value, str):
        return value.replace("{version}", version)
    if isinstance(value, list):
        return [_fill(item, version) for item in value]
    if isinstance(value, dict):
        return {key: _fill(item, version) for key, item in value.items()}
    return value


def _expand(entry, where):
    """One entry per item of the block's versions, or the entry itself."""
    versions = entry.pop("versions", None)
    if versions is None:
        return [entry]
    if not isinstance(versions, list) or not versions:
        raise ValueError("%s: versions must be a list" % where)
    if "version" in entry:
        raise ValueError("%s: give version or versions, not both" % where)
    if "{version}" not in str(entry.get("name", "")):
        raise ValueError("%s: a name with versions must contain {version}" % where)
    expanded = []
    for item in versions:
        if isinstance(item, str):
            item = {"version": item}
        if not isinstance(item, dict) or not isinstance(item.get("version"), str) \
                or not item["version"].strip():
            raise ValueError("%s: each of versions must be a version, or a table with one"
                             % where)
        expanded.append(dict(_fill(entry, item["version"]), **_fill(item, item["version"])))
    return expanded


def _entries(path, seen):
    """(file, entry) for path's blocks, then those of the files it includes.

    A file's defaults apply to its own blocks, under each block's settings.
    """
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError("%s: %s" % (path, e))
    defaults = data.get("defaults", {})
    if not isinstance(defaults, dict) or "name" in defaults or "versions" in defaults:
        raise ValueError("%s: defaults must be a table of block settings, without name or versions"
                         % path)
    entries = []
    for i, entry in enumerate(data.get("block", []), 1):
        entries += [(path, expanded) for expanded in _expand(
            dict(defaults, **entry), "%s: block %s" % (path, entry.get("name") or i))]
    include = data.get("include", [])
    if not isinstance(include, list) or not all(isinstance(name, str) for name in include):
        raise ValueError("%s: include must be a list of files" % path)
    for name in include:
        included = os.path.realpath(os.path.join(os.path.dirname(path), name))
        if included in seen:
            raise ValueError("%s: %s is included twice" % (path, name))
        seen.add(included)
        entries += _entries(included, seen)
    return entries


def load_sources(path=None):
    """Read and check the sources file and those it includes; return their
    blocks, each file's in order before those of the files it includes."""
    path = path or DEFAULT_SOURCES
    blocks = []
    names = set()
    entries = _entries(path, {os.path.realpath(path)})
    for i, (path, entry) in enumerate(entries, 1):
        name = entry.pop("name", None)
        where = "%s: block %s" % (path, name or i)
        if not name:
            raise ValueError("%s: no name" % where)
        if name in names:
            raise ValueError("%s: duplicate name" % where)
        if "/" in name or name.startswith("."):
            raise ValueError("%s: name can't contain / or start with ." % where)
        names.add(name)
        title = entry.pop("title", None)
        if not isinstance(title, str) or not title.strip():
            raise ValueError("%s: no title" % where)
        version = entry.pop("version", None)
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise ValueError("%s: version must be a non-empty string" % where)
        source = entry.pop("source", None)
        if source not in FETCHERS:
            raise ValueError("%s: source must be one of %s" % (where, ", ".join(sorted(FETCHERS))))
        licence = entry.pop("licence", None)
        try:
            if licence is not None:
                licence = normalise_licence(licence)
            blocks.append(Block(name, title, source, version, licence, **entry))
        except ValueError as e:
            raise ValueError("%s: %s" % (where, e))
    return blocks
