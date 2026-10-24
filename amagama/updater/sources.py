"""The sources file: which blocks of translations to fetch, and from where."""

import datetime
import json
import os
import shutil
import tomllib

from amagama.licences import normalise_licence
from amagama.updater.fetchers import FETCHERS, FetchError
from amagama.updater.filemask import Selection
from amagama.updater.formats import check_format
from amagama.updater.languages import map_language

DEFAULT_SOURCES = os.path.join(os.path.dirname(__file__), "sources.toml")
STATE_FILE = "fetched.json"


class Block(object):

    def __init__(self, id, name, title, source, version=None, licence=None, files=None,
                 exclude=None, languages=None, format=None, **settings):
        self.id = id
        self.name = name
        self.title = title
        self.version = version
        self.source = source
        self.licence = licence
        self.fetcher = FETCHERS[source](settings)
        for key, value in (("files", files), ("exclude", exclude)):
            if value is not None and (not isinstance(value, list) or not value
                                      or not all(isinstance(mask, str) for mask in value)):
                raise ValueError("%s must be a list of filemasks" % key)
        self.selection = Selection(files, exclude)
        if not self.fetcher.gives_language and not self.selection.gives_language:
            raise ValueError("files must give each file's language with {lang}")
        if languages is not None and (not isinstance(languages, dict) or not all(
                isinstance(value, str) for value in languages.values())):
            raise ValueError("languages must map codes to codes, or to \"\" to skip")
        self.languages = languages or {}
        if format is not None:
            check_format(format)
        self.format = format

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
        """Whether the source differs from the last fetch.

        A source without a usable fingerprint always counts as changed.
        """
        state = self.state(cache_dir)
        fingerprint = self.fetcher.fingerprint()
        return fingerprint is None or state is None or state["fingerprint"] != fingerprint

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
            # Templates (.pot) are never imported; GNOME repeats them for
            # every language.
            for path, lang in self.fetcher.fetch(new):
                lang = self.selection.select(path, lang)
                code = lang and map_language(lang, self.languages)
                if code is None:
                    if lang and lang not in self.languages:
                        unknown.add(lang)
                    os.remove(os.path.join(new, path))
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
        state = {
            "block": self.name,
            "fingerprint": fingerprint,
            "fetched": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "files": sorted(files),
            "unknown_languages": sorted(unknown),
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


def load_sources(path=None):
    """Read and check the sources file; return its blocks in file order."""
    path = path or DEFAULT_SOURCES
    with open(path, "rb") as f:
        try:
            data = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError("%s: %s" % (path, e))
    blocks = []
    names = set()
    ids = set()
    for i, entry in enumerate(data.get("block", []), 1):
        entry = dict(entry)
        name = entry.pop("name", None)
        where = "%s: block %s" % (path, name or i)
        if not name:
            raise ValueError("%s: no name" % where)
        if name in names:
            raise ValueError("%s: duplicate name" % where)
        if "/" in name or name.startswith("."):
            raise ValueError("%s: name can't contain / or start with ." % where)
        names.add(name)
        block_id = entry.pop("id", None)
        if type(block_id) is not int or block_id < 1:
            raise ValueError("%s: id must be a positive integer" % where)
        if block_id in ids:
            raise ValueError("%s: duplicate id %d" % (where, block_id))
        ids.add(block_id)
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
            blocks.append(Block(block_id, name, title, source, version, licence, **entry))
        except ValueError as e:
            raise ValueError("%s: %s" % (where, e))
    return blocks
