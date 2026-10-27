#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Fetch translation files from where projects publish them.

Each fetcher downloads one block's files into a directory and reports their
target languages, and computes a fingerprint cheaply enough to check weekly.
"""

import fnmatch
import gzip
import hashlib
import json
import logging
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from translate.storage import po

from amagama import __version__

USER_AGENT = "amaGama-updater/%s (+https://github.com/translate/amagama)" % __version__
TIMEOUT = 120


class FetchError(Exception):

    def __init__(self, message, status=None):
        super(FetchError, self).__init__(message)
        self.status = status


def _request(url, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except (urllib.error.URLError, OSError) as e:
        raise FetchError("%s: %s" % (url, e), getattr(e, "code", None))


def http_text(url):
    with _request(url) as response:
        return response.read().decode("utf-8")


def http_fingerprint(url):
    """ETag or Last-Modified, with the length; None if the server gives neither."""
    with _request(url, "HEAD") as response:
        headers = response.headers
        tag = headers.get("ETag") or headers.get("Last-Modified")
        if not tag:
            return None
        return "%s %s" % (tag, headers.get("Content-Length", ""))


def download(url, path):
    with _request(url) as response, open(path, "wb") as f:
        shutil.copyfileobj(response, f)


def extract_tarball(path, dest):
    try:
        with tarfile.open(path) as tar:
            tar.extractall(dest, filter="data")
    except (tarfile.TarError, OSError) as e:
        raise FetchError("%s: %s" % (path, e))


def extract_zip(path, dest):
    """zipfile leaves out absolute paths and .. parts."""
    try:
        with zipfile.ZipFile(path) as archive:
            archive.extractall(dest)
    except (zipfile.BadZipFile, OSError) as e:
        raise FetchError("%s: %s" % (path, e))


def combine(fingerprints):
    """One fingerprint from (name, fingerprint) pairs; None if any is None."""
    if any(fp is None for _, fp in fingerprints):
        return None
    digest = hashlib.sha256()
    for name, fp in sorted(fingerprints):
        digest.update(("%s\t%s\n" % (name, fp)).encode("utf-8"))
    return digest.hexdigest()


def files_under(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for filename in sorted(filenames):
            yield os.path.relpath(os.path.join(dirpath, filename), root)


class Fetcher(object):
    """Settings come from the block's entry in the sources file."""

    required = ()
    optional = ()
    # Whether fetch() knows each file's language without a {lang} filemask.
    gives_language = True
    # Whether fetch() also downloads the block's English, the files its
    # template matches; the block then calls use_template().
    needs_template = False

    def __init__(self, settings):
        missing = [key for key in self.required if key not in settings]
        if missing:
            raise ValueError("missing %s" % ", ".join(missing))
        unknown = set(settings) - set(self.required) - set(self.optional)
        if unknown:
            raise ValueError("unknown %s" % ", ".join(sorted(unknown)))
        self.settings = settings

    def fingerprint(self):
        raise NotImplementedError

    # Whether languages() lists the languages fetch() can take one at a time,
    # so a block can refresh a share of them per run.
    rotates = False

    def fetch(self, dest, languages=None):
        """Download into dest; return [(path relative to dest, language)].

        With languages (when rotates), only those languages.
        """
        raise NotImplementedError


class TarballPerLanguage(Fetcher):
    """One tarball per language (and per kind, if the site splits them).

    Downloads are pause seconds apart.
    """

    # Whether each tarball already has its files under <lang>/.
    has_lang_dir = False
    rotates = True

    def languages(self):
        raise NotImplementedError

    def urls(self, lang):
        raise NotImplementedError

    def fingerprint(self):
        return combine([(url, http_fingerprint(url))
                        for lang in self.languages() for url in self.urls(lang)])

    def fetch(self, dest, languages=None):
        files = []
        with tempfile.TemporaryDirectory() as tmp:
            for lang in self.languages():
                if languages is not None and lang not in languages:
                    continue
                lang_dir = os.path.join(dest, lang)
                for url in self.urls(lang):
                    path = os.path.join(tmp, "download")
                    download(url, path)
                    extract_tarball(path, dest if self.has_lang_dir else lang_dir)
                    time.sleep(self.settings.get("pause", 1))
                if os.path.isdir(lang_dir):
                    files.extend((os.path.join(lang, f), lang) for f in files_under(lang_dir))
        return files


class DamnedLies(TarballPerLanguage):
    """GNOME's l10n.gnome.org: per-language tarballs of a release.

    The tarballs are rebuilt daily, so the fingerprint is the release's
    statistics instead: one request, changing when any language's counts do.
    """

    required = ("release",)
    optional = ("tarballs", "base_url", "pause")

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://l10n.gnome.org").rstrip("/")

    def statistics(self):
        if not hasattr(self, "_statistics"):
            url = "%s/api/v1/releases/%s/stats" % (self.base_url, self.settings["release"])
            try:
                self._statistics = json.loads(http_text(url))["statistics"]
            except (ValueError, KeyError) as e:
                raise FetchError("%s: %s" % (url, e))
        return self._statistics

    def languages(self):
        """Languages with something translated in the tarballs the block takes."""
        kinds = {"doc" if tarball == "doc" else "ui" for tarball in self.settings.get("tarballs", ["ui"])}
        return sorted(lang["lang_locale"] for lang in self.statistics()
                      if any(lang.get(kind, {}).get("translated") for kind in kinds))

    def fingerprint(self):
        stats = json.dumps(sorted(self.statistics(), key=lambda lang: lang["lang_locale"]),
                           sort_keys=True)
        return hashlib.sha256(stats.encode("utf-8")).hexdigest()

    def urls(self, lang):
        return ["%s/languages/%s/%s/%s.tar.gz" % (self.base_url, lang, self.settings["release"], tarball)
                for tarball in self.settings.get("tarballs", ["ui"])]


