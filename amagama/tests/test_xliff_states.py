#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage

UNITS = [
    ("no-state", "", "<target>Geen</target>", False),
    ("approved", ' approved="yes"', "<target>Goedgekeur</target>", True),
    ("translated", "", '<target state="translated">Vertaal</target>', True),
    ("final", "", '<target state="final">Finaal</target>', True),
    ("signed-off", "", '<target state="signed-off">Afgeteken</target>', True),
    ("needs-review", "", '<target state="needs-review-translation">Hersien</target>', False),
    ("new", "", '<target state="new">Nuut</target>', False),
    ("empty", "", '<target state="translated"></target>', False),
]


@pytest.fixture
def imported(amagama_config, tmp_path):
    body = "".join('<trans-unit id="%s"%s><source>%s</source>%s</trans-unit>'
                   % (name, attributes, name, target) for name, attributes, target, _ in UNITS)
    path = tmp_path / "af.xlf"
    path.write_text('<?xml version="1.0"?><xliff version="1.2" '
                    'xmlns="urn:oasis:names:tc:xliff:document:1.2"><file original="x" '
                    'source-language="en" target-language="af" datatype="plaintext">'
                    '<body>%s</body></file></xliff>' % body, encoding="utf-8")
    runner = CliRunner()
    runner.invoke(manage, ("initdb", "-s", "en"))
    result = runner.invoke(manage, ("build_tmdb", "-s", "en", "-t", "af", "-i", str(path)))
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("name, attributes, target, expected", UNITS)
def test_states(imported, name, attributes, target, expected):
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/af/unit/%s" % name)
    found = response.status_code == 200 and any(
        c["source"] == name for c in response.get_json())
    assert found == expected
