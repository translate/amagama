#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage

PO = '''msgid ""
msgstr "Content-Type: text/plain; charset=UTF-8\\n"

msgid "translator-credits"
msgstr "Jan Jansen <jan@example.org>"

msgctxt "NAME OF TRANSLATORS"
msgid "Your names"
msgstr "Jan Jansen"

msgctxt "EMAIL OF TRANSLATORS"
msgid "Your emails"
msgstr "jan@example.org"

msgid "Save file"
msgstr "Stoor lêer"
'''


def test_credits_not_imported(amagama_config, tmp_path):
    path = tmp_path / "af.po"
    path.write_text(PO, encoding="utf-8")
    runner = CliRunner()
    runner.invoke(manage, ("initdb", "-s", "en"))
    result = runner.invoke(manage, ("build_tmdb", "-s", "en", "-t", "af", "-i", str(path)))
    assert result.exit_code == 0, result.output
    app = amagama_server_factory()
    with app.test_client() as client:
        for source in ("translator-credits", "Your names", "Your emails"):
            response = client.get("/api/v1/en/af/unit/%s" % source)
            assert response.status_code == 404 or response.get_json() == []
        assert [c["target"] for c in client.get("/api/v1/en/af/unit/Save file").get_json()] == [
            "Stoor lêer"]
