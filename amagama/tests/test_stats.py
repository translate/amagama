#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import json
import os

import pytest
from click.testing import CliRunner

from amagama import stats
from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_update import run, site, write_gnome  # noqa: F401 - fixtures
from amagama.tests.test_updater import write_stats


def figures_in(tmp_path):
    with open(tmp_path / "cache" / "stats.json", encoding="utf-8") as f:
        return json.load(f)


def get(path):
    app = amagama_server_factory()
    with app.test_client() as client:
        return client.get(path)


@pytest.fixture
def checked(run, site):  # noqa: F811 - fixture
    """A rebuild with one translation left out (printf) and one flagged (unchanged)."""
    root, url = site
    write_gnome(root, "af", ("Open file", "Maak lêer oop"), ("Open %s", "Maak oop"),
                ("Open the file now", "Open the file now"))
    result = run("update", "-s", "en", ENABLE_WEB_UI=True)
    assert result.exit_code == 0, result.output
    return result


class TestRebuild(object):

    def test_rebuild_computes_figures(self, checked, tmp_path, pg_connection):
        assert "Updated the statistics in %s." % (tmp_path / "cache" / "stats.json") \
            in checked.output
        figures = figures_in(tmp_path)
        en = figures["sourceLanguages"]["en"]
        # The live schema is the swapped-in, deploy_db-clustered build.
        assert sorted(en) == ["blocks", "built", "checks", "computed", "history", "languages",
                              "sources", "targets", "words"]
        assert (en["sources"], en["targets"], en["words"]) == (4, 4, 10)
        assert [(lang["code"], lang["targets"], lang["sources"], lang["words"], lang["coverage"])
                for lang in en["languages"]] == [
            ("af", 3, 3, 8, 0.75), ("zu", 1, 1, 2, 0.25)]
        assert [(b["name"], b["languages"], b["targets"]) for b in en["blocks"]] == [
            ("gnome-51", 2, 3), ("tp", 1, 1)]
        assert all(b["imported"].endswith("Z") for b in en["blocks"])
        assert en["built"][:19] == max(b["imported"] for b in en["blocks"])[:19]
        checks = en["checks"]
        assert "unchanged" in checks["flagged"]
        assert checks["failures"]["unchanged"] == 1
        assert en["languages"][0]["checks"]["unchanged"] == 1
        assert en["blocks"][0]["checks"] == {"unchanged": 1}
        assert checks["leftOutChecks"] == ["printf", "braces", "script"]
        assert checks["leftOut"] == {"targets": 1, "checks": {"printf": 1, "braces": 0,
                                                             "script": 0}}
        assert en["history"] == [{"built": en["built"], "sources": 4, "targets": 4, "words": 10,
                                  "languages": 2}]
        assert os.stat(tmp_path / "cache" / "stats.json").st_mode & 0o777 == 0o644

    def test_history_grows_with_rebuilds(self, checked, run, site, tmp_path):  # noqa: F811
        root, url = site
        write_gnome(root, "af", ("Open file", "Maak lêer oop"))
        write_stats(root, af=2, zu=1)
        assert run("update", "-s", "en").exit_code == 0
        history = figures_in(tmp_path)["sourceLanguages"]["en"]["history"]
        assert [entry["targets"] for entry in history] == [4, 3]
        assert history[0]["built"] < history[1]["built"]

    def test_update_stats_keeps_what_the_rebuild_left_out(self, checked, run, tmp_path):  # noqa: F811
        before = figures_in(tmp_path)["sourceLanguages"]["en"]
        result = run("update_stats")
        assert result.exit_code == 0, result.output
        assert "en: 4 sources, 4 targets in 2 languages, 2 blocks." in result.output
        after = figures_in(tmp_path)["sourceLanguages"]["en"]
        assert after["checks"]["leftOut"] == before["checks"]["leftOut"]
        # The same rebuild isn't added to the history again.
        assert after["history"] == before["history"]

    def test_failure_doesnt_fail_the_rebuild(self, run, monkeypatch, tmp_path):  # noqa: F811
        def broken(*args):
            raise OSError("disk full")
        monkeypatch.setattr(stats, "update_tm", broken)
        result = run("update", "-s", "en")
        assert result.exit_code == 0, result.output
        assert "Swapped in 'en'" in result.output
        assert "Couldn't update the statistics in %s: disk full" % (
            tmp_path / "cache" / "stats.json") in result.output

    def test_page(self, checked):
        response = get("/stats/")
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert "Volume: English — en" in page
        assert '<th scope="row" lang="af">Afrikaans — af</th>' in page
        assert "<code>unchanged</code>" in page
        assert "The last rebuild left out 1 translations that look broken:" in page
        assert "printf 1" in page
        assert "Lookups haven&#39;t been counted yet." in page
        assert response.headers["Vary"] == "Accept-Language"

    def test_api(self, checked, tmp_path):
        response = get("/api/v1/stats/")
        assert response.status_code == 200
        assert response.get_json() == figures_in(tmp_path)
        assert response.headers["Cache-Control"] == "max-age=3600, public"


