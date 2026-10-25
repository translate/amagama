import subprocess

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_updater import block
# The app reads PO files with this loaded.
import amagama.poparser_compat  # noqa: F401
from amagama.updater import formats
from amagama.updater.formats import check_format, open_pieces, open_store, po_pieces, tmx_pieces

SAMPLES = {
    "po": 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n\n'
          'msgid "Save file"\nmsgstr "Stoor lêer"\n',
    "ts": '<?xml version="1.0"?><!DOCTYPE TS><TS version="2.1" language="af"><context>'
          '<name>C</name><message><source>Save file</source>'
          '<translation>Stoor lêer</translation></message></context></TS>',
    "tmx": '<?xml version="1.0"?><tmx version="1.4"><header srclang="en" datatype="plaintext" '
           'segtype="sentence" adminlang="en" o-tmf="x" creationtool="x" '
           'creationtoolversion="1"/><body><tu><tuv xml:lang="en"><seg>Save file</seg></tuv>'
           '<tuv xml:lang="af"><seg>Stoor lêer</seg></tuv></tu></body></tmx>',
    "tbx": '<?xml version="1.0"?><martif type="TBX" xml:lang="en"><text><body><termEntry>'
           '<langSet xml:lang="en"><tig><term>Save file</term></tig></langSet>'
           '<langSet xml:lang="af"><tig><term>Stoor lêer</term></tig></langSet>'
           '</termEntry></body></text></martif>',
    "xliff": '<?xml version="1.0"?><xliff version="1.2" '
             'xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="x" '
             'source-language="en" target-language="af" datatype="plaintext"><body>'
             '<trans-unit id="1" approved="yes"><source>Save file</source>'
             '<target>Stoor lêer</target></trans-unit></body></file></xliff>',
    "csv": '"location","source","target"\n"","Save file","Stoor lêer"\n',
    "nextcloud-json": '{"translations": {"Save file": "Stoor lêer"}, '
                      '"pluralForm": "nplurals=2; plural=(n != 1);"}',
}


@pytest.mark.parametrize("format", sorted(SAMPLES))
def test_read_whatever_the_extension(tmp_path, format):
    path = tmp_path / "af.data"
    path.write_text(SAMPLES[format], encoding="utf-8")
    store = open_store(str(path), format)
    assert [(u.source, u.target) for u in store.units if u.source] == [("Save file", "Stoor lêer")]


def test_extension_without_format(tmp_path):
    path = tmp_path / "af.po"
    path.write_text(SAMPLES["po"], encoding="utf-8")
    assert [u.target for u in open_store(str(path)).units if u.source] == ["Stoor lêer"]


def test_unknown_format():
    with pytest.raises(ValueError, match="format must be one of android, csv, dtd"):
        check_format("yaml")


def test_update_with_format(amagama_config, tmp_path):
    """Nextcloud keeps l10n/<lang>.json beside a .js copy of each."""
    repo = tmp_path / "repo"
    (repo / "core/l10n").mkdir(parents=True)
    (repo / "core/l10n/af.json").write_text(SAMPLES["nextcloud-json"], encoding="utf-8")
    (repo / "core/l10n/af.js").write_text("OC.L10N.register('core', {});")

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                        "-C", str(repo)] + list(args), check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "l10n")
    sources = tmp_path / "sources.toml"
    sources.write_text(block("nextcloud", "git", url=repo.as_uri(),
                             files=["core/l10n/{lang}.json"], format="nextcloud-json"))
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                   UPDATER_WORKERS=1)
    result = CliRunner().invoke(manage, ("update", "-s", "en"))
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 1 units (0 skipped)." in result.output
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/af/unit/Save file")
    assert [c["target"] for c in response.get_json()] == ["Stoor lêer"]