class TranslationProject(TarballPerLanguage):
    """The Translation Project's monthly per-language backups."""

    optional = ("base_url", "pause")

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://translationproject.org/backups/last/").rstrip("/") + "/"

    def languages(self):
        if not hasattr(self, "_languages"):
            listing = http_text(self.base_url)
            self._languages = sorted({urllib.parse.unquote(name) for name in
                                      re.findall(r'href="([^"/]+)\.tgz"', listing)})
        return self._languages

    has_lang_dir = True

    def urls(self, lang):
        return [self.base_url + urllib.parse.quote(lang) + ".tgz"]


def run_tool(tool, *args, trusted=None):
    """A version-control command's output, run outside any repository.

    The caller's GIT_DIR and friends are left out. trusted is a git
    repository of the updater's own (a clone in the cache), or a list of
    them, which git otherwise refuses as "dubious ownership" when another
    user owns the cache (a mounted volume); it is set in the environment so
    the git processes git starts get it too.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    # A repository that isn't there fails rather than asking for a login.
    env["GIT_TERMINAL_PROMPT"] = "0"
    if trusted is not None:
        paths = [trusted] if isinstance(trusted, str) else list(trusted)
        env["GIT_CONFIG_COUNT"] = str(len(paths))
        for n, path in enumerate(paths):
            env["GIT_CONFIG_KEY_%d" % n] = "safe.directory"
            env["GIT_CONFIG_VALUE_%d" % n] = os.path.realpath(path)
    try:
        return subprocess.run((tool,) + args, check=True, capture_output=True, text=True,
                              timeout=3600, cwd=tempfile.gettempdir(), env=env).stdout
    except (subprocess.SubprocessError, OSError) as e:
        stderr = getattr(e, "stderr", None) or ""
        raise FetchError("%s %s: %s %s" % (tool, args[0], e, stderr.strip()))


class HttpPerLanguage(Fetcher):
    """One file per language, in the directories of a listing (Debian's compendia).

    path is the file under base_url, with {lang}. Where it is missing (403 or
    404), the last file in the language's directory matching fallback (a
    filemask with {lang} and *) is used instead; a language with neither is
    skipped. Downloads are pause seconds apart.
    """

    required = ("base_url", "path")
    optional = ("fallback", "pause")
    rotates = True

    def __init__(self, settings):
        super(HttpPerLanguage, self).__init__(settings)
        for key in ("path", "fallback"):
            if key in settings and "{lang}" not in settings[key]:
                raise ValueError("%s must contain {lang}" % key)

    @property
    def base_url(self):
        return self.settings["base_url"].rstrip("/") + "/"

    @staticmethod
    def _listed(url, directories=False):
        pattern = r'href="([^"/?#.][^"/?#]*)/"' if directories else r'href="([^"/?#]+)"'
        return sorted({urllib.parse.unquote(name) for name in re.findall(pattern, http_text(url))})

    def languages(self):
        if not hasattr(self, "_languages"):
            self._languages = self._listed(self.base_url, directories=True)
        return self._languages

    def _url(self, path, lang):
        return self.base_url + urllib.parse.quote(path.replace("{lang}", lang))

    def url(self, lang):
        return self._url(self.settings["path"], lang)

    def fallback_url(self, lang):
        """The last listed file matching fallback, or None."""
        if "fallback" not in self.settings:
            return None
        mask = self.settings["fallback"].replace("{lang}", lang)
        directory = posixpath.dirname(mask)
        names = [name for name in self._listed(self._url(directory + "/", lang))
                 if fnmatch.fnmatchcase(name, posixpath.basename(mask))]
        return self._url(posixpath.join(directory, names[-1]), lang) if names else None

    def _get(self, lang, action):
        """action(url) for path, else for fallback; None if neither exists."""
        try:
            return action(self.url(lang))
        except FetchError as e:
            if e.status not in (403, 404):
                raise
        url = self.fallback_url(lang)
        return action(url) if url else None

    def fingerprint(self):
        return combine([(lang, self._get(lang, http_fingerprint) or "missing")
                        for lang in self.languages()])

    def fetch(self, dest, languages=None):
        files = []
        for lang in self.languages():
            if languages is not None and lang not in languages:
                continue

            def save(url):
                path = posixpath.join(lang, posixpath.basename(urllib.parse.unquote(url)))
                os.makedirs(os.path.join(dest, lang), exist_ok=True)
                try:
                    download(url, os.path.join(dest, path))
                except FetchError:
                    if not os.listdir(os.path.join(dest, lang)):
                        os.rmdir(os.path.join(dest, lang))
                    raise
                return path
            path = self._get(lang, save)
            if path:
                files.append((path, lang))
            time.sleep(self.settings.get("pause", 1))
        return files


class Pontoon(Fetcher):
    """Mozilla's Pontoon: each locale's whole translation memory as TMX.

    Pontoon keeps a translation in its memory once it is approved. Locales
    with fewer than min_approved approved strings are skipped. The files are
    generated on request, with no fingerprint. Downloads are pause seconds
    apart.
    """

    optional = ("base_url", "min_approved", "pause")
    rotates = True

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://pontoon.mozilla.org").rstrip("/")

    def languages(self):
        if not hasattr(self, "_languages"):
            codes = []
            url = "%s/api/v2/locales/?page_size=500" % self.base_url
            while url:
                try:
                    page = json.loads(http_text(url))
                    codes += [locale["code"] for locale in page["results"]
                              if locale["approved_strings"] >= self.settings.get("min_approved", 1)]
                except (ValueError, KeyError, TypeError) as e:
                    raise FetchError("%s: %s" % (url, e))
                url = page.get("next")
            self._languages = sorted(codes)
        return self._languages

    def fingerprint(self):
        return None

    def fetch(self, dest, languages=None):
        files = []
        for lang in self.languages():
            if languages is not None and lang not in languages:
                continue
            path = posixpath.join(lang, "%s.all-projects.tmx" % lang)
            os.makedirs(os.path.join(dest, lang))
            download("%s/translation-memory/%s.all-projects.tmx"
                     % (self.base_url, urllib.parse.quote(lang)), os.path.join(dest, path))
            files.append((path, lang))
            time.sleep(self.settings.get("pause", 1))
        return files


# Gitiles hosts give a ref's commit as JSON; ls-remote on chromium/src
# lists millions of refs, whatever the pattern.
GITILES_HOSTS = (".googlesource.com",)


class Git(Fetcher):
    """A shallow clone of one branch or tag; files gives each file's language.

    With sparse, a list of gitignore-style patterns, only matching files
    are downloaded and checked out. With store set (by the block), the clone
    is one partial repository per URL kept there and shared by the blocks
    that fetch from it: a refetch downloads only what changed since. A
    mirror is a copy of url the block may fetch from instead (see
    Block.mirror_lag_days); the repository is still url's.
    """

    # Where the shared repositories are kept; None makes a throwaway clone.
    store = None
    # The URL to fetch from: url, or the mirror (set by the block).
    source = None

    required = ("url",)
    optional = ("ref", "sparse", "mirror")
    gives_language = False

    def __init__(self, settings):
        super(Git, self).__init__(settings)
        sparse = settings.get("sparse")
        if sparse is not None and (not isinstance(sparse, list) or not sparse or not all(
                isinstance(pattern, str) for pattern in sparse)):
            raise ValueError("sparse must be a list of patterns")
        mirror = settings.get("mirror")
        if mirror is not None and (not isinstance(mirror, str) or "://" not in mirror
                                   or mirror == settings["url"]):
            raise ValueError("mirror must be the URL of a copy of url")

    def _git(self, *args, clone=None):
        return run_tool("git", *args, trusted=clone)

    def fingerprint(self):
        return self.commit(self.settings["url"])

    def commit(self, url):
        """The commit of the block's ref at url."""
        ref = self.settings.get("ref", "HEAD")
        host = urllib.parse.urlsplit(url).hostname or ""
        if host.endswith(GITILES_HOSTS):
            return self._gitiles_commit(url, ref)
        lines = self._git("ls-remote", url, ref).split()
        if not lines:
            raise FetchError("%s: no ref %s" % (url, ref))
        return lines[0]

    def _gitiles_commit(self, url, ref):
        url = "%s/+/%s?format=JSON" % (url.rstrip("/"), urllib.parse.quote(ref))
        try:
            # Gitiles prefixes its JSON with )]}' to stop it being run as script.
            return json.loads(http_text(url).split("\n", 1)[1])["commit"]
        except (IndexError, ValueError, KeyError, TypeError) as e:
            raise FetchError("%s: %s" % (url, e))

    def repository(self):
        """The shared repository for the URL: store/host/path.git."""
        parts = urllib.parse.urlsplit(self.settings["url"])
        names = [parts.hostname or "local"] + [name for name in parts.path.split("/") if name]
        names = [re.sub(r"[^A-Za-z0-9._-]", "_", name).lstrip(".") or "_" for name in names]
        path = os.path.join(self.store, *names)
        return path if path.endswith(".git") else path + ".git"

    def fetch(self, dest):
        if self.store is None:
            return self._clone(dest)
        repo = self.repository()
        url = self.source or self.settings["url"]
        ref = self.settings.get("ref", "HEAD")
        trusted = (repo, dest)
        if not os.path.isfile(os.path.join(repo, "HEAD")):
            shutil.rmtree(repo, ignore_errors=True)
            os.makedirs(repo)
            self._git("init", "--quiet", "--bare", repo, clone=trusted)
            self._git("-C", repo, "remote", "add", "origin", url, clone=trusted)
            # Blobs come when a checkout needs them, from origin.
            self._git("-C", repo, "config", "remote.origin.promisor", "true", clone=trusted)
            self._git("-C", repo, "config", "remote.origin.partialclonefilter", "blob:none",
                      clone=trusted)
        self._git("-C", repo, "remote", "set-url", "origin", url, clone=trusted)
        self._git("-C", repo, "worktree", "prune", clone=trusted)
        # Kept as a local ref, so the next fetch sends only what's new.
        local = "refs/fetched/" + ref
        self._git("-C", repo, "fetch", "--quiet", "--depth", "1", "--filter=blob:none",
                  "origin", "+%s:%s" % (ref, local), clone=trusted)
        self._git("-C", repo, "worktree", "add", "--quiet", "--no-checkout", "--detach",
                  dest, local, clone=trusted)
        try:
            sparse = self.settings.get("sparse")
            if sparse:
                self._git("-C", dest, "sparse-checkout", "set", "--no-cone", *sparse,
                          clone=trusted)
            self._git("-C", dest, "checkout", "--quiet", clone=trusted)
        finally:
            # A plain directory again; the repository forgets the worktree.
            if os.path.exists(os.path.join(dest, ".git")):
                os.remove(os.path.join(dest, ".git"))
            self._git("-C", repo, "worktree", "prune", clone=trusted)
        self._git("-C", repo, "gc", "--auto", "--quiet", clone=trusted)
        return [(path, None) for path in files_under(dest)]

    def _clone(self, dest):
        args = ["clone", "--quiet", "--depth", "1"]
        if "ref" in self.settings:
            args += ["--branch", self.settings["ref"]]
        sparse = self.settings.get("sparse")
        if sparse:
            args += ["--filter=blob:none", "--sparse", "--no-checkout"]
        self._git(*(args + [self.source or self.settings["url"], dest]), clone=dest)
        if sparse:
            self._git("-C", dest, "sparse-checkout", "set", "--no-cone", *sparse, clone=dest)
            self._git("-C", dest, "checkout", "--quiet", clone=dest)
        shutil.rmtree(os.path.join(dest, ".git"))
        return [(path, None) for path in files_under(dest)]


