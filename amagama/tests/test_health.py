#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import datetime
import importlib.util
import json
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "devsupport" / "health.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="no devsupport/ beside the package")

NOW = datetime.datetime(2026, 12, 2, 6, 47, tzinfo=datetime.timezone.utc)


@pytest.fixture
def health():
    spec = importlib.util.spec_from_file_location("health", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stats(built="2026-11-29T09:00:00.5Z", fetched="2026-12-02T04:30:00Z",
          counted="2026-12-02T00:30:04Z", failing=()):
    return {"version": 1, "sourceLanguages": {"en": {"built": built}},
            "use": {"computed": counted, "days": []},
            "fetch": {"finished": fetched, "blocks": 10, "waiting": 0, "failing": list(failing)}}


def test_healthy(health):
    assert health.problems(stats(), NOW) == []


def test_not_fetched(health):
    assert health.problems(None, NOW) == [
        "The statistics couldn't be fetched: the site or its API isn't answering."]


def test_old_rebuild(health):
    assert health.problems(stats(built="2026-11-22T09:00:00Z"), NOW) == [
        "No rebuild of `en` has swapped in since 2026-11-22: the last weekly rebuild failed, "
        "was skipped or is still running (`journalctl -u amagama-update`)."]


def test_rebuild_copying_every_block(health):
    """A rebuild that copied every block keeps an old built: its swap counts."""
    figures = stats(built="2026-11-01T09:00:00Z")
    figures["sourceLanguages"]["en"]["rebuild"] = {"swapped": "2026-11-29T11:16:07Z"}
    assert health.problems(figures, NOW) == []
    figures["sourceLanguages"]["en"]["rebuild"]["swapped"] = "2026-11-22T11:16:07Z"
    assert "swapped in since 2026-11-22" in health.problems(figures, NOW)[0]


def test_old_fetch_and_count(health):
    assert health.problems(stats(fetched="2026-11-28T04:30:00Z",
                                 counted="2026-11-29T00:30:00Z"), NOW) == [
        "No fetch has finished since 2026-11-28 (`journalctl -u amagama-fetch`).",
        "The lookups per day were last counted on 2026-11-29 (`journalctl -u amagama-stats`)."]


def test_failing_blocks(health):
    failing = [{"name": "gnome-51", "runs": 4, "since": "2026-11-28"},
               {"name": "kde", "runs": 9, "since": "2026-11-20"}]
    assert health.problems(stats(failing=failing), NOW) == [
        "2 blocks have failed to fetch 4 or more runs in a row (`amagama-manage sources_status "
        "--failing`): gnome-51 (since 2026-11-28), kde (since 2026-11-20)."]


def test_same_text_each_day(health):
    """A problem reads the same each day it lasts, so it isn't news daily."""
    old = stats(built="2026-11-22T09:00:00Z")
    assert health.problems(old, NOW) == health.problems(old, NOW + datetime.timedelta(days=1))


def test_older_server(health):
    """Statistics from before fetches were recorded aren't a problem."""
    figures = stats()
    del figures["fetch"]
    figures["use"] = None
    assert health.problems(figures, NOW) == []


def test_main(health, tmp_path, capsys):
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(stats(built="2026-11-22T09:00:00Z")))
    health.main(["health.py", str(path), "2026-12-02T06:47:00Z"])
    assert capsys.readouterr().out.startswith("- No rebuild of `en` has swapped in since ")
    health.main(["health.py", str(tmp_path / "missing.json")])
    assert capsys.readouterr().out == (
        "- The statistics couldn't be fetched: the site or its API isn't answering.\n")
    path.write_text(json.dumps(stats()))
    health.main(["health.py", str(path), "2026-12-02T06:47:00Z"])
    assert capsys.readouterr().out == ""
    # A time without a zone is UTC.
    health.main(["health.py", str(path), "2026-12-02T06:47:00"])
    assert capsys.readouterr().out == ""
