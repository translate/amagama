#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Candidate repositories on GitLab.com for discover_sources.

GitLab.com has no code search across projects, so its public projects are
listed by stars and each one's top-level files show whether it commits a
signal's configuration (LINGUAS in po/). Configurations kept deeper in a
repository are missed. A project's listings and licence are cached by its
last activity, so a run of every signal reads each project once.
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import spdx_license_list

from amagama.updater.fetchers import TIMEOUT, USER_AGENT, FetchError, TRANSIENT_STATUSES, retrying

API = "https://gitlab.com/api/v4"
HOST = "gitlab.com"

# The top-level names that show each signal's configuration.
TOP_LEVEL = {
    "crowdin": {"crowdin.yml", "crowdin.yaml", ".crowdin.yml"},
    "l10n-toml": {"l10n.toml"},
    "lingui": {"lingui.config.js", "lingui.config.ts", ".linguirc"},
    "inlang": {"project.inlang"},
    "transifex": {".tx"},
    "weblate": {".weblate"},
}
# The pages of 100 GitLab lists projects by offset.
MAX_PAGES = 500

# A signal whose file is in a directory: (directory, file).
INSIDE = {"linguas": ("po", "LINGUAS")}


def spdx(key):
    """The SPDX id of a GitLab licence key ("gpl-3.0", "mit"), or None.
    GitLab's "gpl-2.0+" is SPDX's deprecated GPL-2.0+: GPL-2.0-or-later."""
    if not key or key == "other":
        return None
    later = key.endswith("+")
    key = key.removesuffix("+").lower()
    for spdx_id in spdx_license_list.LICENSES:
        if spdx_id.lower() == key:
            if later and spdx_id + "-or-later" in spdx_license_list.LICENSES:
                return spdx_id + "-or-later"
            return None if later else spdx_id
    return None


def repository(project, licence_key=None):
    """A project's stats in the shape discovery.select() and licence() read."""
    return {
        "url": project["web_url"],
        "stargazerCount": project.get("star_count", 0),
        "isArchived": bool(project.get("archived")),
        "isFork": bool(project.get("forked_from_project")),
        # GitLab gives no push date: activity, which issues count too.
        "pushedAt": project.get("last_activity_at"),
        "description": project.get("description"),
        "hasIssuesEnabled": project.get("issues_enabled", True),
        "licenseInfo": {"spdxId": spdx(licence_key)},
    }


class GitLab(object):
    """GitLab.com's API with a token, requests pause seconds apart; cache_path
    is a JSON file of listings and licences kept between runs."""

    def __init__(self, token, cache_path=None, pause=0.5):
        self.token = token
        self.pause = pause
        # Whether projects() stopped at the end of what GitLab lists.
        self.truncated = False
        self.cache_path = cache_path
        self.cache = {}
        if cache_path and os.path.exists(cache_path):
            try:
                with open(cache_path, encoding="utf-8") as f:
                    self.cache = json.load(f)
            except (OSError, ValueError):
                self.cache = {}

    def save(self):
        if self.cache_path:
            os.makedirs(os.path.dirname(self.cache_path) or ".", exist_ok=True)
            with open(self.cache_path + ".new", "w", encoding="utf-8") as f:
                json.dump(self.cache, f)
            os.replace(self.cache_path + ".new", self.cache_path)

    def get(self, endpoint, **params):
        """An API request's JSON, or None for 404; transient errors are tried
        again (fetchers.retrying)."""
        url = "%s/%s?%s" % (API, endpoint, urllib.parse.urlencode(params))
        request = urllib.request.Request(url, headers={"PRIVATE-TOKEN": self.token,
                                                       "User-Agent": USER_AGENT})

        def attempt():
            time.sleep(self.pause)
            try:
                with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                raise FetchError("%s/%s: %s" % (API, endpoint, e), e.code, e.headers or {},
                                 e.code in TRANSIENT_STATUSES)
            except (urllib.error.URLError, OSError) as e:
                raise FetchError("%s/%s: %s" % (API, endpoint, e), transient=True)
            except ValueError as e:
                raise FetchError("%s/%s: %s" % (API, endpoint, e))
        return retrying(HOST, attempt)

    def projects(self, min_stars):
        """Public projects with at least min_stars, most stars first."""
        found = []
        # GitLab pages a listing by offset up to 50,000 projects only.
        for page in range(1, MAX_PAGES + 1):
            batch = self.get("projects", visibility="public", order_by="star_count",
                             sort="desc", per_page=100, page=page) or []
            found += [project for project in batch if project.get("star_count", 0) >= min_stars]
            if len(batch) < 100 or batch[-1].get("star_count", 0) < min_stars:
                return found
        self.truncated = True
        return found

    def project(self, path):
        """A project by its path ("group/name"), or None."""
        return self.get("projects/%s" % urllib.parse.quote(path, safe=""))

    def _cached(self, kind, project, compute):
        key = "%s:%s" % (project["id"], project.get("last_activity_at"))
        table = self.cache.setdefault(kind, {})
        if key not in table:
            table[key] = compute()
        return table[key]

    def names(self, project, directory=None):
        """The names at the top of a project's default branch, or in directory."""
        if project.get("empty_repo") or not project.get("default_branch"):
            return []
        params = {"per_page": 100}
        if directory:
            params["path"] = directory

        def listing():
            names = []
            for page in range(1, 1000):
                batch = self.get("projects/%d/repository/tree" % project["id"], page=page,
                                 **params) or []
                names += [item["name"] for item in batch]
                if len(batch) < 100:
                    break
            return sorted(names)
        return self._cached("names" if directory is None else "names/" + directory, project,
                            listing)

    def has_config(self, project, signal):
        """Whether a project commits signal's configuration where it's looked for."""
        if signal in INSIDE:
            directory, name = INSIDE[signal]
            return directory in self.names(project) and name in self.names(project, directory)
        return bool(TOP_LEVEL[signal] & set(self.names(project)))

    def licence_key(self, project):
        """GitLab's key for a project's licence ("mit"), or None."""
        return self._cached("licences", project, lambda: ((self.get(
            "projects/%d" % project["id"], license="true") or {}).get("license") or {}).get("key"))
