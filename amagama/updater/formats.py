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
import datetime
import functools
import gettext
import importlib
import inspect
import io
import os
import re

from lxml import etree
from translate.lang.data import cldr_plural_categories
from translate.misc.multistring import multistring
from translate.storage import factory, po, poheader, tmx, xliff
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

# Translate Toolkit stops reading YAML at 20,000 strings, against aliases
# expanding without end; these still stop that, past any real locale file.
YAML_LIMITS = {"max_yaml_units": 500_000, "max_yaml_nodes": 5_000_000}

# These hold one language each, keyed by id, and need their English template.
MONOLINGUAL = {
    "android": ("translate.storage.aresource", "AndroidResourceFile", {}),
    # Compose Multiplatform's Android-style resources, under composeResources/.
    "compose": ("translate.storage.aresource", "CMPResourceFile", {}),
    "php": ("translate.storage.php", "phpfile", {}),
    "laravel": ("translate.storage.php", "LaravelPHPFile", {}),
    # Java's traditional ISO-8859-1 (non-Latin text as \u escapes).
    "properties": ("translate.storage.properties", "propfile", {}),
    "properties-utf8": ("translate.storage.properties", "propfile", {"personality": "java-utf8"}),
    "properties-utf16": ("translate.storage.properties", "propfile", {"personality": "java-utf16"}),
    "joomla-ini": ("translate.storage.properties", "propfile", {"personality": "joomla"}),
    "json": ("translate.storage.jsonl10n", "JsonFile", {}),
    # Flutter's Application Resource Bundles, and WebExtensions' messages.json.
    "arb": ("translate.storage.jsonl10n", "ARBJsonFile", {}),
    "webextension": ("translate.storage.jsonl10n", "WebExtensionJsonFile", {}),
    "resx": ("translate.storage.resx", "RESXFile", {}),
    # PO keyed by id: each msgid an id, the English the template's msgstr.
    "po-mono": ("translate.storage.po", "pofile", {}),
    "fluent": ("translate.storage.fluent", "FluentFile", {}),
    "yaml": ("translate.storage.yaml", "YAMLFile", YAML_LIMITS),
    # Rails' locale files: each under its language's key, left out of ids.
    "ruby-yaml": ("translate.storage.yaml", "RubyYAMLFile", YAML_LIMITS),
    # XWiki's ISO-8859-1 properties, and its wiki pages holding them.
    "xwiki-properties": ("translate.storage.properties", "xwikifile", {}),
    "xwiki-page": ("translate.storage.properties", "XWikiPageProperties", {}),
    "dtd": ("translate.storage.dtd", "dtdfile", {}),
    # Apple's .strings, in UTF-16 or UTF-8: read as UTF-8 once decoded.
    "strings": ("translate.storage.properties", "stringsutf8file", {}),
    # Symfony's: each <source> is the message's id, the English is the
    # template's <target>.
    "xliff-monolingual": ("translate.storage.xliff", "xlifffile", {}),
}


# Chromium's .xtb translations, joined with their .grd English by grit id.
CHROMIUM = "xtb"

# A PO file bigger than this is read in pieces of about this size, since a
# reader holds the whole file (a 161 MB compendium takes 2.8 GB, an 8 MB
# piece about 180 MB, a 1 MB one 25 MB).
PIECE_SIZE = 1024 * 1024
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
    """The entries of a PO file's lines, split at the blank lines between them,
    and where a comment, msgctxt or msgid follows a msgstr."""
    entry, translated = [], False
    for line in lines:
        # An obsolete entry's lines are its keywords behind "#~".
        keyword = line[2:].lstrip() if line.startswith(b"#~") else line
        comment = line.startswith(b"#") and not keyword.startswith((b"msg", b'"'))
        if translated and (comment or keyword.startswith((b"msgctxt", b"msgid"))):
            yield entry
            entry, translated = [], False
        if line.strip():
            entry.append(line)
            translated = translated or keyword.startswith(b"msgstr")
        elif entry:
            yield entry
            entry, translated = [], False
    if entry:
        yield entry


def _newline(entry):
    """The line ending of entry's lines."""
    return b"\r\n" if entry[0].endswith(b"\r\n") else b"\n"


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
            kept.append(b"".join(lines) + _newline(entry))
        else:
            dropped += 1
    return b"".join(kept), dropped


