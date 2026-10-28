#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Translate Toolkit readers by name, for files whose extension doesn't say.

Translate Toolkit picks a reader by file extension, and doesn't know .json
or knows it as something else. A block's format names the reader instead.
A monolingual file (Android, PHP, plain JSON) holds no English, and is
paired with its template first.
"""

import codecs
import csv
import functools
import importlib
import io
import os
import re

from lxml import etree
from translate.storage import factory, po, tmx, xliff
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
    # Java's traditional ISO-8859-1 (non-Latin text as \u escapes).
    "properties": ("translate.storage.properties", "propfile", {}),
    "properties-utf8": ("translate.storage.properties", "propfile", {"personality": "java-utf8"}),
    "properties-utf16": ("translate.storage.properties", "propfile", {"personality": "java-utf16"}),
    "joomla-ini": ("translate.storage.properties", "propfile", {"personality": "joomla"}),
    "json": ("translate.storage.jsonl10n", "JsonFile", {}),
    "fluent": ("translate.storage.fluent", "FluentFile", {}),
    "dtd": ("translate.storage.dtd", "dtdfile", {}),
    # Symfony's: each <source> is the message's id, the English is the
    # template's <target>.
    "xliff-monolingual": ("translate.storage.xliff", "xlifffile", {}),
}


# Chromium's .xtb translations, joined with their .grd English by grit id.
CHROMIUM = "xtb"

# A PO file bigger than this is read in pieces of about this size, since a
# reader holds the whole file (a 161 MB compendium takes 2.8 GB).
PIECE_SIZE = 8 * 1024 * 1024
# TMX takes far more memory per byte (8 MB of it takes 0.5 GB).
TMX_PIECE_SIZE = 1024 * 1024
# XLIFF takes about as much as TMX.
XLIFF_PIECE_SIZE = 1024 * 1024


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
    if header_row is not None and not isinstance(header_row, bool) and (
            not isinstance(header_row, str) or not header_row.strip()):
        raise ValueError("header_row must be true, false or the first cell of the header row")
    if header_row and columns is None:
        raise ValueError("header_row needs columns")


# gettext's domain directive, which msgcat keeps when merging files; the
# Translate Toolkit's PO parser rejects it, and amaGama keeps no domains.
_PO_DOMAIN = re.compile(rb'^domain[ \t]+"[^"\n]*"[ \t]*\r?\n', re.MULTILINE)


_PO_CHARSET = re.compile(rb"charset=([A-Za-z0-9._:-]+)")


def _po_entries(lines):
    """The entries of a PO file's lines, split at the blank lines between them."""
    entry = []
    for line in lines:
        if line.strip():
            entry.append(line)
        elif entry:
            yield entry
            entry = []
    if entry:
        yield entry


def _decodes(line, charset):
    try:
        line.decode(charset)
    except UnicodeDecodeError:
        return False
    return True


def _decodable(data):
    """data without what its header's charset can't decode, and the number of
    entries left out.

    msgcat merges files without converting them, so a file can hold text in
    another charset, often translators' names in comments. Comment lines
    that don't decode are dropped, as comments aren't imported; an entry
    whose text doesn't decode is left out. Nothing is decoded by guess.
    """
    entries = list(_po_entries(data.splitlines(keepends=True)))
    header = entries[0] if entries and _is_header(entries[0]) else []
    match = _PO_CHARSET.search(b"".join(header))
    try:
        charset = codecs.lookup(match.group(1).decode("ascii")).name if match else "utf-8"
    except LookupError:
        charset = "utf-8"
    kept, dropped = [], 0
    for entry in entries:
        lines = [line for line in entry
                 if not line.startswith(b"#") or _decodes(line, charset)]
        if entry is header or all(_decodes(line, charset) for line in lines):
            kept.append(b"".join(lines))
        else:
            dropped += 1
    return b"\n".join(kept), dropped


def _read_po(path, data):
    """The PO file's store; undecodable counts the entries left out."""
    data = _PO_DOMAIN.sub(b"", data)
    store = po.pofile()
    store.filename = path
    store.undecodable = 0
    try:
        store.parse(data)
    except UnicodeDecodeError:
        data, store.undecodable = _decodable(data)
        undecodable = store.undecodable
        store = po.pofile()
        store.filename = path
        store.undecodable = undecodable
        store.parse(data)
    return store


@functools.cache
def _php_parser(make_parser):
    parser = make_parser()
    return parser, [production.callable for production in parser.productions]


def _reader(module):
    """The reader module, with translate's PHP parser built once per process.

    The PHP reader builds a parser for each file, and phply rebuilds its
    tables each time it can't save them (a read-only site-packages): about
    2 seconds a file. The reader wraps the parser's productions on each use,
    so they're restored first.
    """
    module = importlib.import_module(module)
    if module.__name__ == "translate.storage.php" and not hasattr(module, "_amagama_parser"):
        make_parser = module.make_parser

        def parser():
            parser, callables = _php_parser(make_parser)
            for production, callable in zip(parser.productions, callables):
                production.callable = callable
            return parser
        module.make_parser = module._amagama_parser = parser
    return module


