#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import functools
import http.server
import threading

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_update import po
from amagama.tests.test_updater import block, write_tarball


class NoFingerprintHandler(http.server.SimpleHTTPRequestHandler):
    """A server that, like Pontoon's TMX export, sends no ETag or Last-Modified."""

    def send_header(self, keyword, value):
        if keyword not in ("Last-Modified", "ETag"):
            super().send_header(keyword, value)

    def log_message(self, *args):
        pass


@pytest.fixture
def site(tmp_path):
    root = tmp_path / "site"
    write_tarball(str(root / "af.tgz"), {"af/bash.af.po": po("af", ("Save file", "Stoor lêer"))})
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(NoFingerprintHandler, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield root, "http://127.0.0.1:%d/" % server.server_address[1]
    server.shutdown()
    server.server_close()


@pytest.fixture
def update(amagama_config, tmp_path, site):
    root, url = site

    def update(max_age_days, *args):
        sources = tmp_path / "sources.toml"
        sources.write_text(block("tp", "translationproject", base_url=url,
                                 max_age_days=max_age_days))
        amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                       UPDATER_WORKERS=1)
        return CliRunner().invoke(manage, ("update", "-s", "en") + args)
    return update


def lookup(source):
    app = amagama_server_factory()
    with app.test_client() as client:
        return [c["target"] for c in client.get("/api/v1/en/af/unit/%s" % source).get_json()]


def old_copy(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_namespace WHERE nspname = '_old_en'")
        found = cursor.fetchone() is not None
    pg_connection.commit()
    return found


def test_not_refetched_within_max_age_days(update):
    assert update(30).exit_code == 0
    result = update(30)
    assert result.output == "Nothing changed.\n"


def test_same_content_skips_rebuild(update, pg_connection):
    assert update(0).exit_code == 0
    result = update(0)
    assert result.exit_code == 0, result.output
    assert "Fetched tp: 1 files, as last time." in result.output
    assert result.output.endswith("Nothing changed.\n")
    assert not old_copy(pg_connection)


def test_changed_content_rebuilds(update, site, pg_connection):
    root, url = site
    assert update(0).exit_code == 0
    write_tarball(str(root / "af.tgz"), {"af/bash.af.po": po("af", ("Save file", "Stoor"))})
    result = update(0)
    assert result.exit_code == 0, result.output
    assert "Fetched tp: 1 files." in result.output
    assert lookup("Save file") == ["Stoor"]
    assert old_copy(pg_connection)


def test_force_rebuilds_same_content(update, pg_connection):
    assert update(0).exit_code == 0
    result = update(0, "--force")
    assert "Imported 1 files" in result.output
    assert old_copy(pg_connection)