def _without_obsolete(data):
    """data without its obsolete lines, and the entries left with only comments.

    Obsolete entries aren't imported, and Translate Toolkit reads one that
    stops short into the next entry.
    """
    kept = []
    for entry in _po_entries(data.splitlines(keepends=True)):
        lines = [line for line in entry if not line.startswith(b"#~")]
        if len(lines) == len(entry) or not all(line.startswith(b"#") for line in lines):
            kept.append(b"".join(lines) + _newline(entry))
    return b"".join(kept)


def _read_po(path, data):
    """The PO file's store; undecodable counts the entries left out."""
    # Translate Toolkit splits lines on the first msgid's ending only.
    data = _PO_DOMAIN.sub(b"", data).replace(b"\r\n", b"\n")
    if b"#~" in data:
        data = _without_obsolete(data)
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


# A message id that is a key, not English: words joined by dots, colons or
# underscores, with no spaces (worldedit.selection.cuboid.primary,
# start_button). Hyphens alone join English words (Double-click).
_KEY = re.compile(r"[A-Za-z][A-Za-z0-9-]*(?:[._:][A-Za-z0-9-]+)+")
# A PO file is keyed by id when this share of its translated messages (and
# at least KEYED_LEAST of them) have keys for msgids.
KEYED_SHARE = 0.8
KEYED_LEAST = 5


def _messages(store):
    return [unit for unit in store.units if not unit.isheader() and unit.source]


def keyed(store):
    """Whether a PO store's msgids are keys rather than English: PO keyed by
    id (po-mono), read as ordinary PO. Only translated messages count, and
    a key translated as itself is a name kept as it is (a file name, an
    option), not an id: its "translation" is no English."""
    translated = [unit for unit in _messages(store) if unit.target]
    keys = sum(1 for unit in translated
               if _KEY.fullmatch(str(unit.source)) and str(unit.target) != str(unit.source))
    return len(translated) >= KEYED_LEAST and keys >= KEYED_SHARE * len(translated)


def keyed_template(path, data):
    """Whether PO data is the English of PO keyed by id: keys for msgids
    (keyed()) and the English in most msgstrs."""
    store = _read_po(path, data)
    messages = _messages(store)
    return keyed(store) and sum(1 for unit in messages if unit.target) >= KEYED_SHARE * len(messages)


@functools.cache
def _php_parser(make_parser):
    parser = make_parser()
    return parser, [production.callable for production in parser.productions]


def _yaml_text(flatten):
    """YAMLFile._flatten, leaving out numbers, booleans, dates and binary
    values: it fails the whole file for most of them."""
    def text_only(self, data, *args, **kwargs):
        if isinstance(data, (int, float, datetime.date, bytes)):
            return iter(())
        return flatten(self, data, *args, **kwargs)
    return text_only


def _yaml_plurals(parse_dict):
    """RubyYAMLFile._parse_dict, leaving out a plural whose forms aren't all
    text: it makes the plural of them directly, which fails the whole file."""
    def text_plurals(self, data, prev):
        if data and all(key in cldr_plural_categories for key in data) and \
                not all(value is None or isinstance(value, str) for value in data.values()):
            return iter(())
        return parse_dict(self, data, prev)
    return text_plurals


def _fluent_first(parse):
    """FluentFile.parse, keeping a message or term's first definition, as
    Fluent's runtime does, instead of refusing the file for a later one. Only
    a file refused for that is parsed again."""
    def first_only(self, fluentsrc):
        try:
            return parse(self, fluentsrc)
        except ValueError as e:
            if "has the same id as a previous entry" not in str(e):
                raise
        from fluent.syntax import FluentSerializer, ast
        from fluent.syntax import parse as fluent_parse
        resource = fluent_parse(fluentsrc.decode("utf-8"))
        seen, kept = set(), []
        for entry in resource.body:
            if isinstance(entry, (ast.Message, ast.Term)):
                key = ("-" if isinstance(entry, ast.Term) else "") + entry.id.name
                if key in seen:
                    continue
                seen.add(key)
            kept.append(entry)
        resource.body = kept
        # The units the refused parse added go; junk stays, to be refused.
        self.units = []
        return parse(self, FluentSerializer(with_junk=True).serialize(resource).encode("utf-8"))
    return first_only