@pytest.mark.parametrize("text, columns, header_row", [
    ("Save file,Stoor lêer\n", ["source", "target"], False),
    ("English,Afrikaans\nSave file,Stoor lêer\n", ["source", "target"], True),
    ("id,English,Afrikaans\n7,Save file,Stoor lêer\n", ["", "source", "target"], True),
    # Source in column 3, target in column 5; the rest ignored.
    ("id,area,English,note,Afrikaans,extra\n7,ui,Save file,button,Stoor lêer,x\n",
     ["", "", "source", "", "target"], True),
])
def test_csv_columns(tmp_path, text, columns, header_row):
    path = tmp_path / "af.csv"
    path.write_text(text, encoding="utf-8")
    store = open_store(str(path), "csv", columns=columns, header_row=header_row)
    assert [(u.source, u.target) for u in store.units] == [("Save file", "Stoor lêer")]


def test_update_csv(amagama_config, tmp_path):
    repo = tmp_path / "repo"
    (repo / "glossary").mkdir(parents=True)
    (repo / "glossary/af.csv").write_text("English,Afrikaans\nSave file,Stoor lêer\n",
                                          encoding="utf-8")

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                        "-C", str(repo)] + list(args), check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "csv")
    sources = tmp_path / "sources.toml"
    sources.write_text(block("glossary", "git", url=repo.as_uri(), files=["glossary/{lang}.csv"],
                             format="csv", columns=["source", "target"], header_row=True))
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                   UPDATER_WORKERS=1)
    result = CliRunner().invoke(manage, ("update", "-s", "en"))
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 1 units (0 skipped)." in result.output
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/af/unit/Save file")
        assert [c["target"] for c in response.get_json()] == ["Stoor lêer"]
        assert client.get("/api/v1/en/af/unit/English").status_code == 404 or \
            client.get("/api/v1/en/af/unit/English").get_json() == []


XLIFF = ('<?xml version="1.0"?><xliff version="1.2" '
         'xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="x" '
         'source-language="en" target-language="af" datatype="plaintext"><body>'
         '<trans-unit id="1"><source>No state</source><target>Geen</target></trans-unit>'
         '<trans-unit id="2"><source>Translated</source>'
         '<target state="translated">Vertaal</target></trans-unit>'
         '<trans-unit id="3"><source>Review</source>'
         '<target state="needs-review-translation">Hersien</target></trans-unit>'
         '<trans-unit id="4" approved="yes"><source>Approved</source>'
         '<target state="new">Goedgekeur</target></trans-unit>'
         '<trans-unit id="5"><source>Custom</source><target state="x-done">Klaar</target></trans-unit>'
         '<trans-unit id="6"><source>Empty</source><target state="translated"></target></trans-unit>'
         '</body></file></xliff>')


@pytest.mark.parametrize("states, expected", [
    (["translated", "final", "signed-off"], ["Translated", "Approved"]),
    (["none", "translated"], ["No state", "Translated", "Approved"]),
    (["x-done"], ["Approved", "Custom"]),
])
def test_xliff_states(tmp_path, states, expected):
    path = tmp_path / "af.xlf"
    path.write_text(XLIFF, encoding="utf-8")
    for format in (None, "xliff"):
        store = open_store(str(path), format, xliff_states=states)
        assert [u.source for u in store.units] == expected
        assert all(u.istranslated() for u in store.units)


def test_nextcloud_plurals(tmp_path):
    path = tmp_path / "de.json"
    path.write_text('{"translations": {"Save file": "Datei speichern", '
                    '"_%n file_::_%n files_": ["%n Datei", "%n Dateien"]}, '
                    '"pluralForm": "nplurals=2; plural=(n != 1);"}', encoding="utf-8")
    store = open_store(str(path), "nextcloud-json")
    assert [(u.source, u.target) for u in store.units] == [
        ("Save file", "Datei speichern"), ("%n file", "%n Datei")]


PO_HEADER = b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=ISO-8859-1\\n"\n\n'
PO_ENTRIES = (
    b'msgid "Save file"\nmsgstr "Stoor l\xeaer"\n\n'
    # Starts like a header, but isn't one.
    b'msgid ""\n"Open file"\nmsgstr "Maak l\xeaer oop"\n\n'
    b'#, fuzzy\nmsgid "Close file"\nmsgstr "Sluit"\n\n'
    b'msgid "Quit"\nmsgstr "Verlaat"\n'
)


