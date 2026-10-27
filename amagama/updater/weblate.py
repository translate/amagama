#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Sources-file blocks for a Weblate instance's projects, for review.

Weblate's API lists each component's repository, branch, filemask, template,
file format, licence and source language: what a git block needs. The
translations are fetched from those repositories, which Weblate commits to,
so the instance isn't asked for files.
"""

import fnmatch
import json
import re
import time
import urllib.parse

from amagama.updater.fetchers import FetchError, http_text
from amagama.updater.generate import Names, check_block, identity, normal_url
from amagama.updater.sources import sparse_patterns

# Weblate file formats amaGama reads, and the format setting each needs.
FORMATS = {
    "po": None,
    "ts": "ts",
    "xliff": "xliff",
    "poxliff": "xliff",
    "aresource": "android",
    "json": "json",
    "json-nested": "json",
    "fluent": "fluent",
    "properties": "properties",
    "properties-utf8": "properties-utf8",
    "properties-utf16": "properties-utf16",
    "joomla": "joomla-ini",
    "php": "php",
    "i18next": "json",
    "i18nextv4": "json",
}
MONOLINGUAL = {"aresource", "json", "json-nested", "fluent", "properties", "properties-utf8",
               "properties-utf16", "joomla", "php", "i18next", "i18nextv4"}

# Hosts that no longer serve their repositories, though Weblate still lists them.
DEAD_HOSTS = {"pagure.io": "archived"}

# A repository with more single-file components than this is read whole,
# from the directory before the language.
COLLAPSE = 10


def components(url, pause=1):
    """All of a Weblate instance's components, from its API."""
    page = "%s/api/components/?page_size=1000" % url.rstrip("/")
    found = []
    while page:
        try:
            data = json.loads(http_text(page))
            found += data["results"]
        except (ValueError, KeyError) as e:
            raise FetchError("%s: %s" % (page, e))
        page = data.get("next")
        if page:
            time.sleep(pause)
    return found


def _with_encoding(component):
    """The component's Weblate format, naming a properties file's encoding.

    Newer Weblate keeps it in file_format_params, not in the format's name.
    """
    weblate_format = component.get("file_format")
    if weblate_format == "properties":
        encoding = ((component.get("file_format_params") or {}).get("properties_encoding")
                    or "").lower().replace("_", "-")
        if encoding in ("utf-8", "utf8"):
            return "properties-utf8"
        if encoding.startswith(("utf-16", "utf16")):
            return "properties-utf16"
    return weblate_format


def _repository(component, by_url):
    """(repo, branch) of a component, or (None, None).

    Follows a weblate:// link; an scp-style git@host:path becomes https; a
    repository only in Weblate (local:) is Weblate's git export of it.
    """
    if (component.get("repo") or "").startswith("weblate://"):
        component = by_url.get(component.get("linked_component") or "", {})
    repo = component.get("repo") or ""
    match = re.fullmatch(r"[\w.-]+@([\w.-]+):(?!//)(.+)", repo)
    if match:
        repo = "https://%s/%s" % match.groups()
    elif repo.startswith("local:"):
        return (component.get("git_export") or None), None
    return (repo, component.get("branch") or "") if "://" in repo else (None, None)


def _mask(filemask):
    """Weblate's filemask as a filemask: its first * is the language."""
    return filemask.replace("*", "{lang}", 1)


def _collapsed(masks):
    """masks as one "<dir>/{lang}/**/*<ext>" when they share both."""
    shapes = {(mask.split("{lang}")[0], "." + mask.rsplit(".", 1)[-1])
              for mask in masks if "{lang}/" in mask}
    if len(masks) <= COLLAPSE or len(shapes) != 1 or not all("{lang}/" in mask for mask in masks):
        return sorted(masks)
    prefix, extension = shapes.pop()
    return ["%s{lang}/**/*%s" % (prefix, extension)]