def _reader(module):
    """The reader module, with translate's PHP parser built once per process,
    its YAML reader taking only text, and its Fluent reader keeping a message's
    first definition.

    The PHP reader builds a parser for each file, and phply rebuilds its
    tables each time it can't save them (a read-only site-packages): about
    2 seconds a file. The reader wraps the parser's productions on each use,
    so they're restored first.
    """
    module = importlib.import_module(module)
    if module.__name__ == "translate.storage.fluent" and not hasattr(module, "_amagama_first"):
        module.FluentFile.parse = module._amagama_first = _fluent_first(module.FluentFile.parse)
    if module.__name__ == "translate.storage.yaml" and not hasattr(module, "_amagama_text"):
        module.YAMLFile._flatten = module._amagama_text = _yaml_text(module.YAMLFile._flatten)
        module.RubyYAMLFile._parse_dict = _yaml_plurals(module.RubyYAMLFile._parse_dict)
    if module.__name__ == "translate.storage.php" and not hasattr(module, "_amagama_parser"):
        make_parser = module.make_parser

        def parser():
            parser, callables = _php_parser(make_parser)
            for production, callable in zip(parser.productions, callables):
                production.callable = callable
            return parser
        module.make_parser = module._amagama_parser = parser
    return module


def _taken(reader, kwargs):
    """kwargs, less the YAML limits a Translate Toolkit before 3.20.2 hasn't:
    those reader's __init__ doesn't name, nor its parents' it passes **kwargs
    on to (3.21.0's RubyYAMLFile passes them on to YAMLFile)."""
    parameters = set()
    for cls in reader.__mro__:
        init = vars(cls).get("__init__")
        if init is None:
            continue
        try:
            signature = inspect.signature(init)
        except (TypeError, ValueError):
            break
        parameters.update(signature.parameters)
        # Only one taking **kwargs passes them on to its parent's.
        if not any(p.kind is p.VAR_KEYWORD for p in signature.parameters.values()):
            break
    return {key: value for key, value in kwargs.items()
            if key not in YAML_LIMITS or key in parameters}


def open_store(path, format=None, columns=None, header_row=False, xliff_states=None):
    """Read path with the named reader, or the one its extension picks.

    For CSV, columns names each column, and header_row drops the first row,
    or as text everything up to the row it begins.
    For XLIFF, xliff_states lists the target states that count as translated.
    """
    if format == "csv" and columns:
        return _read_csv(path, columns, header_row)
    if format in ("po", "po-mono") or format is None and path.endswith(".po"):
        with open(path, "rb") as f:
            store = _read_po(path, f.read())
    elif format == "strings":
        with open(path, "rb") as f:
            store = _read_strings(path, f.read())
    elif format is None:
        store = factory.getobject(path)
    else:
        module, name, kwargs = FORMATS.get(format) or MONOLINGUAL[format]
        reader = getattr(_reader(module), name)
        store = reader(**_taken(reader, kwargs))
        store.filename = path
        with open(path, "rb") as f:
            # Not store_class(f): NextcloudJsonFile then fails on _metadata.
            store.parse(f.read())
    _singulars_first(store)
    if xliff_states and isinstance(store, xliff.xlifffile):
        return _accepted_xliff(store, xliff_states)
    if format == "nextcloud-json":
        return _nextcloud_singulars(store)
    return store


def _strings_text(data):
    """A .strings file's text: UTF-16 with a byte order mark, or without one
    (NUL bytes say which order), else UTF-8."""
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode("utf-16")
    if b"\0" in data[:2]:
        return data.decode("utf-16-be" if data[:1] == b"\0" else "utf-16-le")
    return data.decode("utf-8-sig")


def _read_strings(path, data):
    # Translate Toolkit's reader guesses the encoding, and takes UTF-8 for
    # UTF-16 when it guesses wrong.
    module, name, kwargs = MONOLINGUAL["strings"]
    store = getattr(_reader(module), name)(**kwargs)
    store.filename = path
    store.parse(_strings_text(data).encode("utf-8"))
    return store


