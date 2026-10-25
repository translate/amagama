"""Rebuild a source language from the sources file and swap it in."""

import concurrent.futures
import functools
import multiprocessing
import os

from flask import current_app
from amagama.commands import file_checksum
from amagama.tmdb import BUILD_PREFIX, OLD_PREFIX, lang_to_table
from amagama.updater import chromium
from amagama.updater.formats import CHROMIUM, open_pieces, open_store
from amagama.updater.pairing import paired_store, without_ids
from amagama.updater.segment import segmented
from amagama.updater.sources import load_sources


@functools.lru_cache(maxsize=16)
def _template(path, format, stamp):
    return open_store(path, format)


def template_store(path, format):
    """The English template, read once while the files pairing with it follow
    each other (update orders them so)."""
    stat = os.stat(path)
    return _template(path, format, (stat.st_mtime_ns, stat.st_size))


def import_file(path, slang, tlang, block, licence, format=None, template=None, exclude_ids=(),
                reader_options=None, segment=False):
    """Import one file into the build schema.

    Returns (units, skipped), or a reason the file can't be read; database
    errors raise.
    """
    tmdb = current_app.tmdb
    if not lang_to_table(tlang):
        return "unknown language %s" % tlang
    try:
        if template is not None and not os.path.exists(template):
            return "no English template %s" % os.path.basename(template)
        in_pieces = False
        if format == CHROMIUM:
            stores = [chromium.paired_store(path, template)]
        else:
            stores = None if template is not None else \
                open_pieces(path, format, **(reader_options or {}))
            # Every piece has been read already: commit each, so its locks
            # don't hold up other workers until the file is done.
            in_pieces = stores is not None
            if stores is None:
                store = open_store(path, format, **(reader_options or {}))
                if template is not None:
                    store = paired_store(store, template_store(template, format), exclude_ids)
                stores = [store]
            if template is None and exclude_ids:
                stores = (without_ids(store, exclude_ids) for store in stores)
    except Exception as e:
        return "%s: %s" % (type(e).__name__, e)
    units = skipped = 0
    for store in stores:
        # Entries left out for not decoding are skipped too.
        translatable = sum(1 for u in store.units if u.istranslatable()) + getattr(
            store, "undecodable", 0)
        if segment:
            whole = sum(1 for u in store.units if tmdb._usable_unit(u))
            store = segmented(store, slang, tlang, tmdb._usable_unit)
        added = tmdb.add_store(store, slang, tlang, commit=in_pieces)
        units += added
        # Sentence pairs add units; skipped counts the file's own.
        skipped += translatable - (whole if segment else added)
        # Let this piece go before the next is read.
        del store
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


def in_workers(func, tasks, workers, initializer=None):
    """[func(task) for task in tasks] in worker processes.

    A worker that dies (killed for lack of memory, say) raises instead of
    leaving its task unfinished forever.
    """
    try:
        with concurrent.futures.ProcessPoolExecutor(
                workers, mp_context=multiprocessing.get_context("spawn"),
                initializer=initializer) as pool:
            return list(pool.map(func, tasks))
    except concurrent.futures.process.BrokenProcessPool as e:
        raise RuntimeError("An import worker died, perhaps for lack of memory") from e


def import_files(tasks, workers):
    """Import import_file() argument tuples; stop on the first error."""
    if workers <= 1:
        return [import_file(*task) for task in tasks]
    return in_workers(_import_task, tasks, workers, _start_worker)


def update(slang, cache_dir, sources_path=None, workers=1, force=False, out=print):
    """Fetch changed blocks, rebuild slang from every block and swap it in.

    Returns False when nothing changed (and force is off). A failure raises
    before the swap, leaving the live data as it was, and the next update
    rebuilds even if nothing has changed since.
    """
    slang = lang_to_table(slang)
    tmdb = current_app.tmdb
    blocks = load_sources(sources_path)
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
    # Fetches save their state before the rebuild; this marks one still owed.
    pending = os.path.join(cache_dir, ".rebuild-%s" % slang)
    was_pending = os.path.exists(pending)
    if not changed and not force and not was_pending:
        out("Nothing changed.")
        return False
    os.makedirs(cache_dir, exist_ok=True)
    open(pending, "w").close()
    same = []
    for block in changed:
        state = block.fetch(cache_dir)
        same.append(state["same_as_last"])
        out("Fetched %s: %d files%s." % (block.name, len(state["files"]),
                                        ", as last time" if state["same_as_last"] else ""))
        for code, sources in state["merged_languages"].items():
            out("Codes merged in %s: %s -> %s" % (block.name, ", ".join(sources), code))
        if state["unknown_languages"]:
            out("Not language codes in %s, skipped: %s" % (
                block.name, ", ".join(state["unknown_languages"])))
    if same and all(same) and not force and not was_pending:
        os.remove(pending)
        out("Nothing changed.")
        return False

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
        block_tasks = [(os.path.join(directory, path), slang, lang, block.name, block.licence,
                        block.format, os.path.join(directory, *template) if template else None,
                        block.exclude_ids, block.reader_options, block.segment)
                       for path, lang, *template in block.state(cache_dir)["files"]]
        # Files sharing an English template in a row, so each worker reads it once.
        tasks += sorted(block_tasks, key=lambda task: (task[6] or "", task[0]))
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
    os.remove(pending)
    out("Swapped in '%s'; the replaced copy is %s%s." % (slang, OLD_PREFIX, slang))
    return True