def blocks(components, prefix, projects=("*",), docs=(), covered=(), names=None):
    """(blocks, skipped) for the components of projects matching the globs.

    Blocks are dicts of sources-file settings; skipped counts components
    left out, by reason. docs are globs of documentation, imported with
    segmentation, matched to a project's slug or project/component;
    covered are repositories other blocks already import; names is the
    generate.Names the blocks take theirs from. Each block is checked as the
    sources file would check it.
    """
    by_url = {component.get("url"): component for component in components}
    covered = {normal_url(repo) for repo in covered}
    groups, skipped = {}, {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for component in components:
        project = component["project"]
        if not any(fnmatch.fnmatchcase(project["slug"], glob) for glob in projects):
            continue
        weblate_format = _with_encoding(component)
        licence = (component.get("license") or "").strip()
        if component.get("is_glossary") or weblate_format == "tbx":
            skip("glossary (terminology)")
        elif (component.get("source_language") or {}).get("code") != "en":
            skip("source not English")
        elif weblate_format not in FORMATS:
            skip("format %s" % weblate_format)
        elif "-ND-" in licence.upper():
            skip("no-derivatives licence")
        elif weblate_format in MONOLINGUAL and not component.get("template"):
            skip("monolingual without a template")
        else:
            repo, branch = _repository(component, by_url)
            if not repo:
                skip("no repository")
            elif normal_url(repo) in covered:
                skip("repository imported by another block")
            elif urllib.parse.urlsplit(repo).hostname in DEAD_HOSTS:
                host = urllib.parse.urlsplit(repo).hostname
                skip("host %s %s" % (host, DEAD_HOSTS[host]))
            else:
                # A bilingual file's template is only its source file.
                template = (component.get("template") or None) if weblate_format in MONOLINGUAL \
                    else None
                segment = any(fnmatch.fnmatchcase(project["slug"], glob)
                              or fnmatch.fnmatchcase("%s/%s" % (project["slug"], component["slug"]), glob)
                              for glob in docs)
                key = (project["slug"], repo, branch, weblate_format,
                       component["slug"] if template else "", licence, segment)
                group = groups.setdefault(key, {"project": project, "masks": set(),
                                                "template": template, "components": set()})
                group["masks"].add(_mask(component["filemask"]))
                group["components"].add(component["slug"])

    found = []
    names = names or Names()
    branches = {}
    for slug, repo, branch, *_ in groups:
        branches.setdefault((slug, repo), set()).add(branch)
    def base(slug, repo, branch):
        name = slug if slug.startswith(prefix + "-") else "%s-%s" % (prefix, slug)
        # A project on several branches: named by branch.
        if len(branches[slug, repo]) > 1 and branch:
            name += "-" + re.sub(r"[^\w.-]+", "-", branch)
        return name
    bases = {}
    for slug, repo, branch, *_ in groups:
        bases[base(slug, repo, branch)] = bases.get(base(slug, repo, branch), 0) + 1
    # A new name doesn't depend on what else the project has, as a project's
    # blocks are told apart by a component.
    chosen = {}
    for key, group in groups.items():
        slug, repo, branch, weblate_format, _, licence, segment = key
        kept = names.kept(identity(repo, branch, group["template"], segment, licence))
        if kept:
            chosen[key] = kept
            names.take(kept)
    for key, group in sorted(groups.items()):
        if key in chosen:
            continue
        name = base(*key[:3])
        if bases[name] > 1 or name in names:
            name += "-" + re.sub(r"[^\w.-]+", "-", min(group["components"]))
        chosen[key] = names.unique(name)
        names.take(chosen[key])
    for (slug, repo, branch, weblate_format, _, licence, segment), group in sorted(
            groups.items()):
        name = chosen[slug, repo, branch, weblate_format, _, licence, segment]
        # A branch that looks like a release (1.2.x) is a version.
        versioned = len(branches[slug, repo]) > 1 and branch and re.search(r"\d", branch)
        block = {"name": name, "title": group["project"]["name"], "source": "git", "url": repo}
        if versioned:
            block["version"] = branch
        if branch:
            block["ref"] = branch
        block["files"] = _collapsed(group["masks"]) if not group["template"] else sorted(group["masks"])
        # Clone only the files the block reads.
        block["sparse"] = sparse_patterns(block["files"], group["template"])
        if FORMATS[weblate_format]:
            block["format"] = FORMATS[weblate_format]
        if group["template"]:
            block["template"] = group["template"]
        if segment:
            block["segment"] = True
        if licence:
            block["licence"] = licence
        try:
            check_block(block)
        except ValueError as e:
            skip("not a valid block: %s" % str(e).split(": ", 1)[-1])
            names.release(name)
            continue
        found.append(block)
    return found, skipped
