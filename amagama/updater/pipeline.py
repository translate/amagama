#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Rebuild a source language from the sources file and swap it in."""

import concurrent.futures
import functools
import json
import multiprocessing
import os
import tempfile

from flask import current_app
from amagama.commands import file_checksum
from amagama.tmdb import BUILD_PREFIX, OLD_PREFIX, lang_to_table
from amagama.updater import chromium, compressed
from amagama.updater.formats import CHROMIUM, open_pieces, open_store
from amagama.updater.pairing import paired_store, without_ids
from amagama.updater.segment import segmented
from amagama.updater.fetchers import FetchError
from amagama.updater.sources import FAILURES_TO_REPORT, limit_first_fetches, load_sources, select


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

    The cache keeps the file compressed (path + compressed.SUFFIX); it is
    read from a decompressed copy under the same name. Returns (units,
    skipped), or a reason the file can't be read; database errors raise.
    """
    args = (slang, tlang, block, licence, format, template, exclude_ids, reader_options, segment)
    if os.path.exists(path) or not os.path.exists(path + compressed.SUFFIX):
        return _import_file(path, path, *args)
    with tempfile.TemporaryDirectory() as tmp:
        plain = os.path.join(tmp, os.path.basename(path))
        try:
            compressed.decompress(path + compressed.SUFFIX, plain)
        except Exception as e:
            return "%s: %s" % (type(e).__name__, e)
        return _import_file(plain, path, *args)


def _import_file(path, recorded, slang, tlang, block, licence, format, template, exclude_ids,
                 reader_options, segment):
    """import_file() reading path, recorded as recorded."""
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
    # The row goes first, so the targets can link to it.
    iid = tmdb.start_import(slang, tlang, recorded, block, licence)
    units = skipped = 0
    for store in stores:
        # Entries left out for not decoding are skipped too.
        translatable = sum(1 for u in store.units if u.istranslatable()) + getattr(
            store, "undecodable", 0)
        if segment:
            whole = sum(1 for u in store.units if tmdb._usable_unit(u))
            store = segmented(store, slang, tlang, tmdb._usable_unit)
        added = tmdb.add_store(store, slang, tlang, commit=in_pieces, iid=iid)
        units += added
        # Sentence pairs add units; skipped counts the file's own.
        skipped += translatable - (whole if segment else added)
        # Let this piece go before the next is read.
        del store
    tmdb.finish_import(slang, iid, units, skipped, file_checksum(path))
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


def _marked(progress, func, task):
    """func(*task), with the file task[0] marked in progress while it runs."""
    mark = os.path.join(progress, str(os.getpid()))
    with open(mark, "w", encoding="utf-8") as f:
        f.write(task[0])
    try:
        return func(*task)
    finally:
        os.remove(mark)


def in_progress(progress):
    """The files left marked by a process killed while importing them, cleared."""
    if not os.path.isdir(progress):
        return []
    paths = []
    for name in sorted(os.listdir(progress)):
        mark = os.path.join(progress, name)
        with open(mark, encoding="utf-8") as f:
            paths.append(f.read())
        os.remove(mark)
    return paths


def import_files(tasks, workers, progress=None):
    """Import import_file() argument tuples; stop on the first error.

    With progress, a directory, each file is marked there while it is
    imported, so in_progress() names those a killed process left unfinished.
    """
    if progress is None:
        func = _import_task
    else:
        os.makedirs(progress, exist_ok=True)
        func = functools.partial(_marked, progress, import_file)
    if workers <= 1:
        return [func(task) for task in tasks]
    return in_workers(func, tasks, workers, _start_worker)


def built_from(blocks, cache_dir):
    """What a rebuild would read: {block name: its fetched content}."""
    states = ((block.name, block.state(cache_dir)) for block in blocks)
    return {name: state["content"] for name, state in states if state}


def update(slang, cache_dir, sources_path=None, workers=1, force=False, out=print,
           first_fetches=None, fetch=True, tags=None, exclude_tags=()):
    """Fetch changed blocks, rebuild slang from every block and swap it in.

    The blocks are those with any of tags (None: all) and none of
    exclude_tags.

    Without fetch, the cache is used as it is (fetch_sources keeps it
    current), and slang is rebuilt if it differs from what the last rebuild
    read. At most first_fetches blocks are fetched for the first time; the rest
    wait for later updates. A block that fails to fetch is skipped, and
    rebuilt from its last fetch if it has one. Returns {block name: its
    failures in a row} for those. Any other failure raises before the swap, leaving the live data
    as it was, and the next update rebuilds even if nothing has changed
    since.
    """
    slang = lang_to_table(slang)
    tmdb = current_app.tmdb
    blocks = select(load_sources(sources_path), tags, exclude_tags)
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
    failed = {}

    def checked(block):
        try:
            changed = block.changed(cache_dir)
        except FetchError as e:
            failed[block.name] = block.record_failure(cache_dir, e)
            return False
        if not changed:
            block.clear_failures(cache_dir)
        return changed
    if fetch:
        due, later = limit_first_fetches(blocks, cache_dir, first_fetches)
        if later:
            out("Blocks not fetched yet, left for later updates: %d." % later)
        changed = [block for block in due if checked(block)]
    else:
        changed = []
    # Fetches save their state before the rebuild; this marks one still owed.
    pending = os.path.join(cache_dir, ".rebuild-%s" % slang)
    was_pending = os.path.exists(pending)
    # What the last rebuild read, against the cache now.
    record = os.path.join(cache_dir, ".built-%s" % slang)
    try:
        with open(record, encoding="utf-8") as f:
            last_built = json.load(f)
    except (FileNotFoundError, ValueError):
        last_built = None
    cache_changed = not fetch and built_from(blocks, cache_dir) != last_built
    if not changed and not force and not was_pending and not cache_changed:
        out("Nothing changed.")
        report_failures(failed, out)
        return failed
    os.makedirs(cache_dir, exist_ok=True)
    open(pending, "w").close()
    same = []
    for block in changed:
        try:
            state = block.fetch(cache_dir)
        except FetchError as e:
            failed[block.name] = block.record_failure(cache_dir, e)
            continue
        block.clear_failures(cache_dir)
        same.append(state["same_as_last"])
        out("Fetched %s: %d files%s." % (block.name, len(state["files"]),
                                        ", as last time" if state["same_as_last"] else ""))
        for code, sources in state["merged_languages"].items():
            out("Codes merged in %s: %s -> %s" % (block.name, ", ".join(sources), code))
        if state["unknown_languages"]:
            out("Not language codes in %s, skipped: %s" % (
                block.name, ", ".join(state["unknown_languages"])))
        if state.get("outside_links"):
            out("Symlinks leading outside %s, skipped: %s" % (
                block.name, ", ".join(state["outside_links"])))
        if block.mirror_note(cache_dir):
            out("%s: %s" % (block.name, block.mirror_note(cache_dir)))
    if all(same) and not force and not was_pending and not cache_changed:
        os.remove(pending)
        out("Nothing changed.")
        report_failures(failed, out)
        return failed

    tmdb.use_schemas(BUILD_PREFIX)
    tmdb.drop_db([slang])
    tmdb.init_db([slang])
    tmdb.record_blocks(slang, [(block.name, block.title, block.version, block.weight)
                               for block in blocks])
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
        state = block.state(cache_dir)
        if state is None:
            if block in due:
                out("Nothing to import from %s: it has never been fetched." % block.name)
            continue
        directory = block.directory(cache_dir)
        block_tasks = [(os.path.join(directory, path), slang, lang, block.name, block.licence,
                        block.format, os.path.join(directory, *template) if template else None,
                        block.exclude_ids, block.reader_options, block.segment)
                       for path, lang, *template in state["files"]]
        # Files sharing an English template in a row, so each worker reads it once.
        tasks += sorted(block_tasks, key=lambda task: (task[6] or "", task[0]))
    progress = os.path.join(cache_dir, ".importing-%s" % slang)

    def names(paths):
        return ", ".join(os.path.relpath(path, cache_dir) for path in paths)
    stopped = in_progress(progress)
    if stopped:
        out("The last update stopped while importing %s." % names(stopped))
    try:
        results = import_files(tasks, workers, progress)
    except RuntimeError as e:
        stopped = in_progress(progress)
        if not stopped:
            raise
        raise RuntimeError("%s, while importing %s" % (e, names(stopped))) from e
    counts = [result for result in results if isinstance(result, tuple)]
    out("Imported %d files: %d units (%d skipped)." % (
        len(counts), sum(units for units, _ in counts), sum(skipped for _, skipped in counts)))
    unreadable = [(task[0], result) for task, result in zip(tasks, results)
                  if not isinstance(result, tuple)]
    if unreadable:
        out("Couldn't read %d files:" % len(unreadable))
        for path, reason in unreadable:
            out("  %s: %s" % (os.path.relpath(path, cache_dir), reason))

    tmdb.deploy(slang)
    tmdb.use_schemas(OLD_PREFIX)
    tmdb.drop_db([slang])
    tmdb.use_schemas("")
    tmdb.swap(slang)
    with open(record + ".new", "w", encoding="utf-8") as f:
        json.dump(built_from(blocks, cache_dir), f)
    os.replace(record + ".new", record)
    os.remove(pending)
    out("Swapped in '%s'; the replaced copy is %s%s." % (slang, OLD_PREFIX, slang))
    report_failures(failed, out)
    return failed


def report_failures(failed, out=print):
    """List the blocks that failed to fetch, calling out repeated failures."""
    if not failed:
        return
    out("Couldn't fetch %d blocks:" % len(failed))
    for name, failures in sorted(failed.items()):
        often = (" (%d runs in a row, since %s)" % (failures["runs"], failures["since"][:10])
                 if failures["runs"] >= FAILURES_TO_REPORT else "")
        out("  %s%s: %s" % (name, often, failures["error"]))
