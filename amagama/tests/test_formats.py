import subprocess

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_updater import block
from amagama.updater.formats import check_format, open_store

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