class Svn(Fetcher):
    """A Subversion tree with one directory per language (KDE's l10n-kf6).

    dirs lists what to export from each language's directory (default: all
    of it). Exports are paced, pause seconds apart, to go easy on the server.
    """

    required = ("url",)
    optional = ("dirs", "pause")
    rotates = True

    @property
    def url(self):
        return self.settings["url"].rstrip("/")

    def languages(self):
        """The top-level directories; files there (KDE's teamnames) aren't languages."""
        if not hasattr(self, "_languages"):
            self._languages = sorted(name[:-1] for name in run_tool("svn", "ls", self.url).split()
                                     if name.endswith("/"))
        return self._languages

    def fingerprint(self):
        return run_tool("svn", "info", "--show-item", "last-changed-revision", self.url).strip()

    def fetch(self, dest, languages=None):
        files = []
        for lang in self.languages():
            if languages is not None and lang not in languages:
                continue
            for directory in self.settings.get("dirs", [""]):
                target = os.path.join(dest, lang, directory)
                os.makedirs(os.path.dirname(target.rstrip(os.sep)), exist_ok=True)
                url = "/".join(part for part in (self.url, lang, directory) if part)
                try:
                    run_tool("svn", "export", "--quiet", "--force", url, target)
                except FetchError as e:
                    # Not every language has every directory.
                    if "E170000" not in str(e) and "doesn't exist" not in str(e):
                        raise
                time.sleep(self.settings.get("pause", 1))
            if os.path.isdir(os.path.join(dest, lang)):
                files.extend((os.path.join(lang, path), lang)
                             for path in files_under(os.path.join(dest, lang)))
        return files


