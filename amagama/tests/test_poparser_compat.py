import pytest
from click.testing import CliRunner

from amagama import poparser_compat
from amagama.application import amagama_server_factory
from amagama.cli import manage
from translate.storage import factory, poparser


def po_bytes(charset, target):
    return (('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=%s\\n"\n\n'
             'msgid "Save file"\nmsgstr "' % charset).encode("ascii")
            + target.encode(charset) + b'"\n')


@pytest.mark.parametrize("charset, target", [
    ("ISO-8859-1", "Stoor die lêer"),
    ("KOI8-R", "Сохранить файл"),
    ("Big5", "儲存檔案"),
])
def test_legacy_charset(tmp_path, charset, target):
    path = tmp_path / "file.po"
    path.write_bytes(po_bytes(charset, target))
    store = factory.getobject(str(path))
    assert [unit.target for unit in store.units if unit.source] == [target]


def test_patched_only_where_needed():
    state_class = poparser.PoParseState
    patched = "_current_encoding" in state_class.__init__.__code__.co_names
    assert (poparser_compat._patch.__qualname__ in state_class.set_encoding.__qualname__) == patched


def test_import(amagama_config, tmp_path):
    path = tmp_path / "sv.po"
    path.write_bytes(po_bytes("ISO-8859-1", "Spara filen å"))
    runner = CliRunner()
    runner.invoke(manage, ("initdb", "-s", "en"))
    result = runner.invoke(manage, ("build_tmdb", "-s", "en", "-t", "sv", "-i", str(path)))
    assert result.exit_code == 0, result.output
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/sv/unit/Save file")
    assert [c["target"] for c in response.get_json()] == ["Spara filen å"]