class TestAbsent(object):

    def test_page_says_not_computed(self, amagama_config):
        amagama_config(ENABLE_WEB_UI=True, STATS_FILE="/nonexistent/stats.json")
        response = get("/stats/")
        assert response.status_code == 200
        assert "The statistics haven&#39;t been computed yet." in response.get_data(as_text=True)

    def test_api_not_found(self, amagama_config):
        amagama_config(STATS_FILE="/nonexistent/stats.json")
        response = get("/api/v1/stats/")
        assert response.status_code == 404
        assert response.get_json() == {"error": "The statistics haven't been computed yet."}

    def test_empty_database(self, amagama_config, tmp_path):
        amagama_config(ENABLE_WEB_UI=True, STATS_FILE=str(tmp_path / "stats.json"))
        result = CliRunner().invoke(manage, ["update_stats"])
        assert result.exit_code == 0, result.output
        page = get("/stats/").get_data(as_text=True)
        assert "The translation memory has no translations yet." in page

    def test_unreadable_file_is_absent(self, tmp_path):
        path = tmp_path / "stats.json"
        path.write_text("{not json", encoding="utf-8")
        assert stats.load(str(path)) is None
        path.write_text('{"version": 0}', encoding="utf-8")
        assert stats.load(str(path)) is None

    def test_main_page_links_to_it(self, amagama_config):
        amagama_config(ENABLE_WEB_UI=True)
        page = get("/?lang=af").get_data(as_text=True)
        assert '<a href="/stats/?lang=af">' in page


def test_path():
    assert stats.path({"STATS_FILE": "/srv/stats.json"}) == "/srv/stats.json"
    assert stats.path({"UPDATER_CACHE_DIR": "/srv/cache"}) == "/srv/cache/stats.json"