class WordPress(Fetcher):
    """WordPress.org's language packs: a zip per language of core, or of each
    plugin or theme in slugs.

    For core, release names a WordPress release: each language's pack is the
    one that release installs, which may be an older one.

    The fingerprint is every pack's version and update time, one request per
    slug. Downloads are pause seconds apart.
    """

    required = ("kind",)
    optional = ("slugs", "release", "base_url", "pause")
    kinds = ("core", "plugins", "themes")
    rotates = True

    # Register and spelling variants, kept with their language.
    locales = {
        "de_CH_informal": "de_CH", "de_DE_formal": "de_DE", "nl_NL_formal": "nl_NL",
        "pt_PT_ao90": "pt_PT",
    }

    def __init__(self, settings):
        super(WordPress, self).__init__(settings)
        if settings["kind"] not in self.kinds:
            raise ValueError("kind must be one of %s" % ", ".join(self.kinds))
        slugs = settings.get("slugs")
        if (settings["kind"] == "core") != (slugs is None) or slugs is not None and (
                not isinstance(slugs, list) or not slugs
                or not all(isinstance(slug, str) and re.fullmatch(r"[a-z0-9-]+", slug)
                           for slug in slugs)):
            raise ValueError("slugs must list the plugins or themes, and only for them")
        if "release" in settings and (settings["kind"] != "core" or not isinstance(
                settings["release"], str) or not re.fullmatch(r"\d+(\.\d+)+", settings["release"])):
            raise ValueError("release must be a WordPress release such as 7.1, for core only")

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://api.wordpress.org/translations").rstrip("/")

    def packs(self):
        """[(slug, WordPress locale, version, updated, zip URL)]"""
        if not hasattr(self, "_packs"):
            kind = self.settings["kind"]
            self._packs = []
            for slug in self.settings.get("slugs", ["core"]):
                url = "%s/%s/1.0/" % (self.base_url, kind)
                if kind != "core":
                    url += "?slug=" + urllib.parse.quote(slug)
                elif "release" in self.settings:
                    url += "?version=" + self.settings["release"]
                try:
                    self._packs += [(slug, pack["language"], pack["version"], pack["updated"],
                                     pack["package"])
                                    for pack in json.loads(http_text(url))["translations"]]
                except (ValueError, KeyError, TypeError) as e:
                    raise FetchError("%s: %s" % (url, e))
        return self._packs

    def fingerprint(self):
        return combine([("%s %s" % (slug, lang), "%s %s" % (version, updated))
                        for slug, lang, version, updated, url in self.packs()])

    def languages(self):
        """The languages files are given, with their register variants."""
        return sorted({self.locales.get(lang, lang) for slug, lang, *rest in self.packs()})

    def fetch(self, dest, languages=None):
        files = []
        with tempfile.TemporaryDirectory() as tmp:
            for slug, lang, version, updated, url in self.packs():
                if languages is not None and self.locales.get(lang, lang) not in languages:
                    continue
                path = os.path.join(tmp, "pack.zip")
                download(url, path)
                directory = os.path.join(dest, slug, lang)
                extract_zip(path, directory)
                files.extend((os.path.join(slug, lang, f), self.locales.get(lang, lang))
                             for f in files_under(directory))
                time.sleep(self.settings.get("pause", 1))
        return files


