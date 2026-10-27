#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks from repositories' .weblate files, for review.

A repository translated on Weblate may commit .weblate, the wlc client's
configuration: the instance's API and one project/component, but no paths.
On an instance weblate_sources generates blocks for, the project's
components in this repository are looked up there and become blocks as
weblate_sources would write them. Any other instance is counted, with how
many components it has, as a candidate for its own weblate_sources run.
"""

import collections
import configparser
import json
import re
import urllib.parse

from amagama.updater import weblate
from amagama.updater.fetchers import FetchError, http_text
from amagama.updater.generate import normal_url, tree
from amagama.updater.repositories import blocks as repository_blocks
from amagama.updater.repositories import candidates
from amagama.updater.repositories import read as read_configs

CONFIG = re.compile(r"(?:^|/)\.weblate\Z")


def settings(text):
    """(instance URL, project, component or None) of a .weblate, or None."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return None
    for section in parser.sections():
        url = parser[section].get("url", "").strip()
        translation = parser[section].get("translation", "").strip()
        if url and translation and translation.count("/") <= 1:
            instance = re.sub(r"/api/?\Z", "", url.rstrip("/"))
            project, _, component = translation.partition("/")
            return instance, project, component or None
    return None


def component_count(instance, get=http_text):
    """How many components an instance has, or None if its API doesn't say."""
    try:
        return json.loads(get("%s/api/components/?page_size=1" % instance))["count"]
    except (FetchError, ValueError, KeyError, TypeError):
        return None


def entries(url, project_components, named):
    """(files by branch, skipped) of the project's components in url's
    repository, or in the repository of the component the .weblate names
    (a repository renamed since)."""
    by_url = {component.get("url"): component for component in project_components}
    own = {normal_url(url)}
    for component in project_components:
        if component.get("slug") == named:
            repo, _ = weblate._repository(component, by_url)
            if repo:
                own.add(normal_url(repo))
    files, skipped = collections.defaultdict(list), collections.Counter()
    for component in project_components:
        repo, branch = weblate._repository(component, by_url)
        if not repo or normal_url(repo) not in own:
            continue
        reason = weblate.readable(component)
        if reason:
            skipped["components", reason] += 1
            continue
        files[branch or None].append(weblate.entry(component))
    return files, skipped


def repository(url):
    """repositories.read() of url's .weblate files."""
    return read_configs(url, CONFIG)


def blocks(repositories, prefix, covered=(), names=None, pause=1, read=None, licences=None,
           mirrors=None, min_units=None, lookup=None, count=component_count, trees=tree):
    """(blocks, skipped) for repositories' .weblate files; see
    repositories.blocks() for the arguments and what skipped counts.

    lookup(instance, project) gives a project's components (default: the
    instance's API); trees(url, ref) a repository's paths at a branch.
    Units aren't counted (min_units).
    """
    def resolve(url):
        read_back = (read or repository)(url)
        if read_back is None:
            return None
        paths, texts = read_back
        if not texts:
            return [], {("repositories", "no .weblate"): 1}
        skipped = collections.Counter()
        found = []
        for config, text in sorted(texts.items()):
            given = settings(text) if text is not None else None
            if given is None:
                skipped["components", "unreadable .weblate"] += 1
                continue
            instance, project, component = given
            host = urllib.parse.urlsplit(instance).hostname
            if host in weblate.DEAD_HOSTS:
                skipped["components", "host %s %s" % (host, weblate.DEAD_HOSTS[host])] += 1
                continue
            if host not in weblate.GENERATED:
                total = count(instance)
                skipped["instances", "%s (%s components): a candidate for weblate_sources" % (
                    instance, "?" if total is None else total)] += 1
                continue
            try:
                project_components = (lookup or (lambda instance, project: weblate.components(
                    instance, pause, project)))(instance, project)
            except FetchError:
                skipped["components", "project %s not found on %s" % (project, host)] += 1
                continue
            files, reasons = entries(url, project_components, component)
            skipped += reasons
            for branch, branch_files in sorted(files.items(), key=lambda item: item[0] or ""):
                # On several branches, a block is named by its branch, and a
                # release-like one (1.2.x) is a version, as weblate_sources does.
                several = len(files) > 1 and branch
                branch_paths = trees(url, branch) if branch else paths
                if branch_paths is None:
                    skipped["components", "branch %s not found" % branch] += 1
                    continue
                branch_found, reasons = candidates(branch_files, branch_paths, "components",
                                                   "filemask matches nothing")
                skipped += reasons
                for candidate in branch_found:
                    if branch:
                        candidate["ref"] = branch
                    if several:
                        suffix = re.sub(r"[^\w.-]+", "-", branch)
                        candidate["suffix"] = "%s-%s" % (candidate["suffix"], suffix) \
                            if candidate["suffix"] else suffix
                        if re.search(r"\d", branch):
                            candidate["version"] = branch
                    found.append(candidate)
        return found, skipped

    return repository_blocks(repositories, prefix, resolve, "components", covered, names, pause,
                             licences, mirrors)
