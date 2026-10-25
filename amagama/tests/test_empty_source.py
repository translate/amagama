#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage

# Qt's old convention: an empty <source>, the English in a <comment>.
TS = """<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE TS>
<TS version="2.1" language="it">
<context>
    <name>MainWindow</name>
    <message>
        <source></source>
        <comment>This is the application's main window.</comment>
        <translation>Questa è la finestra principale dell'applicazione.</translation>
    </message>
    <message>
        <source>Save file</source>
        <translation>Salva file</translation>
    </message>
</context>
</TS>
"""


def test_unit_without_source_is_skipped(amagama_config, tmp_path):
    path = tmp_path / "linguist_it.ts"
    path.write_text(TS, encoding="utf-8")
    runner = CliRunner()
    runner.invoke(manage, ("initdb", "-s", "en"))
    result = runner.invoke(manage, ("build_tmdb", "-s", "en", "-t", "it", "-i", str(path)))
    assert result.exit_code == 0, result.output
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/it/unit/Save file")
    assert [c["target"] for c in response.get_json()] == ["Salva file"]