class GlotPress(Fetcher):
    """A GlotPress project's translations (translate.wordpress.org): a PO export
    of the current translations per locale and translation set, of the locales
    with anything translated.

    project is the project's path, such as apps/android/dev. Its page lists
    every locale and set with its translated count, which is the fingerprint.
    Files are given the locale's slug as their language, so a formal or other
    set goes with its language. Exports are pause seconds apart; a 429 is
    retried after retry_wait seconds, twice as long the second time.
    """

    required = ("project",)
    optional = ("base_url", "pause", "retry_wait")
    rotates = True

    # Slugs that aren't language codes as they are.
    locales = {"ca-val": "ca@valencia"}
    # Emoji and pirate English.
    not_languages = {"art-xemoji", "pirate"}

    def __init__(self, settings):
        super(GlotPress, self).__init__(settings)
        if not isinstance(settings["project"], str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9-]*(/[a-z0-9][a-z0-9.-]*)*", settings["project"]):
            raise ValueError("project must be a GlotPress project path such as apps/android/dev")

    @property
    def project_url(self):
        return "%s/projects/%s/" % (self.settings.get(
            "base_url", "https://translate.wordpress.org").rstrip("/"), self.settings["project"])

    def _get(self, action, url):
        """action(url), waiting and trying again on 429 Too Many Requests."""
        wait = self.settings.get("retry_wait", 60)
        for attempt in range(3):
            try:
                return action(url)
            except FetchError as e:
                if e.status != 429 or attempt == 2:
                    raise
            time.sleep(wait * 2 ** attempt)

    def sets(self):
        """[(locale slug, set slug, translated count)] with something translated."""
        if not hasattr(self, "_sets"):
            path = re.escape(urllib.parse.urlsplit(self.project_url).path)
            page = self._get(http_text, self.project_url)
            rows = re.findall(path + r'([a-z0-9-]+)/([a-z0-9-]+)/\?filters%5Bstatus%5D=current"'
                              r'>([\d,]+)</a>', page)
            if not rows:
                raise FetchError("%s: no locales listed" % self.project_url)
            self._sets = sorted((slug, name, int(count.replace(",", "")))
                                for slug, name, count in rows
                                if count.strip("0,") and slug not in self.not_languages)
        return self._sets

    def fingerprint(self):
        return combine([("%s/%s" % (slug, name), str(count)) for slug, name, count in self.sets()])

    def languages(self):
        return sorted({self.locales.get(slug, slug) for slug, name, count in self.sets()})

    def fetch(self, dest, languages=None):
        files = []
        for slug, name, count in self.sets():
            lang = self.locales.get(slug, slug)
            if languages is not None and lang not in languages:
                continue
            path = os.path.join(slug, name + ".po")
            os.makedirs(os.path.join(dest, slug), exist_ok=True)
            # Current only: a whole export is mostly untranslated entries.
            url = "%s%s/%s/export-translations/?format=po&filters%%5Bstatus%%5D=current" % (
                self.project_url, slug, name)
            self._get(lambda url: download(url, os.path.join(dest, path)), url)
            files.append((path, lang))
            time.sleep(self.settings.get("pause", 5))
        return files