def sources(stores):
    return [str(u.source) for store in stores for u in store.units if not u.isheader()]


def test_po_pieces(tmp_path):
    path = tmp_path / "af.po"
    path.write_bytes(PO_HEADER + PO_ENTRIES)
    pieces = list(po_pieces(str(path), size=40))
    assert len(pieces) > 1
    assert all(piece.startswith(PO_HEADER) for piece in pieces)
    stores = list(open_pieces(str(path), size=40))
    assert sources(stores) == sources([open_store(str(path))])
    assert [str(u.target) for u in stores[0].units if not u.isheader()] == ["Stoor lêer", "Maak lêer oop"]


def test_small_po_read_whole(tmp_path):
    path = tmp_path / "af.po"
    path.write_bytes(PO_HEADER + PO_ENTRIES)
    assert open_pieces(str(path)) is None
    assert open_pieces(str(path), format="xliff", size=40) is None


def test_po_piece_unreadable(tmp_path):
    path = tmp_path / "af.po"
    path.write_bytes(PO_HEADER + PO_ENTRIES + b'\nmsgid "Broken\n')
    with pytest.raises(Exception):
        open_pieces(str(path), size=40)


def test_update_po_in_pieces(amagama_config, tmp_path, monkeypatch):
    monkeypatch.setattr(formats, "PIECE_SIZE", 40)
    repo = tmp_path / "repo"
    (repo / "po").mkdir(parents=True)
    (repo / "po/af.po").write_bytes(PO_HEADER + PO_ENTRIES)

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                        "-C", str(repo)] + list(args), check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "l10n")
    sources_toml = tmp_path / "sources.toml"
    sources_toml.write_text(block("af", "git", url=repo.as_uri(), files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources_toml), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                   UPDATER_WORKERS=1)
    result = CliRunner().invoke(manage, ("update", "-s", "en"))
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 3 units (1 skipped)." in result.output
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/af/unit/Open file")
    assert [c["target"] for c in response.get_json()] == ["Maak lêer oop"]


@pytest.mark.parametrize("size", [None, 40])
def test_po_domain_lines_ignored(tmp_path, size):
    """msgcat keeps gettext's domain lines; the parser doesn't accept them."""
    path = tmp_path / "af.po"
    path.write_bytes(PO_HEADER + b'domain "gmtp"\n\n' + PO_ENTRIES)
    stores = open_pieces(str(path), size=size) if size else [open_store(str(path))]
    assert sources(stores) == ["Save file", "Open file", "Close file", "Quit"]


def test_php_parser_built_once(tmp_path):
    # More files than Python's recursion limit: the reader wraps the
    # parser's productions on each use.
    for i in range(1100):
        path = tmp_path / "a.php"
        path.write_text("<?php\n$string['k%d'] = 'v%d';\n" % (i, i))
        assert [u.target or u.source for u in open_store(str(path), "php").units] == ["v%d" % i]
    assert formats._php_parser.cache_info().misses == 1


# A UTF-8 file with text merged in from Latin-1 files: a translator's name
# in the header's comments and in an entry's, and one entry's translation.
MIXED_PO = (b'# Jes\xfas, 2000.\nmsgid ""\nmsgstr ""\n'
            b'"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
            b'msgid "Save file"\nmsgstr "Stoor l\xc3\xaaer"\n\n'
            b'msgid "Open file"\nmsgstr "Maak l\xeaer oop"\n\n'
            b'#. Thanks to Fran\xe7ois\nmsgid "Quit"\nmsgstr "Verlaat"\n')


