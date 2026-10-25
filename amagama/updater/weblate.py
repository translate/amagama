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
import os
import re
import tempfile
import time
import urllib.parse

from amagama.licences import normalise_licence
from amagama.updater.fetchers import FetchError, http_text, run_tool
from amagama.updater.filemask import compile_mask
from amagama.updater.sources import Block

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


def identity(url, ref, template, segment, licence):
    """What a block reads, for keeping its name when it's regenerated: not
    its format, so a corrected reader keeps the name."""
    try:
        licence = normalise_licence(licence) if licence else None
    except ValueError:
        pass
    return (_normal(url), ref or None, template or None, bool(segment), licence)


def previous_names(blocks):
    """{identity(): name} of loaded sources-file blocks, for blocks()."""
    return {identity(block.fetcher.settings.get("url", ""), block.fetcher.settings.get("ref"),
                     block.template, block.segment, block.licence): block.name
            for block in blocks if block.source == "git"}


def blocks(components, prefix, projects=("*",), docs=(), covered=(), taken=(), previous=None):
    """(blocks, skipped) for the components of projects matching the globs.

    Blocks are dicts of sources-file settings; skipped counts components
    left out, by reason. docs are globs of documentation, imported with
    segmentation, matched to a project's slug or project/component;
    covered are repositories other blocks already import, and taken their
    names; previous maps the identity() of blocks generated before to their
    names, which are kept. Each block is checked as the sources file would
    check it.
    """
    by_url = {component.get("url"): component for component in components}
    covered = {_normal(repo) for repo in covered}
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
            elif _normal(repo) in covered:
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
    names = set(taken)
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
    # Names identify imports: one generated before is kept, and a new one
    # doesn't depend on what else the project has, as a project's blocks are
    # told apart by a component.
    chosen = {}
    for key, group in groups.items():
        slug, repo, branch, weblate_format, _, licence, segment = key
        kept = previous and previous.get(identity(repo, branch, group["template"], segment, licence))
        if kept and kept not in names and kept not in chosen.values():
            chosen[key] = kept
    names |= set(chosen.values())
    for key, group in sorted(groups.items()):
        if key in chosen:
            continue
        name = base(*key[:3])
        if bases[name] > 1 or name in names:
            name += "-" + re.sub(r"[^\w.-]+", "-", min(group["components"]))
        unique, number = name, 2
        while unique in names:
            unique, number = "%s-%d" % (name, number), number + 1
        chosen[key] = unique
        names.add(unique)
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
        block["sparse"] = sorted({mask.replace("{lang}", "*") for mask in block["files"]}
                                 | ({group["template"]} if group["template"] else set()))
        if FORMATS[weblate_format]:
            block["format"] = FORMATS[weblate_format]
        if group["template"]:
            block["template"] = group["template"]
        if segment:
            block["segment"] = True
        if licence:
            block["licence"] = licence
        try:
            _check(block)
        except ValueError as e:
            skip("not a valid block: %s" % str(e).split(": ", 1)[-1])
            names.discard(name)
            continue
        found.append(block)
    return found, skipped


def _check(block):
    settings = dict(block)
    licence = settings.pop("licence", None)
    if licence is not None:
        licence = normalise_licence(licence)
    Block(settings.pop("name"), settings.pop("title"), settings.pop("source"),
          settings.pop("version", None), licence, **settings)


def _normal(repo):
    """A repository URL without scheme, credentials, trailing .git or /."""
    parts = urllib.parse.urlsplit(repo)
    return (parts.hostname or "") + re.sub(r"(\.git)?/*$", "", parts.path)


def toml(blocks, header, defaults=None):
    """blocks as sources-file text, after header's comment lines.

    defaults become the file's defaults table, left out of each block.
    """
    def settings(table):
        return ["%s = %s" % (key, json.dumps(value, ensure_ascii=False))
                for key, value in table.items()]
    lines = ["# " + line if line else "#" for line in header.splitlines()]
    defaults = defaults or {}
    if defaults:
        lines += ["", "[defaults]"] + settings(defaults)
    for block in blocks:
        lines += ["", "[[block]]"]
        lines += settings({key: value for key, value in block.items()
                           if key not in defaults or defaults[key] != value})
    return "\n".join(lines) + "\n"


def _tree(url, ref):
    """The file paths of url at ref (None: its default branch), or None if
    the repository or ref isn't there. Only the trees are downloaded."""
    with tempfile.TemporaryDirectory() as tmp:
        clone = os.path.join(tmp, "repo")
        args = ["clone", "--quiet", "--depth", "1", "--filter=blob:none", "--no-checkout"]
        if ref:
            args += ["--branch", ref]
        try:
            run_tool("git", *(args + [url, clone]))
            return run_tool("git", "-C", clone, "ls-tree", "-r", "--name-only", "HEAD",
                            trusted=clone).splitlines()
        except FetchError:
            return None


def verify(blocks, pause=1, tree=_tree):
    """(blocks, problems): blocks as their repositories have them now.

    Weblate's data can be out of date. A missing branch falls back to the
    default branch; masks matching nothing are dropped; a block left with
    none, or whose template is missing, is left out. problems counts by reason.
    """
    trees, kept, seen, problems = {}, [], set(), {}

    def paths(url, ref):
        if (url, ref) not in trees:
            trees[url, ref] = tree(url, ref)
            time.sleep(pause)
        return trees[url, ref]

    for block in blocks:
        ref = block.get("ref")
        found = paths(block["url"], ref)
        if found is None and ref:
            ref, found = None, paths(block["url"], None)
        if found is None:
            problems["repository or branch not found"] = problems.get("repository or branch not found", 0) + 1
            continue
        masks = [mask for mask in block["files"]
                 if any(compile_mask(mask).match(path) for path in found)]
        template = block.get("template")
        if template and not any(compile_mask(template, allow_lang=False).match(path) for path in found):
            masks = []
        key = (block["url"], ref, tuple(masks), template)
        if not masks or key in seen:
            reason = "duplicate after branch fallback" if masks else "filemask matches nothing"
            problems[reason] = problems.get(reason, 0) + 1
            continue
        seen.add(key)
        block = dict(block, files=masks)
        block["sparse"] = sorted({mask.replace("{lang}", "*") for mask in masks}
                                 | ({template} if template else set()))
        if ref != block.get("ref"):
            problems["branch gone, default branch used"] = problems.get(
                "branch gone, default branch used", 0) + 1
            block.pop("ref", None)
            block.pop("version", None)
        kept.append(block)
    return kept, problems