class Drupal(Fetcher):
    """localize.drupal.org: a .po per language of each project's latest
    stable release.

    The languages come from the translation server's l10n_server.xml, and
    each release from the project's release history. With core, a Drupal
    major version, each release is the newest for that core. The files are
    rebuilt daily, so the fingerprint is the releases. A language with no
    file for a project is skipped. Downloads are pause seconds apart.
    """

    required = ("projects",)
    optional = ("core", "base_url", "releases_url", "pause")
    rotates = True

    def __init__(self, settings):
        super(Drupal, self).__init__(settings)
        projects = settings["projects"]
        if not isinstance(projects, list) or not projects or not all(
                isinstance(project, str) and re.fullmatch(r"[a-z0-9_]+", project)
                for project in projects):
            raise ValueError("projects must list Drupal project names")
        if "core" in settings and not (isinstance(settings["core"], str)
                                       and settings["core"].isdigit()):
            raise ValueError("core must be a Drupal major version, such as \"11\"")

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://ftp.drupal.org/files/translations").rstrip("/")

    @staticmethod
    def _xml(url):
        try:
            return ET.fromstring(http_text(url))
        except ET.ParseError as e:
            raise FetchError("%s: %s" % (url, e))

    def languages(self):
        if not hasattr(self, "_languages"):
            server = self._xml(self.base_url + "/l10n_server.xml")
            self._languages = sorted(code.text for code in server.iter("code"))
        return self._languages

    @staticmethod
    def core_majors(compatibility):
        """The Drupal majors a core_compatibility such as "^10.3 || ^11" allows."""
        majors = set()
        for alternative in (compatibility or "").split("||"):
            lowest = re.search(r"(?:^|[\s^~=])(\d+)(?:\.|\s|$)", alternative.replace(">=", "="))
            below = re.search(r"<\s*(\d+)", alternative)
            if lowest and below:
                majors.update(range(int(lowest.group(1)), int(below.group(1))))
            elif lowest:
                majors.add(int(lowest.group(1)))
        return majors

    def release(self, project):
        """The newest published version that isn't a pre-release (for core)."""
        url = "%s/%s/current" % (self.settings.get(
            "releases_url", "https://updates.drupal.org/release-history").rstrip("/"), project)
        core = self.settings.get("core")
        for release in self._xml(url).iter("release"):
            version = release.findtext("version") or ""
            if release.findtext("status") != "published" or re.search(
                    r"(alpha|beta|rc|dev)\d*$", version):
                continue
            if core is None:
                return version
            if project == "drupal":
                if version.split(".")[0] == core:
                    return version
            elif int(core) in self.core_majors(release.findtext("core_compatibility")):
                return version
        raise FetchError("%s: no stable release%s" % (url, " for core %s" % core if core else ""))

    def releases(self):
        if not hasattr(self, "_releases"):
            self._releases = [(project, self.release(project))
                              for project in self.settings["projects"]]
        return self._releases

    def fingerprint(self):
        return combine(self.releases())

    def fetch(self, dest, languages=None):
        files = []
        for project, version in self.releases():
            os.makedirs(os.path.join(dest, project))
            for lang in self.languages():
                if languages is not None and lang not in languages:
                    continue
                name = "%s-%s.%s.po" % (project, version, lang)
                try:
                    download("%s/all/%s/%s" % (self.base_url, project, urllib.parse.quote(name)),
                             os.path.join(dest, project, name))
                    files.append((os.path.join(project, name), lang))
                except FetchError as e:
                    if e.status != 404:
                        raise
                time.sleep(self.settings.get("pause", 1))
        return files


class Moodle(Fetcher):
    """Moodle's language packs for a release: a zip per language, listed in
    the index Moodle's language installer reads, English included.

    The fingerprint is the index, which gives each pack's MD5. Downloads are
    checked against it, pause seconds apart.
    """

    required = ("release",)
    optional = ("base_url", "pause")
    rotates = True

    def __init__(self, settings):
        super(Moodle, self).__init__(settings)
        if not isinstance(settings["release"], str) or not re.fullmatch(
                r"\d+\.\d+", settings["release"]):
            raise ValueError("release must be a Moodle release such as 5.1")

    @property
    def url(self):
        return "%s/%s/" % (self.settings.get(
            "base_url", "https://packaging.moodle.org/langpack").rstrip("/"),
            self.settings["release"])

    def index(self):
        """[(language, MD5 of its pack)]"""
        if not hasattr(self, "_index"):
            url = self.url + "languages.md5"
            lines = http_text(url).splitlines()
            index = [re.match(r"([a-z]{2,3}(?:_[a-z0-9]+)*),([0-9a-f]{32}),", line)
                     for line in lines if line.strip()]
            if not index or not all(index):
                raise FetchError("%s: not a language pack index" % url)
            self._index = [(match.group(1), match.group(2)) for match in index]
        return self._index

    def fingerprint(self):
        return combine(self.index())

    def languages(self):
        return [lang for lang, md5 in self.index()]

    def fetch(self, dest, languages=None):
        files = []
        if languages is not None:
            # English is the template the others pair with: always fetched.
            languages = set(languages) | {"en"}
        with tempfile.TemporaryDirectory() as tmp:
            for lang, md5 in self.index():
                if languages is not None and lang not in languages:
                    continue
                path = os.path.join(tmp, "pack.zip")
                url = "%s%s.zip" % (self.url, lang)
                download(url, path)
                with open(path, "rb") as f:
                    if hashlib.md5(f.read()).hexdigest() != md5:
                        raise FetchError("%s: MD5 doesn't match the index" % url)
                extract_zip(path, dest)
                if os.path.isdir(os.path.join(dest, lang)):
                    files.extend((os.path.join(lang, f), lang)
                                 for f in files_under(os.path.join(dest, lang)))
                time.sleep(self.settings.get("pause", 1))
        return files


