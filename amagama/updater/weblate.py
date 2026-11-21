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
import urllib.parse

from amagama.updater.fetchers import FetchError, http_text
from amagama.updater.filemask import compile_mask
from amagama.updater.generate import Names, Pacer, check_block, identity, normal_url
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
    "xliff-template": "xliff-monolingual",
    "yaml": "yaml",
    "ruby-yaml": "ruby-yaml",
    "xwiki-java-properties": "xwiki-properties",
    "xwiki-page-properties": "xwiki-page",
    "strings": "strings",
    "strings-utf8": "strings",
    "arb": "arb",
    "po-mono": "po-mono",
    "laravel": "laravel",
    "cmp-resource": "compose",
    "webextension": "webextension",
    "resx": "resx",
}
MONOLINGUAL = {"aresource", "json", "json-nested", "fluent", "properties", "properties-utf8",
               "properties-utf16", "joomla", "php", "i18next", "i18nextv4", "xliff-template",
               "yaml", "ruby-yaml", "xwiki-java-properties", "xwiki-page-properties",
               "strings", "strings-utf8", "arb", "po-mono", "laravel", "cmp-resource",
               "webextension", "resx"}

# Hosts that no longer serve their repositories, though Weblate still lists them.
DEAD_HOSTS = {"pagure.io": "archived"}

# A repository with more single-file components than this is read whole,
# from the directory before the language.
COLLAPSE = 10


# Instances weblate_sources has generated blocks for.
GENERATED = {"hosted.weblate.org", "translate.fedoraproject.org", "translate.inkscape.org",
             "l10n.opensuse.org", "translate.lxqt-project.org", "l10n.xwiki.org"}


def components(url, pause=None, project=None):
    """All of a Weblate instance's components, or a project's, from its API;
    pages spaced as Pacer.of(pause)."""
    listing = "projects/%s/components" % urllib.parse.quote(project) if project else "components"
    page = "%s/api/%s/?page_size=1000" % (url.rstrip("/"), listing)
    found = []
    pacer = Pacer.of(pause)
    while page:
        pacer.wait(page)
        try:
            data = json.loads(http_text(page))
            found += data["results"]
        except (ValueError, KeyError) as e:
            raise FetchError("%s: %s" % (page, e))
        page = data.get("next")
    return found


def _with_encoding(component):
    """The component's Weblate format, naming a properties file's encoding,
    and xliff-template for XLIFF with a template, which Weblate reads as
    monolingual (each <source> an id).

    Newer Weblate keeps the encoding in file_format_params, not in the
    format's name.
    """
    weblate_format = component.get("file_format")
    if weblate_format == "xliff" and component.get("template"):
        return "xliff-template"
    if weblate_format == "properties":
        encoding = ((component.get("file_format_params") or {}).get("properties_encoding")
                    or "").lower().replace("_", "-")
        if encoding in ("utf-8", "utf8"):
            return "properties-utf8"
        if encoding.startswith(("utf-16", "utf16")):
            return "properties-utf16"
    return weblate_format


def _https(repo):
    """repo, an scp-style git@host:path as https."""
    match = re.fullmatch(r"[\w.-]+@([\w.-]+):(?!//)(.+)", repo)
    return "https://%s/%s" % match.groups() if match else repo


def _repository(component, by_url, branches=None):
    """(repo, branch) of a component, or (None, None).

    Follows a weblate:// link; an scp-style git@host:path becomes https; a
    repository only in Weblate (local:) is Weblate's git export of it. An
    empty branch is the one other components of the repository name, if
    they name one (older Weblate's linked components show none).
    """
    if (component.get("repo") or "").startswith("weblate://"):
        component = by_url.get(component.get("linked_component") or "", {})
    repo = _https(component.get("repo") or "")
    if repo.startswith("local:"):
        return (component.get("git_export") or None), None
    if "://" not in repo:
        return None, None
    branch = component.get("branch") or ""
    named = (branches or {}).get(repo, set())
    if not branch and len(named) == 1:
        branch = next(iter(named))
    return repo, branch


