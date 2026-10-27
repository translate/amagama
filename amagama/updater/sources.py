#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The sources file: which blocks of translations to fetch, and from where."""

import datetime
import hashlib
import json
import math
import os
import random
import shutil
import tomllib

from amagama.licences import normalise_licence
from amagama.source_options import MAX_WEIGHT
from amagama.updater import chromium, compressed
from amagama.updater.fetchers import FETCHERS, FetchError
from amagama.updater.filemask import Selection, compile_mask, fill, wildcards
from amagama.updater.formats import (CHROMIUM, check_csv_options, check_format,
                                     check_xliff_states, is_monolingual)
from amagama.updater.languages import is_android_locale, map_language

DEFAULT_SOURCES = os.path.join(os.path.dirname(__file__), "sources.toml")
STATE_FILE = "fetched.json"
# Beside a block's directory: its failed runs in a row.
FAILED_SUFFIX = ".failed"
# Beside a block's directory: since when its mirror has been behind.
MIRROR_SUFFIX = ".mirror"
REPOSITORIES = ".repos"
# This many failed runs in a row and a block is called out for attention.
FAILURES_TO_REPORT = 4
# Tags group blocks apart from the file they're in. A tier tells how a
# block was chosen: by hand, from a translation platform's listing, or by a
# search of repositories. A block without a content tag is documentation if
# it's segmented, else user interface.
TIERS = ("core", "platform", "discovered")
CONTENTS = ("ui", "docs", "terminology")
TAGS = TIERS + CONTENTS + ("trial",)


def sparse_patterns(files, template=None, keep=None):
    """Git sparse-checkout patterns for a block's files: {lang} as *."""
    return sorted({mask.replace("{lang}", "*") for mask in files}
                  | ({template} if template else set()) | set(keep or []))