class Joomla(Fetcher):
    """Joomla's accredited language packs for a major release, with the
    English they translate from joomla-cms.

    The packs are listed in translationlist_<release>.xml, and each pack's
    details give its zip and SHA-256; a pack that doesn't match is skipped.
    The English is the files of joomla-cms at ref that the block's template
    matches. The fingerprint is the packs' versions and ref's commit.
    Downloads are pause seconds apart.
    """

    required = ("release", "ref")
    optional = ("base_url", "repository", "pause")
    rotates = True
    needs_template = True
    # Where Joomla installs each client's files.
    clients = {"site": "language", "administrator": "administrator/language",
               "api": "api/language"}

    def __init__(self, settings):
        super(Joomla, self).__init__(settings)
        if not isinstance(settings["release"], str) or not settings["release"].isdigit():
            raise ValueError("release must be a Joomla major version, such as \"5\"")

    def use_template(self, template):
        if not template:
            raise ValueError("joomla needs a template: the English files of joomla-cms")
        self.git = Git({"url": self.settings.get("repository", "https://github.com/joomla/joomla-cms"),
                        "ref": self.settings["ref"], "sparse": [template]})

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://update.joomla.org/language").rstrip("/")

    def packs(self):
        """[(language, version, details URL)]"""
        if not hasattr(self, "_packs"):
            url = "%s/translationlist_%s.xml" % (self.base_url, self.settings["release"])
            try:
                listing = ET.fromstring(http_text(url))
            except ET.ParseError as e:
                raise FetchError("%s: %s" % (url, e))
            self._packs = [(pack.get("element", "").removeprefix("pkg_"), pack.get("version"),
                            pack.get("detailsurl")) for pack in listing.iter("extension")]
            if not self._packs or not all(all(pack) for pack in self._packs):
                raise FetchError("%s: not a list of language packs" % url)
        return self._packs

    def fingerprint(self):
        return combine([(lang, version) for lang, version, details in self.packs()]
                       + [("en-GB", self.git.fingerprint())])

    def _download(self, details, version):
        """The URL and SHA-256 of a pack's version."""
        try:
            updates = ET.fromstring(http_text(details)).iter("update")
            update = next(update for update in updates if update.findtext("version") == version)
            return update.findtext("downloads/downloadurl").strip(), update.findtext("sha256").strip()
        except (ET.ParseError, StopIteration, AttributeError) as e:
            raise FetchError("%s: no download for %s (%s)" % (details, version, e))

    def _unpack_clients(self, pack, lang):
        """Unpack the zip per client that a package manifest names, as Joomla does."""
        for manifest in sorted(os.listdir(pack)):
            if not manifest.endswith(".xml"):
                continue
            try:
                files = ET.parse(os.path.join(pack, manifest)).getroot().iter("file")
            except ET.ParseError:
                continue
            for element in files:
                inner = os.path.join(pack, os.path.basename((element.text or "").strip()))
                client = self.clients.get(element.get("client"))
                if element.get("type") == "language" and client and zipfile.is_zipfile(inner):
                    extract_zip(inner, os.path.join(pack, *client.split("/"), lang))

    def languages(self):
        return [lang for lang, version, details in self.packs()]

    def fetch(self, dest, languages=None):
        with tempfile.TemporaryDirectory() as tmp:
            self.git.fetch(os.path.join(tmp, "english"))
            shutil.copytree(os.path.join(tmp, "english"), dest, dirs_exist_ok=True)
            found = {path: "en-GB" for path in files_under(os.path.join(tmp, "english"))}
            for lang, version, details in self.packs():
                if languages is not None and lang not in languages:
                    continue
                url, sha256 = self._download(details, version)
                path = os.path.join(tmp, "pack.zip")
                download(url, path)
                with open(path, "rb") as f:
                    if hashlib.sha256(f.read()).hexdigest() != sha256:
                        # Teams publish the hashes by hand: one bad one
                        # mustn't stop the other languages.
                        logging.warning("%s: SHA-256 doesn't match %s, skipped", url, details)
                        continue
                pack = os.path.join(tmp, lang)
                extract_zip(path, pack)
                self._unpack_clients(pack, lang)
                # Only its language's files, from where Joomla installs them:
                # some packs add a folder around them, the installer scripts
                # have the same name in every pack, and some zips carry
                # macOS's __MACOSX copies.
                own = re.compile(r"(?:^|/)((?:administrator/|api/)?language/%s/[^/]+)$"
                                 % re.escape(lang))
                for name in files_under(pack):
                    match = own.search(name.replace("\\", "/"))
                    if not match or "__MACOSX/" in name or match.group(1).rsplit("/", 1)[1].startswith("._"):
                        continue
                    if match.group(1) in found:
                        continue  # the same tree twice in one pack
                    target = os.path.join(dest, *match.group(1).split("/"))
                    os.makedirs(os.path.dirname(target), exist_ok=True)
                    shutil.copyfile(os.path.join(pack, name), target)
                    found[match.group(1)] = lang
                time.sleep(self.settings.get("pause", 1))
        return sorted(found.items())