def open_store(path, format=None, columns=None, header_row=False, xliff_states=None):
    """Read path with the named reader, or the one its extension picks.

    For CSV, columns names each column, and header_row drops the first row,
    or as text everything up to the row it begins.
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
        store = getattr(_reader(module), name)(**kwargs)
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
    size = size or PIECE_SIZE
    header, piece, length = b"", [], 0
    with open(path, "rb") as f:
        for entry in _po_entries(f):
            if not header and not piece and _is_header(entry):
                header = b"".join(entry) + b"\n"
                continue
            piece.append(b"".join(entry) + b"\n")
            length += len(piece[-1])
            if length >= size:
                yield header + b"".join(piece)
                piece, length = [], 0
    if piece:
        yield header + b"".join(piece)


def _read_tmx(path, data):
    store = tmx.tmxfile()
    store.filename = path
    store.parse(data)
    return store


def tmx_pieces(path, size=None):
    """path in TMX files of about size bytes, each with path's <header>.

    The <tu> elements are streamed, so the whole file is never held.
    """
    size = size or TMX_PIECE_SIZE
    header, piece, length = b"", [], 0

    def wrap():
        return (b'<?xml version="1.0" encoding="UTF-8"?>\n<tmx version="1.4">' + header
                + b"<body>" + b"".join(piece) + b"</body></tmx>")
    for _, elem in etree.iterparse(path, tag=("{*}header", "{*}tu"), huge_tree=True):
        if etree.QName(elem).localname == "header":
            header = etree.tostring(elem, with_tail=False)
        else:
            piece.append(etree.tostring(elem, with_tail=False))
            length += len(piece[-1])
        elem.clear(keep_tail=False)
        while elem.getprevious() is not None:
            del elem.getparent()[0]
        if length >= size:
            yield wrap()
            piece, length = [], 0
    if piece:
        yield wrap()


def _read_xliff(path, data, xliff_states=None):
    store = xliff.xlifffile()
    store.filename = path
    store.parse(data)
    return _accepted_xliff(store, xliff_states) if xliff_states else store


def xliff_pieces(path, size=None):
    """path in XLIFF files of about size bytes, each with one <file>'s attributes.

    The <trans-unit> elements are streamed, so the whole file is never held.
    """
    size = size or XLIFF_PIECE_SIZE
    skeleton, piece, length = None, [], 0

    def wrap():
        before, after = skeleton
        return before + b"".join(piece) + after
    for event, elem in etree.iterparse(path, events=("start", "end"),
                                       tag=("{*}xliff", "{*}file", "{*}trans-unit"),
                                       huge_tree=True):
        name = etree.QName(elem).localname
        if event == "start":
            if name == "xliff":
                root = elem
            elif name == "file":
                # A piece holds the units of one <file>, under its attributes.
                if piece:
                    yield wrap()
                    piece, length = [], 0
                out = etree.Element(root.tag, root.attrib, nsmap=root.nsmap)
                body = etree.SubElement(etree.SubElement(out, elem.tag, elem.attrib),
                                        etree.QName(etree.QName(elem).namespace, "body"))
                body.append(etree.Comment("units"))
                skeleton = etree.tostring(out, xml_declaration=True,
                                          encoding="UTF-8").split(b"<!--units-->")
            continue
        if name != "trans-unit":
            continue
        piece.append(etree.tostring(elem, with_tail=False))
        length += len(piece[-1])
        elem.clear(keep_tail=False)
        while elem.getprevious() is not None:
            del elem.getparent()[0]
        if length >= size:
            yield wrap()
            piece, length = [], 0
    if piece:
        yield wrap()


# Readers for files too large to hold whole, by extension: (format, piece
# size setting, pieces, read one piece).
_PIECES = {
    ".po": ("po", "PIECE_SIZE", po_pieces, _read_po),
    ".tmx": ("tmx", "TMX_PIECE_SIZE", tmx_pieces, _read_tmx),
    ".xlf": ("xliff", "XLIFF_PIECE_SIZE", xliff_pieces, _read_xliff),
    ".xliff": ("xliff", "XLIFF_PIECE_SIZE", xliff_pieces, _read_xliff),
}


def open_pieces(path, format=None, columns=None, header_row=False, xliff_states=None,
                size=None):
    """open_store() for path in pieces, or None if path is read whole.

    Every piece is parsed once first, so a file that can't be read is
    reported before any of it is imported.
    """
    reader = _PIECES.get(os.path.splitext(path)[1])
    if reader is None or format not in (None, reader[0]):
        return None
    format, setting, pieces, read = reader
    if format == "xliff":
        read = functools.partial(read, xliff_states=xliff_states)
    size = size or globals()[setting]
    if os.path.getsize(path) <= size:
        return None
    for piece in pieces(path, size):
        read(path, piece)
    return (read(path, piece) for piece in pieces(path, size))


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
    if isinstance(header_row, str):
        # Text before the header row (a notice) isn't translations.
        start = next((n for n, row in enumerate(rows) if row[:1] == [header_row]), len(rows))
        rows = rows[start:]
    for row in rows[1:] if header_row else rows:
        values = dict(zip(columns, row))
        if values.get("source"):
            unit = TranslationUnit(values["source"])
            unit.target = values.get("target", "")
            store.addunit(unit)
    return store
