#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Read a file with each reader the updater uses, as an import does, then
use its units: the fuzz_readers_* targets, one per family of formats.

The input is a reader's name, a newline, then the file; a monolingual file
is its own English template. Reading may fail with any exception: the import
records it and skips the file. Using the units of a store that was read
mustn't fail.
"""

import importlib
import logging
import os
import tempfile
import types

import atheris

with atheris.instrument_imports():
    from amagama import tmdb
    from amagama.updater import formats, pairing

    for module, _, _ in [*formats.FORMATS.values(), *formats.MONOLINGUAL.values()]:
        importlib.import_module(module)

# name: (format, extension, open_store options)
READERS = {
    **{name: (name, ".txt", {}) for name in [*formats.FORMATS, *formats.MONOLINGUAL]},
    # Read by extension, as an import with no format does.
    "po": (None, ".po", {}),
    "xliff": (None, ".xlf", {}),
    "xliff-states": ("xliff", ".xlf", {"xliff_states": ["translated", "final"]}),
    "tmx": (None, ".tmx", {}),
    "ts": (None, ".ts", {}),
    "csv-columns": ("csv", ".csv", {"columns": ["source", "target"], "header_row": True}),
    "strings": ("strings", ".strings", {}),
    "po-mono": ("po-mono", ".po", {}),
}
PIECES = {"po-pieces": ".po", "tmx-pieces": ".tmx", "xliff-pieces": ".xlf"}
NAMES = sorted([*READERS, *PIECES])

# Each family is a fuzz target, so the time goes to the readers.
FAMILIES = {
    "xml": ["android", "compose", "resx", "tbx", "tmx", "tmx-pieces", "ts", "xliff",
            "xliff-monolingual", "xliff-pieces", "xliff-states", "xwiki-page"],
    "text": ["csv", "csv-columns", "dtd", "joomla-ini", "laravel", "mo", "php", "po",
             "po-mono", "po-pieces", "properties", "properties-utf16", "properties-utf8",
             "strings", "xwiki-properties"],
    "data": ["arb", "fluent", "json", "nextcloud-json", "ruby-yaml", "webextension", "yaml"],
}

IMPORTER = types.SimpleNamespace(max_import_len=2000, _translated=tmdb.TMDB._translated,
                                 _indexable_string=tmdb.TMDB._indexable_string)
DIRECTORY = tempfile.mkdtemp()
# The PHP reader's parser takes seconds to build: build it before fuzzing.
formats._reader("translate.storage.php").make_parser()
EXCLUDE_IDS = ["test.*", "*_desc"]


def use(store):
    """What add_store() does with each unit."""
    for unit in store.units:
        if tmdb.TMDB._usable_unit(IMPORTER, unit):
            tmdb._encodable(str(unit.source))
            tmdb._encodable(str(unit.target))


def read(name, data):
    """The stores the reader gives for data, or [] if it refuses it."""
    if name in PIECES:
        path = os.path.join(DIRECTORY, "input" + PIECES[name])
        with open(path, "wb") as f:
            f.write(data)
        try:
            return list(formats.open_pieces(path, size=64) or [])
        except Exception:
            return []
    format, extension, options = READERS[name]
    path = os.path.join(DIRECTORY, "input" + extension)
    with open(path, "wb") as f:
        f.write(data)
    try:
        store = formats.open_store(path, format, **options)
        if formats.is_monolingual(format):
            template = formats.open_store(path, format, **options)
            store = pairing.paired_store(store, template, EXCLUDE_IDS,
                                         language_in_ids=name == "ruby-yaml")
        else:
            store = pairing.without_ids(store, EXCLUDE_IDS)
        store.getsourcelanguage()
        store.gettargetlanguage()
        store.getprojectstyle()
        return [store]
    except Exception:
        return []


def fuzz_one(names, data):
    """Read data with the reader it names, one of names, and use the units."""
    name, newline, rest = data.partition(b"\n")
    name = name.decode("ascii", "replace")
    if newline:
        data = rest
    if name not in names:
        name = names[len(name) % len(names)]
    for store in read(name, data):
        use(store)


def quiet():
    """Before fuzzing: it warns about each malformed escape, thousands of times a run."""
    logging.getLogger("translate.misc.quote").setLevel(logging.ERROR)