class VSCode(Fetcher):
    """VS Code's language packs from vscode-loc, paired with the English of the
    VS Code release they target, as a PO file per language and part.

    Core strings are keyed by module and message id, so their English comes
    from that release's build on Microsoft's CDN, as does each extension's
    package strings; bundle strings are keyed by their English. Strings
    with no English there (node-only modules) are left out. The fingerprint
    is vscode-loc's commit.
    """

    optional = ("repository", "ref", "update_url", "cdn_url")

    def __init__(self, settings):
        super(VSCode, self).__init__(settings)
        self.git = Git({"url": settings.get("repository", "https://github.com/microsoft/vscode-loc"),
                        "ref": settings.get("ref", "main"),
                        "sparse": ["/i18n/vscode-language-pack-*/package.json",
                                   "/i18n/vscode-language-pack-*/translations/"]})

    def fingerprint(self):
        return self.git.fingerprint()

    @staticmethod
    def _json(url, missing_ok=False):
        """url's JSON, unzipped if the CDN sent it gzipped; None if missing_ok and 404."""
        try:
            with _request(url) as response:
                data = response.read()
        except FetchError as e:
            if missing_ok and e.status == 404:
                return None
            raise
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        try:
            return json.loads(data.decode("utf-8-sig"))
        except ValueError as e:
            raise FetchError("%s: %s" % (url, e))

    def english(self, packs):
        """(core English by (module, id), CDN base) for the release packs target."""
        with open(os.path.join(packs[0], "package.json"), encoding="utf-8-sig") as f:
            version = json.load(f)["engines"]["vscode"].lstrip("^~>=")
        url = "%s/api/versions/%s/linux-x64/stable" % (
            self.settings.get("update_url", "https://update.code.visualstudio.com").rstrip("/"),
            version)
        commit = self._json(url).get("version")
        if not commit:
            raise FetchError("%s: no commit" % url)
        base = "%s/%s" % (self.settings.get("cdn_url", "https://main.vscode-cdn.net/stable")
                          .rstrip("/"), commit)
        keys = self._json(base + "/out/nls.keys.json")
        messages = iter(self._json(base + "/out/nls.messages.json"))
        return {(module, key): next(messages) for module, ids in keys for key in ids}, base

    def fetch(self, dest):
        files = []
        with tempfile.TemporaryDirectory() as tmp:
            loc = os.path.join(tmp, "loc")
            self.git.fetch(loc)
            root = os.path.join(loc, "i18n")
            packs = sorted(os.path.join(root, name) for name in os.listdir(root)
                           if name.startswith("vscode-language-pack-"))
            if not packs:
                raise FetchError("%s: no language packs" % self.git.settings["url"])
            core, base = self.english(packs)
            extensions = {}
            for pack in packs:
                lang = os.path.basename(pack).removeprefix("vscode-language-pack-")
                translations = os.path.join(pack, "translations")
                for name in files_under(translations):
                    with open(os.path.join(translations, name), encoding="utf-8-sig") as f:
                        contents = json.load(f).get("contents", {})
                    extension = os.path.basename(name).removesuffix(".i18n.json")
                    store = po.pofile()
                    for section, strings in sorted(contents.items()):
                        if section == "bundle":
                            english = {text: text for text in strings}
                        elif section == "package":
                            if extension not in extensions:
                                # Built-in extensions are named without "vscode."
                                extensions[extension] = self._json("%s/extensions/%s/package.nls.json" % (
                                    base, extension.removeprefix("vscode.")), missing_ok=True) or {}
                            english = extensions[extension]
                        else:
                            english = {key: core.get((section, key)) for key in strings}
                        for key, target in sorted(strings.items()):
                            source = english.get(key)
                            if isinstance(source, dict):  # {"message": ..., "comment": ...}
                                source = source.get("message")
                            if isinstance(source, str) and source and isinstance(target, str):
                                unit = store.addsourceunit(source)
                                unit.target = target
                                unit.setcontext("%s:%s" % (section, key))
                    if store.units:
                        path = os.path.join(lang, os.path.splitext(name)[0] + ".po")
                        os.makedirs(os.path.dirname(os.path.join(dest, path)), exist_ok=True)
                        store.savefile(os.path.join(dest, path))
                        files.append((path, lang))
        return files


class Snapshot(Fetcher):
    """Data kept in the database by snapshot_db; nothing to download."""

    required = ("snapshot",)

    def fingerprint(self):
        return "snapshot %s" % self.settings["snapshot"]

    def fetch(self, dest):
        return []


FETCHERS = {
    "damned-lies": DamnedLies,
    "drupal": Drupal,
    "translationproject": TranslationProject,
    "vscode": VSCode,
    "git": Git,
    "glotpress": GlotPress,
    "http": HttpPerLanguage,
    "joomla": Joomla,
    "moodle": Moodle,
    "pontoon": Pontoon,
    "wordpress": WordPress,
    "svn": Svn,
    "snapshot": Snapshot,
}