def _mask(filemask):
    """Weblate's filemask as a filemask: its first * is the language."""
    return filemask.replace("*", "{lang}", 1)


def _collapsed(masks, others=()):
    """masks as one "<dir>/{lang}/**/*<ext>" when they share both, unless it
    also matches the files of others' masks."""
    shapes = {(mask.split("{lang}")[0], "." + mask.rsplit(".", 1)[-1])
              for mask in masks if "{lang}/" in mask}
    if len(masks) <= COLLAPSE or len(shapes) != 1 or not all("{lang}/" in mask for mask in masks):
        return sorted(masks)
    prefix, extension = shapes.pop()
    collapsed = "%s{lang}/**/*%s" % (prefix, extension)
    pattern = compile_mask(collapsed)
    if any(pattern.match(path) for mask in others for path in _examples(mask, extension)):
        return sorted(masks)
    return [collapsed]


def _examples(mask, extension):
    """Paths mask matches: with its wildcards as "x", and as "x<extension>",
    which a wildcard extension (terms.*) matches too."""
    return [re.sub(r"\*\*/|\*\*|\*", lambda wildcard: "" if wildcard.group() == "**/" else part,
                   mask).replace("{lang}", "af") for part in ("x", "x" + extension)]


def readable(component):
    """Why amaGama can't import a component, or None if it can."""
    weblate_format = _with_encoding(component)
    licence = (component.get("license") or "").strip()
    if component.get("is_glossary") or weblate_format == "tbx":
        return "glossary (terminology)"
    # Older Weblate gives the source language per project only.
    source = component.get("source_language") or component["project"].get("source_language")
    if (source or {}).get("code") != "en":
        return "source not English"
    if weblate_format not in FORMATS:
        return "format %s" % weblate_format
    if "-ND-" in licence.upper():
        return "no-derivatives licence"
    if weblate_format in MONOLINGUAL and not component.get("template"):
        return "monolingual without a template"
    return None


def entry(component):
    """A readable component as the file dict repositories.candidates() takes."""
    weblate_format = _with_encoding(component)
    # A bilingual file's template is only its source file.
    template = (component.get("template") or None) if weblate_format in MONOLINGUAL else None
    return {"name": component["slug"], "mask": _mask(component["filemask"]),
            "format": FORMATS[weblate_format] or "po", "template": template, "source": None,
            "languages": {}}


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
    named = {}
    for component in components:
        repo = _https(component.get("repo") or "")
        if component.get("branch") and "://" in repo:
            named.setdefault(repo, set()).add(component["branch"])
    covered = {normal_url(repo) for repo in covered}
    # Each project's masks, so a folded mask leaves out others' files.
    groups, skipped, project_masks = {}, {}, {}

    def skip(reason):
        skipped[reason] = skipped.get(reason, 0) + 1

    for component in components:
        project = component["project"]
        if not any(fnmatch.fnmatchcase(project["slug"], glob) for glob in projects):
            continue
        weblate_format = _with_encoding(component)
        licence = (component.get("license") or "").strip()
        project_masks.setdefault(project["slug"], set()).add(_mask(component.get("filemask") or ""))
        if readable(component):
            skip(readable(component))
        else:
            repo, branch = _repository(component, by_url, named)
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
    for group in groups.values():
        group["files"] = _collapsed(
            group["masks"], project_masks[group["project"]["slug"]] - group["masks"]) \
            if not group["template"] else sorted(group["masks"])
    # A block of the same files keeps its name before one of the same identity.
    chosen = {}
    for same_files in (True, False):
        for key, group in groups.items():
            slug, repo, branch, weblate_format, _, licence, segment = key
            if key in chosen:
                continue
            kept = names.kept(identity(repo, branch, group["template"], segment, licence),
                              group["files"] if same_files else None)
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
        block["files"] = group["files"]
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
