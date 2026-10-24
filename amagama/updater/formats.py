"""Translate Toolkit readers by name, for files whose extension doesn't say.

Translate Toolkit picks a reader by file extension, and doesn't know .json
or knows it as something else. A block's format names the reader instead.
A monolingual file (Android, PHP, plain JSON) holds no English, and is
paired with its template first.
"""

import importlib

from translate.storage import factory

FORMATS = {
    "po": ("translate.storage.po", "pofile", {}),
    "mo": ("translate.storage.mo", "mofile", {}),
    "ts": ("translate.storage.ts2", "tsfile", {}),
    "tmx": ("translate.storage.tmx", "tmxfile", {}),
    "tbx": ("translate.storage.tbx", "tbxfile", {}),
    "xliff": ("translate.storage.xliff", "xlifffile", {}),
    "csv": ("translate.storage.csvl10n", "csvfile", {}),
    "nextcloud-json": ("translate.storage.jsonl10n", "NextcloudJsonFile", {}),
}

# These hold one language each, keyed by id, and need their English template.
MONOLINGUAL = {
    "android": ("translate.storage.aresource", "AndroidResourceFile", {}),
    "php": ("translate.storage.php", "phpfile", {}),
    "properties": ("translate.storage.properties", "propfile", {}),
    "joomla-ini": ("translate.storage.properties", "propfile", {"personality": "joomla"}),
    "json": ("translate.storage.jsonl10n", "JsonFile", {}),
    "fluent": ("translate.storage.fluent", "FluentFile", {}),
    "dtd": ("translate.storage.dtd", "dtdfile", {}),
}


def check_format(name):
    if name not in FORMATS and name not in MONOLINGUAL:
        raise ValueError("format must be one of %s"
                         % ", ".join(sorted(list(FORMATS) + list(MONOLINGUAL))))


def is_monolingual(name):
    return name in MONOLINGUAL


def open_store(path, format=None):
    """Read path with the named reader, or the one its extension picks."""
    if format is None:
        return factory.getobject(path)
    module, name, kwargs = FORMATS.get(format) or MONOLINGUAL[format]
    store = getattr(importlib.import_module(module), name)(**kwargs)
    store.filename = path
    with open(path, "rb") as f:
        # Not store_class(f): NextcloudJsonFile then fails on _metadata.
        store.parse(f.read())
    return store
