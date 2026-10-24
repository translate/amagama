"""Translate Toolkit readers by name, for files whose extension doesn't say.

Translate Toolkit picks a reader by file extension, and doesn't know .json
or knows it as something else. A block's format names the reader instead.
Only bilingual formats are here: a monolingual file (Android, PHP, plain
JSON) holds no English, and needs pairing with its template first.
"""

import importlib

from translate.storage import factory

FORMATS = {
    "po": ("translate.storage.po", "pofile"),
    "mo": ("translate.storage.mo", "mofile"),
    "ts": ("translate.storage.ts2", "tsfile"),
    "tmx": ("translate.storage.tmx", "tmxfile"),
    "tbx": ("translate.storage.tbx", "tbxfile"),
    "xliff": ("translate.storage.xliff", "xlifffile"),
    "csv": ("translate.storage.csvl10n", "csvfile"),
    "nextcloud-json": ("translate.storage.jsonl10n", "NextcloudJsonFile"),
}


def check_format(name):
    if name not in FORMATS:
        raise ValueError("format must be one of %s" % ", ".join(sorted(FORMATS)))


def open_store(path, format=None):
    """Read path with the named reader, or the one its extension picks."""
    if format is None:
        return factory.getobject(path)
    module, name = FORMATS[format]
    store = getattr(importlib.import_module(module), name)()
    store.filename = path
    with open(path, "rb") as f:
        # Not store_class(f): NextcloudJsonFile then fails on _metadata.
        store.parse(f.read())
    return store
