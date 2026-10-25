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

from amagama.licences import normalise_licence
from amagama.updater.fetchers import FetchError, http_text
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


def blocks(components, prefix, projects=("*",), docs=(), covered=(), taken=()):
    """(blocks, skipped) for the components of projects matching the globs.

    Blocks are dicts of sources-file settings; skipped counts components
    left out, by reason. docs are globs of documentation, imported with
    segmentation, matched to a project's slug or project/component;
    covered are repositories other blocks already import, and taken their
    names. Each block is checked as the sources file would check it.
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
        weblate_format = component.get("file_format")
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
                                                "template": template})
                group["masks"].add(_mask(component["filemask"]))

    found = []
    names = set(taken)
    branches = {}
    for slug, repo, branch, *_ in groups:
        branches.setdefault((slug, repo), set()).add(branch)
    for (slug, repo, branch, weblate_format, _, licence, segment), group in sorted(
            groups.items()):
        base = slug if slug.startswith(prefix + "-") else "%s-%s" % (prefix, slug)
        # A project on several branches: named by branch, and a branch
        # that looks like a release (1.2.x) is its version.
        several = len(branches[slug, repo]) > 1 and branch
        if several:
            base += "-" + re.sub(r"[^\w.-]+", "-", branch)
        versioned = several and re.search(r"\d", branch)
        name, number = base, 2
        while name in names:
            name, number = "%s-%d" % (base, number), number + 1
        names.add(name)
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
