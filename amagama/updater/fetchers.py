"""Fetch translation files from where projects publish them.

Each fetcher downloads one block's files into a directory and reports their
target languages, and computes a fingerprint cheaply enough to check weekly.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request

from amagama import __version__

USER_AGENT = "amaGama-updater/%s (+https://github.com/translate/amagama)" % __version__
TIMEOUT = 120


class FetchError(Exception):
    pass


def _request(url, method="GET"):
    request = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})
    try:
        return urllib.request.urlopen(request, timeout=TIMEOUT)
    except (urllib.error.URLError, OSError) as e:
        raise FetchError("%s: %s" % (url, e))


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

    def fetch(self, dest):
        """Download into dest; return [(path relative to dest, language)]."""
        raise NotImplementedError


class TarballPerLanguage(Fetcher):
    """One tarball per language (and per kind, if the site splits them)."""

    # Whether each tarball already has its files under <lang>/.
    has_lang_dir = False

    def languages(self):
        raise NotImplementedError

    def urls(self, lang):
        raise NotImplementedError

    def fingerprint(self):
        return combine([(url, http_fingerprint(url))
                        for lang in self.languages() for url in self.urls(lang)])

    def fetch(self, dest):
        files = []
        with tempfile.TemporaryDirectory() as tmp:
            for lang in self.languages():
                lang_dir = os.path.join(dest, lang)
                for url in self.urls(lang):
                    path = os.path.join(tmp, "download")
                    download(url, path)
                    extract_tarball(path, dest if self.has_lang_dir else lang_dir)
                if os.path.isdir(lang_dir):
                    files.extend((os.path.join(lang, f), lang) for f in files_under(lang_dir))
        return files


class DamnedLies(TarballPerLanguage):
    """GNOME's l10n.gnome.org: per-language tarballs of a release.

    The tarballs are rebuilt daily, so the fingerprint is the release's
    statistics instead: one request, changing when any language's counts do.
    """

    required = ("release",)
    optional = ("tarballs", "base_url")

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
        """Languages with anything translated in the release."""
        return sorted(lang["lang_locale"] for lang in self.statistics()
                      if any(lang.get(kind, {}).get("translated") for kind in ("ui", "doc")))

    def fingerprint(self):
        stats = json.dumps(sorted(self.statistics(), key=lambda lang: lang["lang_locale"]),
                           sort_keys=True)
        return hashlib.sha256(stats.encode("utf-8")).hexdigest()

    def urls(self, lang):
        return ["%s/languages/%s/%s/%s.tar.gz" % (self.base_url, lang, self.settings["release"], tarball)
                for tarball in self.settings.get("tarballs", ["ui"])]


class TranslationProject(TarballPerLanguage):
    """The Translation Project's monthly per-language backups."""

    optional = ("base_url",)

    @property
    def base_url(self):
        return self.settings.get("base_url", "https://translationproject.org/backups/last/").rstrip("/") + "/"

    def languages(self):
        if not hasattr(self, "_languages"):
            listing = http_text(self.base_url)
            self._languages = sorted(set(re.findall(r'href="([^"/]+)\.tgz"', listing)))
        return self._languages

    has_lang_dir = True

    def urls(self, lang):
        return [self.base_url + lang + ".tgz"]


class Git(Fetcher):
    """A shallow clone of one branch or tag.

    layout "gnu" is <dir>/<lang>.po; "nongnu" is <dir>/<lang>/... under each
    of paths (default: the repository root).
    """

    required = ("url", "layout")
    optional = ("ref", "paths")

    def __init__(self, settings):
        super(Git, self).__init__(settings)
        if settings["layout"] not in ("gnu", "nongnu"):
            raise ValueError("layout must be gnu or nongnu")

    def _git(self, *args):
        # Outside any repository, and without a caller's GIT_DIR and friends.
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        try:
            return subprocess.run(("git",) + args, check=True, capture_output=True, text=True,
                                  timeout=3600, cwd=tempfile.gettempdir(), env=env).stdout
        except (subprocess.SubprocessError, OSError) as e:
            stderr = getattr(e, "stderr", None) or ""
            raise FetchError("git %s: %s %s" % (args[0], e, stderr.strip()))

    def fingerprint(self):
        ref = self.settings.get("ref", "HEAD")
        lines = self._git("ls-remote", self.settings["url"], ref).split()
        if not lines:
            raise FetchError("%s: no ref %s" % (self.settings["url"], ref))
        return lines[0]

    def fetch(self, dest):
        args = ["clone", "--quiet", "--depth", "1"]
        if "ref" in self.settings:
            args += ["--branch", self.settings["ref"]]
        self._git(*(args + [self.settings["url"], dest]))
        shutil.rmtree(os.path.join(dest, ".git"))
        files = []
        for top in self.settings.get("paths", ["."]):
            root = os.path.normpath(os.path.join(dest, top))
            if not os.path.isdir(root):
                raise FetchError("%s: no directory %s" % (self.settings["url"], top))
            for path in files_under(root):
                if self.settings["layout"] == "gnu":
                    lang = os.path.splitext(os.path.basename(path))[0]
                else:
                    lang = path.split(os.sep)[0]
                    if lang == path:
                        continue
                files.append((os.path.relpath(os.path.join(root, path), dest), lang))
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
    "translationproject": TranslationProject,
    "git": Git,
    "snapshot": Snapshot,
}
