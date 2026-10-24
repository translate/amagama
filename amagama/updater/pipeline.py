"""Rebuild a source language from the sources file and swap it in."""

import multiprocessing
import os

from flask import current_app
from amagama.commands import file_checksum
from amagama.tmdb import BUILD_PREFIX, OLD_PREFIX, lang_to_table
from amagama.updater.formats import open_store
from amagama.updater.pairing import paired_store
from amagama.updater.sources import load_sources


def import_file(path, slang, tlang, block, licence, format=None, template=None, exclude_ids=()):
    """Import one file into the build schema.

    Returns (units, skipped), or a reason the file can't be read; database
    errors raise.
    """
    tmdb = current_app.tmdb
    if not lang_to_table(tlang):
        return "unknown language %s" % tlang
    try:
        store = open_store(path, format)
        if template is not None:
            if not os.path.exists(template):
                return "no English template %s" % os.path.basename(template)
            store = paired_store(store, open_store(template, format), exclude_ids)
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    units = tmdb.add_store(store, slang, tlang, commit=False)
    skipped = sum(1 for u in store.units if u.istranslatable()) - units
    tmdb.record_import(slang, tlang, path, file_checksum(path), units, skipped, block, licence)
    return units, skipped


def rebuildable(slang, blocks):
    """Whether the live schema holds only what these blocks re-import.

    False when it has data from elsewhere: a migrated database (no imports
    table), imports outside a block, or blocks no longer listed.
    """
    tmdb = current_app.tmdb
    if not tmdb._schema_exists(tmdb.get_cursor(), slang):
        return True
    cursor = tmdb.get_cursor(slang)
    cursor.execute("SELECT EXISTS(SELECT 1 FROM sources)")
    if not cursor.fetchone()[0]:
        return True
    names = {block.name for block in blocks if block.source != "snapshot"}
    summary = tmdb.import_summary(slang)
    return bool(summary) and all(row[0] in names for row in summary)


def _start_worker():
    from flask_caching import Cache

    from amagama.application import amagama_server_factory
    app = amagama_server_factory()
    app.app_context().push()
    app.tmdb.use_schemas(BUILD_PREFIX)
    app.cache = Cache(app, config={'CACHE_TYPE': 'SimpleCache', 'CACHE_THRESHOLD': 100000})


def _import_task(task):
    return import_file(*task)


def import_files(tasks, workers):
    """Import (path, slang, tlang, block, licence, format) tasks; stop on the first error."""
    if workers <= 1:
        return [import_file(*task) for task in tasks]
    with multiprocessing.get_context("spawn").Pool(workers, initializer=_start_worker) as pool:
        return pool.map(_import_task, tasks, chunksize=1)


def update(slang, cache_dir, sources_path=None, workers=1, force=False, out=print):
    """Fetch changed blocks, rebuild slang from every block and swap it in.

    Returns False when nothing changed (and force is off). A failure raises
    before the swap, leaving the live data as it was.
    """
    slang = lang_to_table(slang)
    tmdb = current_app.tmdb
    blocks = sorted(load_sources(sources_path), key=lambda block: block.id)
    # Without its snapshot, a rebuild would drop data that only the live
    # schema still has.
    cursor = tmdb.get_cursor()
    snapshots = [block for block in blocks if block.source == "snapshot"]
    missing = [block for block in snapshots if not tmdb._schema_exists(
        cursor, "_%s_%s" % (block.fetcher.settings["snapshot"], slang))]
    if missing and not rebuildable(slang, blocks):
        name = missing[0].fetcher.settings["snapshot"]
        raise ValueError("Snapshot _%s_%s doesn't exist: run snapshot_db -s %s --name %s first."
                         % (name, slang, slang, name))
    changed = [block for block in blocks if block.changed(cache_dir)]
    if not changed and not force:
        out("Nothing changed.")
        return False
    for block in changed:
        state = block.fetch(cache_dir)
        out("Fetched %s: %d files." % (block.name, len(state["files"])))
        if state["unknown_languages"]:
            out("Not language codes in %s, skipped: %s" % (
                block.name, ", ".join(state["unknown_languages"])))

    tmdb.use_schemas(BUILD_PREFIX)
    tmdb.drop_db([slang])
    tmdb.init_db([slang])
    # load_snapshot needs an empty schema, so snapshots go first.
    for block in snapshots:
        name = block.fetcher.settings["snapshot"]
        if block in missing:
            out("Skipped %s: no snapshot _%s_%s." % (block.name, name, slang))
            continue
        sources, skipped_sources, targets, skipped_targets = tmdb.load_snapshot(
            slang, name, block.name, block.licence)
        out("Loaded %s: %d targets (%d skipped)." % (block.name, targets, skipped_targets))

    tasks = []
    for block in blocks:
        if block.source == "snapshot":
            continue
        directory = block.directory(cache_dir)
        for path, lang, *template in block.state(cache_dir)["files"]:
            tasks.append((os.path.join(directory, path), slang, lang, block.name, block.licence,
                          block.format, os.path.join(directory, *template) if template else None,
                          block.exclude_ids))
    results = import_files(tasks, workers)
    counts = [result for result in results if isinstance(result, tuple)]
    out("Imported %d files: %d units (%d skipped)." % (
        len(counts), sum(units for units, _ in counts), sum(skipped for _, skipped in counts)))
    failed = [(task[0], result) for task, result in zip(tasks, results)
              if not isinstance(result, tuple)]
    if failed:
        out("Couldn't read %d files:" % len(failed))
        for path, reason in failed:
            out("  %s: %s" % (os.path.relpath(path, cache_dir), reason))

    tmdb.deploy(slang)
    tmdb.use_schemas(OLD_PREFIX)
    tmdb.drop_db([slang])
    tmdb.use_schemas("")
    tmdb.swap(slang)
    out("Swapped in '%s'; the replaced copy is %s%s." % (slang, OLD_PREFIX, slang))
    return True