def test_cached_reads_a_changed_file(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text('{"version": 1, "computed": "a"}', encoding="utf-8")
    assert stats.cached(str(path))["computed"] == "a"
    path.write_text('{"version": 1, "computed": "bb"}', encoding="utf-8")
    assert stats.cached(str(path))["computed"] == "bb"


def test_history_keeps_the_last():
    previous = {"history": [{"built": "2026-01-%02dT00:00:00Z" % day} for day in range(1, 31)]
                + [{"built": "2026-02-%02dT00:00:00Z" % day} for day in range(1, 29)]}
    new = {"built": "2026-03-01T00:00:00Z", "sources": 1, "targets": 2, "words": 3,
           "languages": [{}]}
    history = stats._history(previous, new)
    assert len(history) == stats.HISTORY
    assert history[-1] == {"built": "2026-03-01T00:00:00Z", "sources": 1, "targets": 2,
                           "words": 3, "languages": 1}


LINE = ('%s - - [%s +0000] "%s %s HTTP/1.1" %d 2 "-" "%s" "%s"\n')


def log_line(time, path="/api/v1/en/af/unit/file", purpose="-", status=200,
             client="203.0.113.7", agent="Virtaal/1.1.0", method="GET"):
    return LINE % (client, time, method, path, status, agent, purpose)


@pytest.fixture
def logs(tmp_path):
    current = tmp_path / "live-nginx.access.log"
    current.write_text("".join([
        log_line("13/Nov/2026:23:59:59"),
        log_line("14/Nov/2026:10:00:00", client="2001:db8::42"),
        log_line("14/Nov/2026:10:00:01", purpose="prefetch"),
        log_line("14/Nov/2026:10:00:02", purpose="prefetch;bulk", status=429),
        log_line("14/Nov/2026:10:00:03", "/tmserver/en/af/unit/file"),
        # Not lookups.
        log_line("14/Nov/2026:10:00:04", "/api/v1/languages/"),
        log_line("14/Nov/2026:10:00:05", "/api/v1/stats/"),
        log_line("14/Nov/2026:10:00:06", "/static/css/amagama.css"),
        log_line("14/Nov/2026:10:00:07", method="PUT"),
        # A test instance's.
        log_line("14/Nov/2026:10:00:08", agent="Virtaal/1.1.0 Test/instance"),
    ]), encoding="utf-8")
    rotated = tmp_path / "live-nginx.access.log-20261113"
    rotated.write_text("".join([log_line("12/Nov/2026:08:00:00", client="198.51.100.9")] * 3
                               + [log_line("13/Nov/2026:00:00:00")]), encoding="utf-8")
    return tmp_path


class TestUse(object):

    def test_counts_lookups_per_day(self, amagama_config, logs, tmp_path):
        filename = tmp_path / "stats.json"
        amagama_config(ENABLE_WEB_UI=True, STATS_FILE=str(filename))
        result = CliRunner().invoke(manage, [
            "update_stats", "--only-use", "--logs", str(logs / "live-nginx.access.log*")])
        assert result.exit_code == 0, result.output
        assert "Lookups counted on 3 days from 2 logs." in result.output
        figures = stats.load(str(filename))
        assert figures["sourceLanguages"] == {}
        assert figures["use"]["days"] == [
            {"date": "2026-11-12", "ordinary": 3, "prefetch": 0, "bulk": 0, "limited": 0},
            {"date": "2026-11-13", "ordinary": 2, "prefetch": 0, "bulk": 0, "limited": 0},
            {"date": "2026-11-14", "ordinary": 2, "prefetch": 1, "bulk": 1, "limited": 1},
        ]
        page = get("/stats/").get_data(as_text=True)
        assert "<th scope=\"row\" class=\"date\">2026-11-14</th>" in page
        # Totals only: nothing about a client is kept or shown.
        for text in (filename.read_text(encoding="utf-8"), page,
                     get("/api/v1/stats/").get_data(as_text=True)):
            for client in ("203.0.113", "2001:db8", "198.51.100", "Virtaal"):
                assert client not in text

    def test_a_day_keeps_its_highest_counts(self, amagama_config, logs, tmp_path):
        filename = str(tmp_path / "stats.json")
        stats.update_use(filename, [str(logs / "live-nginx.access.log-20261113"),
                                    str(logs / "live-nginx.access.log")])
        # A later run reads only the current log, with part of the 13th.
        stats.update_use(filename, [str(logs / "live-nginx.access.log")])
        days = {day["date"]: day["ordinary"] for day in stats.load(filename)["use"]["days"]}
        assert days == {"2026-11-12": 3, "2026-11-13": 2, "2026-11-14": 2}

    def test_only_use_needs_logs(self, amagama_config):
        result = CliRunner().invoke(manage, ["update_stats", "--only-use"])
        assert result.exit_code == 2
        assert "--only-use needs --logs." in result.output

    def test_logs_must_match(self, amagama_config, tmp_path):
        result = CliRunner().invoke(manage, ["update_stats", "--logs", str(tmp_path / "x*")])
        assert result.exit_code == 2
        assert "No logs match" in result.output
