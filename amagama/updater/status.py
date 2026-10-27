#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""How each block is doing: its last fetch, failures and last import, and
whether its repository still has what it reads."""

import datetime
import time

from amagama.updater import generate


def block_status(block, cache_dir, imported=None):
    """A block's status as a dict; imported maps block names to the units
    the last build imported from them. units is None for a block the last
    build didn't have."""
    state = block.state(cache_dir) or {}
    failures = block.failures(cache_dir) or {}
    fetched = state.get("fetched")
    age = None
    if fetched:
        age = (datetime.datetime.now(datetime.timezone.utc)
               - datetime.datetime.fromisoformat(fetched)).total_seconds() / 86400
    return {
        "name": block.name,
        "tags": sorted(block.tags),
        "fetched": fetched,
        "age_days": None if age is None else round(age, 1),
        "files": len(state["files"]) if state else None,
        "languages": len(state["languages"]) if state else None,
        "units": (imported or {}).get(block.name),
        "failures": failures.get("runs", 0),
        "error": failures.get("error"),
        "mirror": block.mirror_note(cache_dir),
    }


def is_empty(status):
    """Fetched or built, but with nothing to import."""
    return status["files"] == 0 or status["units"] == 0


def describe(status):
    """A status as one line, and a line for each note."""
    if status["fetched"] is None:
        line = "%s: never fetched" % status["name"]
    else:
        line = "%s: fetched %s days ago, %d files, %d languages" % (
            status["name"], status["age_days"], status["files"], status["languages"])
    if status["units"] is not None:
        line += ", %d units imported" % status["units"]
    lines = [line]
    if status["failures"]:
        lines.append("  failed %d runs in a row: %s" % (status["failures"], status["error"]))
    if status["mirror"]:
        lines.append("  " + status["mirror"])
    return lines


def tree_problems(block, tree=generate.tree):
    """What a git block's repository lacks of what the block reads: its ref,
    the files a mask matches, its template. Read from the mirror if it has one."""
    settings = block.fetcher.settings
    paths = tree(block.mirror or settings["url"], settings.get("ref"))
    if paths is None:
        return ["repository or ref %s not found" % settings.get("ref", "HEAD")]
    problems = []
    if block.template and not generate.has_template(block.template, paths):
        problems.append("template %s missing" % block.template)
    masks = block.selection.mask_texts
    present = set(generate.matching(masks, paths))
    problems += ["filemask %s matches nothing" % mask for mask in masks if mask not in present]
    return problems


def check_remote(blocks, pause=1, tree=generate.tree):
    """{name: problems} of the git blocks whose repository lacks something;
    each repository and ref is read once."""
    trees = {}

    def cached(url, ref):
        if (url, ref) not in trees:
            trees[url, ref] = tree(url, ref)
            time.sleep(pause)
        return trees[url, ref]
    found = {}
    for block in blocks:
        if block.source == "git":
            problems = tree_problems(block, cached)
            if problems:
                found[block.name] = problems
    return found
