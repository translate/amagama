"""Translate Toolkit readers by name, for files whose extension doesn't say.

Translate Toolkit picks a reader by file extension, and doesn't know .json
or knows it as something else. A block's format names the reader instead.
A monolingual file (Android, PHP, plain JSON) holds no English, and is
paired with its template first.
"""

import csv
import importlib
import io
import os
import re

from translate.storage import factory, po, xliff
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


# Chromium's .xtb translations, joined with their .grd English by grit id.
CHROMIUM = "xtb"

# A PO file bigger than this is read in pieces of about this size, since a
# reader holds the whole file (a 161 MB compendium takes 2.8 GB).
PO_PIECE_SIZE = 8 * 1024 * 1024


def check_format(name):
    if name not in FORMATS and name not in MONOLINGUAL and name != CHROMIUM:
        raise ValueError("format must be one of %s"
                         % ", ".join(sorted(list(FORMATS) + list(MONOLINGUAL) + [CHROMIUM])))


def is_monolingual(name):
    """Whether files of this format need their English template."""
    return name in MONOLINGUAL or name == CHROMIUM


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


# gettext's domain directive, which msgcat keeps when merging files; the
# Translate Toolkit's PO parser rejects it, and amaGama keeps no domains.
_PO_DOMAIN = re.compile(rb'^domain[ \t]+"[^"\n]*"[ \t]*\r?\n', re.MULTILINE)


def _read_po(path, data):
    store = po.pofile()
    store.filename = path
    store.parse(_PO_DOMAIN.sub(b"", data))
    return store


def open_store(path, format=None, columns=None, header_row=False, xliff_states=None):
    """Read path with the named reader, or the one its extension picks.

    For CSV, columns names each column, and header_row drops the first row.
    For XLIFF, xliff_states lists the target states that count as translated.
    """
    if format == "csv" and columns:
        return _read_csv(path, columns, header_row)
    if format in (None, "po") and path.endswith(".po"):
        with open(path, "rb") as f:
            store = _read_po(path, f.read())
    elif format is None:
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
    if format == "nextcloud-json":
        return _nextcloud_singulars(store)
    return store


def _is_header(entry):
    lines = [line.strip() for line in entry if not line.startswith(b"#")]
    return lines[:1] == [b'msgid ""'] and lines[1:2] != [] and lines[1].startswith(b"msgstr")


def po_pieces(path, size=None):
    """path in PO files of about size bytes, each starting with path's header.

    Entries are split at the blank lines between them.
    """
    size = size or PO_PIECE_SIZE
    header, piece, entry, length = b"", [], [], 0
    with open(path, "rb") as f:
        for line in f:
            if line.strip():
                entry.append(line)
                continue
            if not entry:
                continue
            if not header and not piece and _is_header(entry):
                header = b"".join(entry) + b"\n"
            else:
                piece.append(b"".join(entry) + b"\n")
                length += len(piece[-1])
            entry = []
            if length >= size:
                yield header + b"".join(piece)
                piece, length = [], 0
    if entry:
        piece.append(b"".join(entry) + b"\n")
    if piece:
        yield header + b"".join(piece)


def open_po_pieces(path, format=None, columns=None, header_row=False, xliff_states=None,
                   size=None):
    """open_store() for path in pieces, or None if path is read whole.

    Every piece is parsed once first, so a file that can't be read is
    reported before any of it is imported.
    """
    size = size or PO_PIECE_SIZE
    if format not in (None, "po") or not path.endswith(".po") or os.path.getsize(path) <= size:
        return None

    for piece in po_pieces(path, size):
        _read_po(path, piece)
    return (_read_po(path, piece) for piece in po_pieces(path, size))


_NEXTCLOUD_PLURAL = re.compile(r"_(.*)_::_.*_", re.DOTALL)


def _nextcloud_singulars(store):
    """Nextcloud keys a plural "_<singular>_::_<plural>_"; keep the singular."""
    plain = TranslationStore()
    plain.filename = store.filename
    for unit in store.units:
        match = _NEXTCLOUD_PLURAL.fullmatch(str(unit.source))
        target = getattr(unit.target, "strings", [unit.target])
        converted = TranslationUnit(match.group(1) if match else str(unit.source))
        converted.target = str(target[0]) if target else ""
        plain.addunit(converted)
    return plain


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