@pytest.mark.parametrize("size", [None, 40])
def test_po_undecodable_entries_left_out(tmp_path, size):
    path = tmp_path / "af.po"
    path.write_bytes(MIXED_PO)
    stores = list(open_pieces(str(path), size=size)) if size else [open_store(str(path))]
    assert [(str(u.source), str(u.target)) for store in stores for u in store.units
            if not u.isheader()] == [("Save file", "Stoor lêer"), ("Quit", "Verlaat")]
    assert sum(store.undecodable for store in stores) == 1


def test_po_latin1_declared_nothing_left_out(tmp_path):
    path = tmp_path / "af.po"
    path.write_bytes(MIXED_PO.replace(b"charset=UTF-8", b"charset=ISO-8859-1"))
    # Every byte decodes in Latin-1.
    assert open_store(str(path)).undecodable == 0


@pytest.mark.parametrize("size", [None, 40])
def test_update_undecodable_entries_skipped(amagama_config, tmp_path, monkeypatch, size):
    if size:
        monkeypatch.setattr(formats, "PIECE_SIZE", size)
    repo = tmp_path / "repo"
    (repo / "po").mkdir(parents=True)
    (repo / "po/af.po").write_bytes(MIXED_PO)
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m l10n"], cwd=repo, check=True)
    sources_toml = tmp_path / "sources.toml"
    sources_toml.write_text(block("af", "git", url=repo.as_uri(), files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources_toml), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                   UPDATER_WORKERS=1)
    result = CliRunner().invoke(manage, ("update", "-s", "en"))
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 2 units (1 skipped)." in result.output


def test_po_unknown_charset_read_as_utf8(tmp_path):
    """gettext's placeholder CHARSET, left in files never filled in."""
    path = tmp_path / "af.po"
    path.write_bytes(MIXED_PO.replace(b"charset=UTF-8", b"charset=CHARSET"))
    store = open_store(str(path))
    assert [str(u.source) for u in store.units if not u.isheader()] == ["Save file", "Quit"]
    assert store.undecodable == 1


TMX = (b'<?xml version="1.0" encoding="UTF-8"?>\n<tmx version="1.4">\n'
       b'<header srclang="en-US" datatype="plaintext" segtype="sentence" o-tmf="x" '
       b'adminlang="en" creationtool="t" creationtoolversion="1"></header>\n<body>\n%s</body>\n</tmx>\n')


def tmx_unit(tuid, source, target):
    return ('<tu tuid="%s" srclang="en-US"><tuv xml:lang="en-US"><seg>%s</seg></tuv>'
            '<tuv xml:lang="de"><seg>%s</seg></tuv></tu>\n' % (tuid, source, target)).encode()


def test_tmx_pieces(tmp_path):
    path = tmp_path / "de.tmx"
    units = [("a", "Save file", "Datei speichern"), ("b", "Open file", "Datei öffnen"),
             ("c", "Close", "Schließen"), ("d", "Quit", "Beenden"),
             # Left over for a last, smaller piece.
             ("e", "Help", "Hilfe")]
    path.write_bytes(TMX % b"".join(tmx_unit(*u) for u in units))
    assert len(list(tmx_pieces(str(path), size=200))) > 1
    stores = list(open_pieces(str(path), size=200))
    whole = open_store(str(path))
    assert [(u.getid(), str(u.source), str(u.target)) for s in stores for u in s.units] \
        == [(u.getid(), str(u.source), str(u.target)) for u in whole.units]
    assert open_pieces(str(path)) is None


def test_tmx_piece_unreadable(tmp_path):
    path = tmp_path / "de.tmx"
    path.write_bytes(TMX % (tmx_unit("a", "Save", "Speichern") * 10 + b"<tu><tuv>"))
    with pytest.raises(Exception):
        open_pieces(str(path), size=200)


@pytest.mark.parametrize("format, encoding", [("properties-utf8", "utf-8"),
                                              ("properties-utf16", "utf-16")])
def test_properties_encodings(tmp_path, format, encoding):
    path = tmp_path / "Bundle_ar.properties"
    path.write_bytes("open=فتح الملف\n".encode(encoding))
    assert [str(u.target or u.source) for u in open_store(str(path), format).units] == ["فتح الملف"]
