"""Translate Toolkit readers by name, for files whose extension doesn't say.

Translate Toolkit picks a reader by file extension, and doesn't know .json
or knows it as something else. A block's format names the reader instead.
A monolingual file (Android, PHP, plain JSON) holds no English, and is
paired with its template first.
"""

import csv
import importlib
import io

from translate.storage import factory, xliff
from translate.storage.base import TranslationStore, TranslationUnit

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


CSV_COLUMNS = ("source", "target", "location", "")


# XLIFF 1.2 target states, and "none" for a target without one.
XLIFF_STATES = (
    "none", "new", "needs-translation", "needs-adaptation", "needs-l10n",
    "needs-review-translation", "needs-review-adaptation", "needs-review-l10n",
    "translated", "final", "signed-off",
)


def check_xliff_states(format, states):
    if states is None:
        return
    if format not in (None, "xliff"):
        raise ValueError("xliff_states is for XLIFF files only")
    if not isinstance(states, list) or not states or not all(
            isinstance(state, str) and (state in XLIFF_STATES or state.startswith("x-"))
            for state in states):
        raise ValueError("xliff_states must list XLIFF states (%s) or x- ones"
                         % ", ".join(XLIFF_STATES))


def _accepted_xliff(store, states):
    """The units approved, or whose target's state is in states."""
    accepted = TranslationStore()
    accepted.filename = store.filename
    for unit in store.units:
        target = unit.xmlelement.find(unit.namespaced("target"))
        if not unit.target or target is None:
            continue
        if unit.isapproved() or (target.get("state") or "none") in states:
            plain = TranslationUnit(unit.source)
            plain.target = unit.target
            accepted.addunit(plain)
    return accepted


def check_csv_options(format, columns, header_row):
    if columns is None and header_row is None:
        return
    if format != "csv":
        raise ValueError("columns and header_row are for format csv only")
    if columns is not None and (not isinstance(columns, list) or not all(
            column in CSV_COLUMNS for column in columns)
            or "source" not in columns or "target" not in columns):
        raise ValueError('columns must list "source", "target" and optionally '
                         '"location", with "" for columns to ignore')
    if header_row is not None and not isinstance(header_row, bool):
        raise ValueError("header_row must be true or false")
    if header_row and columns is None:
        raise ValueError("header_row needs columns")


def open_store(path, format=None, columns=None, header_row=False, xliff_states=None):
    """Read path with the named reader, or the one its extension picks.

    For CSV, columns names each column, and header_row drops the first row.
    For XLIFF, xliff_states lists the target states that count as translated.
    """
    if format == "csv" and columns:
        return _read_csv(path, columns, header_row)
    if format is None:
        store = factory.getobject(path)
    else:
        module, name, kwargs = FORMATS.get(format) or MONOLINGUAL[format]
        store = getattr(importlib.import_module(module), name)(**kwargs)
        store.filename = path
        with open(path, "rb") as f:
            # Not store_class(f): NextcloudJsonFile then fails on _metadata.
            store.parse(f.read())
    if xliff_states and isinstance(store, xliff.xlifffile):
        return _accepted_xliff(store, xliff_states)
    return store


def _read_csv(path, columns, header_row):
    # Translate Toolkit's reader guesses the dialect, and guesses wrong for
    # some files; with the columns given, read plain comma-separated values.
    store = TranslationStore()
    store.filename = path
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(io.StringIO(f.read())))
    for row in rows[1:] if header_row else rows:
        values = dict(zip(columns, row))
        if values.get("source"):
            unit = TranslationUnit(values["source"])
            unit.target = values.get("target", "")
            store.addunit(unit)
    return store