class Block(object):

    def __init__(self, name, title, source, version=None, licence=None, files=None,
                 exclude=None, keep=None, languages=None, format=None, template=None,
                 exclude_ids=None, columns=None, header_row=None, xliff_states=None,
                 max_age_days=30, min_age_days=0, segment=False, weight=1, tags=None,
                 mirror_lag_days=14, language_in_ids=False, **settings):
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
        if source == "git" and "sparse" not in settings:
            # Clone only the files the block reads.
            # Without a template, the .grd files give each .xtb's language.
            self.fetcher.settings["sparse"] = sparse_patterns(
                files, template, (keep or []) + (["**/*.grd"] if self.grd_listed else []))
        if exclude_ids is not None and (not isinstance(exclude_ids, list) or not all(
                isinstance(pattern, str) for pattern in exclude_ids)):
            raise ValueError("exclude_ids must be a list of patterns")
        self.exclude_ids = exclude_ids or []
        if not isinstance(language_in_ids, bool):
            raise ValueError("language_in_ids must be true or false")
        if language_in_ids and not template:
            raise ValueError("language_in_ids is for blocks with a template")
        self.language_in_ids = language_in_ids
        check_csv_options(format, columns, header_row)
        check_xliff_states(format, xliff_states)
        self.reader_options = {"columns": columns, "header_row": header_row or False,
                               "xliff_states": xliff_states}
        if type(max_age_days) is not int or max_age_days < 0:
            raise ValueError("max_age_days must be a whole number of days")
        self.max_age_days = max_age_days
        if type(min_age_days) is not int or min_age_days < 0:
            raise ValueError("min_age_days must be a whole number of days")
        self.min_age_days = min_age_days
        if type(mirror_lag_days) is not int or mirror_lag_days < 0:
            raise ValueError("mirror_lag_days must be a whole number of days")
        self.mirror_lag_days = mirror_lag_days
        if not isinstance(segment, bool):
            raise ValueError("segment must be true or false")
        self.segment = segment
        if type(weight) not in (int, float) or not 0 <= weight <= MAX_WEIGHT:
            raise ValueError("weight must be a number from 0 to %d" % MAX_WEIGHT)
        self.weight = float(weight)
        self.tags = check_tags(tags if tags is not None else [], "tags")
        if len(self.tags & set(TIERS)) > 1:
            raise ValueError("tags can have one of %s" % ", ".join(TIERS))
        if not self.tags & set(CONTENTS):
            self.tags |= {"docs" if segment else "ui"}

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

    def failures(self, cache_dir):
        """The block's failures in a row: {"runs", "error", "since"}, or None."""
        try:
            with open(self.directory(cache_dir) + FAILED_SUFFIX, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def record_failure(self, cache_dir, error):
        """Count a failed run; returns the failures in a row so far."""
        failures = self.failures(cache_dir) or {
            "runs": 0, "since": datetime.datetime.now(datetime.timezone.utc).isoformat()}
        failures.update(runs=failures["runs"] + 1, error=str(error))
        os.makedirs(cache_dir, exist_ok=True)
        with open(self.directory(cache_dir) + FAILED_SUFFIX, "w", encoding="utf-8") as f:
            json.dump(failures, f, indent=1)
        return failures

    def clear_failures(self, cache_dir):
        try:
            os.remove(self.directory(cache_dir) + FAILED_SUFFIX)
        except FileNotFoundError:
            pass

    @property
    def mirror(self):
        return self.fetcher.settings.get("mirror") if self.source == "git" else None

    def mirror_state(self, cache_dir):
        """Since when the mirror has been behind without moving: {"since",
        "commit", "error"?}, or None."""
        try:
            with open(self.directory(cache_dir) + MIRROR_SUFFIX, encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return None

    def fingerprint(self, cache_dir):
        """The fetcher's fingerprint, choosing where a block with a mirror
        fetches from.

        The mirror takes load off the upstream's servers. It's used while it
        has the upstream's commit, or is behind but moved less than
        mirror_lag_days ago (a mirror syncing now and then is seldom level
        with a busy upstream); after that, or when it can't be reached, the
        upstream is.
        """
        if not self.mirror:
            return self.fetcher.fingerprint()
        url = self.fetcher.settings["url"]
        upstream = self.fetcher.commit(url)
        try:
            copy, error = self.fetcher.commit(self.mirror), None
        except FetchError as e:
            copy, error = None, str(e)
        path = self.directory(cache_dir) + MIRROR_SUFFIX
        if copy == upstream:
            if os.path.exists(path):
                os.remove(path)
            self.fetcher.source = self.mirror
            return copy
        now = datetime.datetime.now(datetime.timezone.utc)
        state = self.mirror_state(cache_dir) or {}
        if copy and state.get("commit") != copy or "since" not in state:
            state = {"since": now.isoformat(), "commit": copy}
        state.pop("error", None)
        if error:
            state["error"] = error
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        behind = now - datetime.datetime.fromisoformat(state["since"])
        if copy and behind < datetime.timedelta(days=self.mirror_lag_days):
            self.fetcher.source = self.mirror
            return copy
        self.fetcher.source = url
        return upstream

    def mirror_note(self, cache_dir):
        """Why the block was fetched from its upstream, not its mirror, or None."""
        state = self.mirror_state(cache_dir) if self.mirror else None
        if state is None:
            return None
        if state.get("error"):
            return "mirror unreachable, fetched from upstream: %s" % state["error"]
        since = datetime.datetime.fromisoformat(state["since"])
        behind = datetime.datetime.now(datetime.timezone.utc) - since
        if behind < datetime.timedelta(days=self.mirror_lag_days):
            return None
        note = "mirror %s behind and unchanged since %s, fetched from upstream" % (
            self.mirror, since.date())
        if behind >= datetime.timedelta(days=2 * max(self.mirror_lag_days, 1)):
            note += "; is it kept current?"
        return note

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
        if self.rotates:
            # A language comes due every max_age_days / languages; fetch()
            # takes the ones due. Without dates yet, a full fetch sets them.
            dates = state.get("language_fetched")
            return dates is None or age >= datetime.timedelta(
                days=self.max_age_days / max(state.get("language_count", len(dates)), 1))
        fingerprint = self.fingerprint(cache_dir)
        if fingerprint is None:
            return age >= datetime.timedelta(days=self.max_age_days)
        return state["fingerprint"] != fingerprint

    @property
    def rotates(self):
        """Whether each run refreshes only the languages that are due."""
        return self.fetcher.rotates and self.max_age_days > 0

    def due(self, previous, languages):
        """The languages to fetch this run, or None for all of them.

        Each language is refreshed within max_age_days: a run takes its share
        of them for the days since the last fetch (a day's share the first
        time), languages never fetched first, then those fetched longest ago.
        So a first fetch is spread over max_age_days too. Only for a block
        that rotates.
        """
        if previous is None:
            dates, days = {}, 1
        else:
            dates = previous.get("language_fetched")
            if dates is None:
                return None
            now = datetime.datetime.now(datetime.timezone.utc)
            # To the hour, so a run a few seconds late doesn't take an extra one.
            hours = round((now - datetime.datetime.fromisoformat(previous["fetched"]))
                          .total_seconds() / 3600)
            days = hours / 24
        share = math.ceil(len(languages) * days / self.max_age_days)
        new = [lang for lang in languages if lang not in dates]
        oldest = sorted((lang for lang in languages if lang in dates), key=lambda lang: dates[lang])
        due = (new + oldest)[:share]
        return due if len(due) < len(languages) else None

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
        previous = self.state(cache_dir)
        # A rotating block's change is found by fetching its due languages.
        fingerprint = None if self.rotates else self.fingerprint(cache_dir)
        available = self.fetcher.languages() if self.rotates else None
        due = self.due(previous, available) if self.rotates else None
        final = self.directory(cache_dir)
        new = final + ".new"
        shutil.rmtree(new, ignore_errors=True)
        os.makedirs(new)
        try:
            files = []
            unknown = set()
            codes = {}
            # Git keeps one repository per URL here, shared between blocks.
            self.fetcher.store = os.path.join(cache_dir, REPOSITORIES)
            fetched = self.fetcher.fetch(new) if due is None else self.fetcher.fetch(new, due)
            outside = _resolve_links(new)
            fetched = [(path, lang) for path, lang in fetched
                       if path.replace("\\", "/") not in outside]
            listed = None
            if self.grd_listed:
                listed = chromium.listed_translations(new, [
                    path.replace("\\", "/") for path, lang in fetched if path.endswith(".grd")])
                templates = {grd for grd, lang in listed.values()}
            else:
                templates = {self.template_for(path) for path, lang in fetched} - {None}
            # Templates (.pot) are never imported; GNOME repeats them for
            # every language. An English template is kept but not imported.
            origin = {}
            for path, lang in fetched:
                origin[path] = lang
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
            hashes = {}
            for entry in files:
                with open(os.path.join(new, entry[0]), "rb") as f:
                    hashes[entry[0]] = hashlib.sha256(f.read()).hexdigest()
            fresh = list(files)
            by_language = {}
            for entry in files:
                by_language.setdefault(origin[entry[0]], []).append(list(entry))
            if due is not None:
                # The languages not due keep their last fetch.
                for lang in set(available) - set(due):
                    for entry in (previous or {}).get("by_language", {}).get(lang, []):
                        _carry(final, new, entry, previous)
                        files.append(tuple(entry))
                        hashes[entry[0]] = previous["hashes"][entry[0]]
                        codes.setdefault(entry[1], set()).update(
                            previous["languages"].get(entry[1], []))
                        by_language.setdefault(lang, []).append(entry)
            # Only once every language has been tried: a run's few may lack them.
            tried = set(available or []) <= set((previous or {}).get("language_fetched", {}))
            if self.selection.masks and not files and (due is None or tried):
                raise FetchError("no file matches %s" % ", ".join(self.selection.mask_texts))
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
        content = hashlib.sha256()
        for entry in sorted(files):
            content.update(json.dumps(entry).encode("utf-8"))
            content.update(bytes.fromhex(hashes[entry[0]]))
        # The hash is of the files as fetched, so compressing changes nothing
        # for change detection. Templates stay as they are: Chromium's .grd
        # includes its .grdp files by path.
        try:
            last = (previous or {}).get("hashes", {})
            for entry in fresh:
                path = os.path.join(new, entry[0])
                kept = os.path.join(final, entry[0]) + compressed.SUFFIX
                if last.get(entry[0]) == hashes[entry[0]] and os.path.exists(kept):
                    # Unchanged since the last fetch: keep its compressed copy.
                    os.remove(path)
                    _link(kept, path + compressed.SUFFIX)
                else:
                    compressed.compress(path)
        except BaseException:
            shutil.rmtree(new, ignore_errors=True)
            raise
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        state = {
            "block": self.name,
            "fingerprint": fingerprint,
            "content": content.hexdigest(),
            "same_as_last": bool(previous) and previous.get("content") == content.hexdigest(),
            "fetches": (previous or {}).get("fetches", 0) + 1,
            "fetched": now,
            "files": sorted(files),
            "hashes": hashes,
            "unknown_languages": sorted(unknown),
            "outside_links": outside,
            "languages": {code: sorted(sources) for code, sources in sorted(codes.items())},
            # Different codes normalised to one, not by the block's languages.
            "merged_languages": {code: sorted(sources) for code, sources in sorted(codes.items())
                                 if len(sources - set(self.languages)) > 1},
        }
        if self.rotates:
            state["by_language"] = by_language
            dates = (previous or {}).get("language_fetched", {})
            done = set(available if due is None else due)
            state["language_fetched"] = {lang: now if lang in done else dates[lang]
                                         for lang in available if lang in done or lang in dates}
            state["language_count"] = len(available)
        with open(os.path.join(new, STATE_FILE), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        old = final + ".old"
        shutil.rmtree(old, ignore_errors=True)
        if os.path.exists(final):
            os.rename(final, old)
        os.rename(new, final)
        shutil.rmtree(old, ignore_errors=True)
        return state


def _carry(final, new, entry, previous):
    """Link a file of the last fetch (and its template) into the new one."""
    names = [entry[0] + compressed.SUFFIX]
    if len(entry) > 2 and entry[2]:
        names.append(entry[2])
    for name in names:
        source, target = os.path.join(final, name), os.path.join(new, name)
        if os.path.exists(target):
            continue
        if not os.path.exists(source):
            raise FetchError("%s is missing from the last fetch" % name)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        _link(source, target)


def _link(source, target):
    """Hard-link source to target, or copy it where links fail."""
    try:
        os.link(source, target)
    except OSError:
        shutil.copy2(source, target)


def _resolve_links(root):
    """Replace the file symlinks under root with copies of what they point to.

    Afterwards files can be removed or compressed in any order. A link
    leading outside root (a repository could link a "translation" to any
    file on the machine) or to nothing is removed; returns those, relative
    to root.
    """
    real_root = os.path.realpath(root)
    dropped = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in filenames:
            path = os.path.join(dirpath, name)
            if not os.path.islink(path):
                continue
            target = os.path.realpath(path)
            os.remove(path)
            if target.startswith(real_root + os.sep) and os.path.isfile(target):
                shutil.copyfile(target, path)
            else:
                dropped.append(os.path.relpath(path, root))
    return sorted(dropped)


def check_tags(tags, what):
    """tags as a set, if they're a list of known tags; else ValueError."""
    if not isinstance(tags, (list, tuple)) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError("%s must be a list of tags" % what)
    unknown = sorted(set(tags) - set(TAGS))
    if unknown:
        raise ValueError("%s: unknown %s; tags are %s" % (what, ", ".join(unknown), ", ".join(TAGS)))
    return set(tags)


def select(blocks, tags=None, exclude_tags=()):
    """The blocks with any of tags (None: every block) and none of exclude_tags."""
    tags = None if tags is None else check_tags(tags, "tags")
    exclude_tags = check_tags(exclude_tags, "exclude_tags")
    return [block for block in blocks
            if (tags is None or block.tags & tags) and not block.tags & exclude_tags]


def _with_defaults(defaults, entry):
    """entry's settings over defaults; tags add to the defaults' own."""
    merged = dict(defaults, **entry)
    if isinstance(defaults.get("tags"), list) and isinstance(entry.get("tags"), list):
        merged["tags"] = defaults["tags"] + [tag for tag in entry["tags"]
                                             if tag not in defaults["tags"]]
    return merged


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
            _with_defaults(defaults, entry), "%s: block %s" % (path, entry.get("name") or i))]
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


def load_sources(path=None, disabled=False):
    """Read and check the sources file and those it includes; return their
    blocks, each file's in order before those of the files it includes.

    A block with disabled (the reason) is checked and keeps its name, but is
    left out unless disabled.
    """
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
        reason = entry.pop("disabled", None)
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("%s: disabled must be the reason the block is left out" % where)
        try:
            if licence is not None:
                licence = normalise_licence(licence)
            block = Block(name, title, source, version, licence, **entry)
        except ValueError as e:
            raise ValueError("%s: %s" % (where, e))
        block.disabled, block.enabled, block.path = reason, reason is None, path
        if block.enabled or disabled:
            blocks.append(block)
    return blocks


def unused_repositories(blocks, cache_dir):
    """The shared git repositories in the cache that none of blocks fetches
    from; give every block of the sources file, disabled ones too."""
    store = os.path.join(cache_dir, REPOSITORIES)
    used = {block.fetcher.repository(store) for block in blocks if block.source == "git"}
    unused = []
    for dirpath, dirnames, filenames in os.walk(store):
        if dirpath.endswith(".git") and "HEAD" in filenames:
            dirnames[:] = []
            if dirpath not in used:
                unused.append(dirpath)
    return sorted(unused)


def limit_first_fetches(blocks, cache_dir, limit):
    """The blocks to run, with at most limit of them fetched for the first time.

    A new cache fills over several runs rather than in one burst. Each run
    picks its first fetches at random, so on average it takes a little from
    every upstream and spaces a project's releases out. A block whose first
    fetch failed is retried without counting. Returns (blocks, number left
    for later runs); None is no limit.
    """
    blocks = list(blocks)
    new = [block for block in blocks
           if block.source != "snapshot" and block.state(cache_dir) is None
           and block.failures(cache_dir) is None]
    if limit is None or len(new) <= limit:
        return blocks, 0
    chosen = random.sample(new, limit)
    return [block for block in blocks if block not in new] + chosen, len(new) - limit