def _is_header(entry):
    lines = [line.strip() for line in entry if not line.startswith(b"#")]
    return lines[:1] == [b'msgid ""'] and lines[1:2] != [] and lines[1].startswith(b"msgstr")


def po_pieces(path, size=None):
    """path in PO files of about size bytes, each starting with path's header.

    Entries are split where _po_entries() splits them.
    """
    size = size or PIECE_SIZE
    header, piece, length = b"", [], 0
    with open(path, "rb") as f:
        for entry in _po_entries(f):
            # A domain line before the header would hide it.
            entry = [line for line in entry if not _PO_DOMAIN.match(line)]
            if not entry:
                continue
            if not header and not piece and _is_header(entry):
                header = b"".join(entry) + _newline(entry)
                continue
            piece.append(b"".join(entry) + _newline(entry))
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
    # As a whole file is read: one from po2xliff (datatype "po") with its
    # plural forms as one unit.
    store = xliff.xlifffile.parsestring(data)
    store.filename = path
    return _accepted_xliff(store, xliff_states) if xliff_states else store


def xliff_pieces(path, size=None):
    """path in XLIFF files of about size bytes, each with one <file>'s attributes.

    The <trans-unit> elements, and gettext plural groups, are streamed, so
    the whole file is never held.
    """
    size = size or XLIFF_PIECE_SIZE
    skeleton, piece, length = None, [], 0

    def wrap():
        before, after = skeleton
        return before + b"".join(piece) + after
    for event, elem in etree.iterparse(path, events=("start", "end"),
                                       tag=("{*}xliff", "{*}file", "{*}group", "{*}trans-unit"),
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
                # Raises for what the reader doesn't read, such as XLIFF 2.0,
                # which has no <trans-unit> to find.
                _read_xliff(path, b"".join(skeleton))
            continue
        # A gettext plural's forms are one message: their group goes whole.
        if name == "trans-unit":
            if elem.getparent().get("restype") == "x-gettext-plurals":
                continue
        elif name != "group" or elem.get("restype") != "x-gettext-plurals":
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


# Readers for files too large to hold whole, by extension, or by format
# whatever the extension: (format, piece size setting, pieces, read one
# piece).
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
    if format is None:
        reader = _PIECES.get(os.path.splitext(path)[1])
    else:
        reader = next((reader for reader in _PIECES.values() if reader[0] == format), None)
    if reader is None:
        return None
    format, setting, pieces, read = reader
    if format == "xliff":
        read = functools.partial(read, xliff_states=xliff_states)
    size = size or globals()[setting]
    if os.path.getsize(path) <= size:
        return None
    for piece in pieces(path, size):
        read(path, piece)
    return (_singulars_first(read(path, piece)) for piece in pieces(path, size))


def _singular_form(plural):
    """The form a Plural-Forms plural expression gives for n=1, or 0."""
    try:
        return int(gettext.c2py(plural)(1)) if plural else 0
    except (ValueError, ArithmeticError, RecursionError):
        return 0


def _singulars_first(store):
    """store, with each plural target's form for n=1 first, the one str()
    gives and so the one imported. Arabic's first form is for n=0."""
    if not isinstance(store, poheader.poheader):
        return store
    form = _singular_form(store.getheaderplural()[1])
    for unit in store.units if form else []:
        strings = [str(s) for s in getattr(unit.target, "strings", [])]
        if form < len(strings):
            unit.target = multistring([strings[form]] + strings[:form] + strings[form + 1:])
    return store


_NEXTCLOUD_PLURAL = re.compile(r"_(.*)_::_.*_", re.DOTALL)


def _nextcloud_singulars(store):
    """Nextcloud keys a plural "_<singular>_::_<plural>_"; keep the singular."""
    plain = TranslationStore()
    plain.filename = store.filename
    plural = poheader.plural_re.findall(str(store._metadata.get("pluralForm", "")))
    form = _singular_form(plural[0] if plural else None)
    for unit in store.units:
        match = _NEXTCLOUD_PLURAL.fullmatch(str(unit.source))
        target = getattr(unit.target, "strings", [unit.target])
        converted = TranslationUnit(match.group(1) if match else str(unit.source))
        converted.target = str(target[form if form < len(target) else 0]) if target else ""
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
