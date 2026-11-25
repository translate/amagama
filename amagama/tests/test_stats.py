#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import json
import os

import pytest
from click.testing import CliRunner

from amagama import stats, unit_checks
from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.updater import pipeline
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
                ("OPEN THE FILE NOW", "OPEN THE FILE NOW"))
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
                              "rebuild", "sources", "targets", "words"]
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
        assert sorted(checks["categories"]) == sorted(checks["flagged"] + checks["leftOutChecks"])
        assert checks["categories"]["printf"] == "critical"
        assert checks["categories"]["unchanged"] == "functional"
        assert checks["leftOut"] == {"targets": 1, "checks": {"printf": 1, "braces": 0,
                                                             "script": 0}}
        rebuild = en["rebuild"]
        assert rebuild["started"] <= rebuild["imported"] <= rebuild["swapped"]
        # Five units, one of which printf leaves out.
        assert {key: rebuild[key] for key in ("files", "units", "skipped", "unreadable",
                                              "copied_blocks")} == \
            {"files": 3, "units": 5, "skipped": 0, "unreadable": 0, "copied_blocks": 0}
        assert en["history"] == [{"built": en["built"], "sources": 4, "targets": 4, "words": 10,
                                  "languages": 2, "leftOut": checks["leftOut"],
                                  "rebuild": rebuild}]
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
        assert after["rebuild"] == before["rebuild"]
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

    def test_failure_leaves_the_connection_usable(self, amagama_config, monkeypatch, tmp_path):
        amagama_config()
        app = amagama_server_factory()

        def broken(filename, tmdb, *args):
            tmdb.get_cursor().execute("SELECT nonexistent")
        monkeypatch.setattr(stats, "update_tm", broken)
        out = []
        with app.app_context():
            pipeline.update_stats("en", str(tmp_path), None, out.append)
            cursor = app.tmdb.get_cursor()
            cursor.execute("SELECT 1")
            assert cursor.fetchone()[0] == 1
        assert out[0].startswith("Couldn't update the statistics in %s: column"
                                 % (tmp_path / "stats.json"))

    def test_page(self, checked):
        response = get("/stats/")
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert "Volume: English — en" in page
        assert '<th scope="row" lang="af">Afrikaans — af</th>' in page
        assert "<code>unchanged</code>" in page
        assert ("Translations the last rebuild left out because they look broken: 1 "
                "(braces 0, printf 1, script 0).") in page
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


def write_figures(filename, failures, categories=True):
    """A figures file of one source language whose af targets fail failures,
    {check: failures}."""
    checks = {"flagged": sorted(failures), "failures": failures, "leftOutChecks": ["printf"],
              "leftOut": {"targets": 1, "checks": {"printf": 1}}}
    if categories:
        checks["categories"] = stats.categories(checks["flagged"] + ["printf"])
    figures = {"version": stats.VERSION, "computed": "2026-11-14T00:00:00Z", "use": None,
               "sourceLanguages": {"en": {
                   "built": None, "sources": 10, "targets": 100, "words": 20, "history": [],
                   "languages": [{"code": "af", "targets": 100, "sources": 10, "words": 20,
                                  "coverage": 1.0, "checks": failures}],
                   "blocks": [], "checks": checks}}}
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(figures, f)


class TestCategories(object):

    def test_every_check_has_one(self):
        names = (list(unit_checks.CHECKS) + list(unit_checks.MEASURES)
                 + sorted(unit_checks.ttk_names()))
        assert {name: unit_checks.category(name) in unit_checks.CATEGORIES
                for name in names} == dict.fromkeys(names, True)

    def test_translate_toolkits(self):
        assert unit_checks.category("xmltags") == "critical"
        assert unit_checks.category("accelerators") == "functional"
        assert unit_checks.category("startcaps") == "cosmetic"
        assert unit_checks.category("untranslated") == "extraction"
        assert unit_checks.category("nonexistent") is None

    def test_page_groups_by_category(self, amagama_config, tmp_path):
        filename = tmp_path / "stats.json"
        amagama_config(ENABLE_WEB_UI=True, STATS_FILE=str(filename))
        write_figures(filename, {"startcaps": 50, "simplecaps": 60, "unchanged": 5, "tags": 1,
                                 "xmltags": 2, "urls": 0})
        page = get("/stats/").get_data(as_text=True)
        table = page[page.index("Critical: likely broken"):page.index("</table>",
                                                                       page.index("Critical"))]
        found = [line.split("<code>")[1].split("</code>")[0] for line in table.splitlines()
                 if "<code>" in line]
        assert found == ["xmltags", "tags", "unchanged", "simplecaps", "startcaps"]
        assert table.index("Critical: likely broken") < table.index("Functional: may not") \
            < table.index("Cosmetic: style")
        assert "Extraction" not in table
        assert '<th scope="rowgroup" colspan="3">Critical: likely broken</th>' in page
        assert "No translation fails these checks:" in page
        assert "printf 1" in page

    def test_file_without_categories(self, tmp_path):
        filename = tmp_path / "stats.json"
        write_figures(filename, {"startcaps": 5}, categories=False)
        checks = stats.load(str(filename))["sourceLanguages"]["en"]["checks"]
        assert checks["categories"] == {"startcaps": "cosmetic", "printf": "critical"}


def test_failed_write_keeps_the_file(tmp_path, monkeypatch):
    filename = tmp_path / "stats.json"
    write_figures(filename, {})
    before = filename.read_text(encoding="utf-8")

    def full(*args):
        raise OSError("disk full")
    monkeypatch.setattr(stats.os, "replace", full)
    with pytest.raises(OSError):
        stats.update_use(str(filename), [])
    assert filename.read_text(encoding="utf-8") == before
    assert sorted(os.listdir(tmp_path)) == ["stats.json", "stats.json.lock"]


@pytest.mark.parametrize("old", ['{"version": 2, "sourceLanguages": {}}', '{"version": 1, "sou'])
def test_unusable_file_is_kept(tmp_path, old):
    filename = tmp_path / "stats.json"
    (tmp_path / "stats.json.1").write_text("older", encoding="utf-8")
    filename.write_text(old, encoding="utf-8")
    stats.update_use(str(filename), [])
    assert (tmp_path / "stats.json.1").read_text(encoding="utf-8") == "older"
    assert (tmp_path / "stats.json.2").read_text(encoding="utf-8") == old
    assert stats.load(str(filename))["version"] == stats.VERSION
    # A file it can use isn't kept again.
    stats.update_use(str(filename), [])
    assert not (tmp_path / "stats.json.3").exists()


def test_unusable_file_is_kept_without_hard_links(tmp_path, monkeypatch):
    filename = tmp_path / "stats.json"
    filename.write_text("{", encoding="utf-8")

    def link(*args):
        raise PermissionError("Operation not permitted")
    monkeypatch.setattr(stats.os, "link", link)
    stats.update_use(str(filename), [])
    assert (tmp_path / "stats.json.1").read_text(encoding="utf-8") == "{"
    assert stats.load(str(filename))["version"] == stats.VERSION


def test_source_language_gone(tmp_path, monkeypatch):
    filename = tmp_path / "stats.json"
    write_figures(filename, {})
    figures = json.loads(filename.read_text(encoding="utf-8"))
    figures["sourceLanguages"]["fr"] = figures["sourceLanguages"]["en"]
    filename.write_text(json.dumps(figures), encoding="utf-8")

    class TMDB:
        source_langs = ["en"]
    monkeypatch.setattr(stats, "compute", lambda tmdb, slang: dict(
        figures["sourceLanguages"]["en"], built="2026-11-14T00:00:00Z", history=None))
    stats.update_tm(str(filename), TMDB())
    assert list(stats.load(str(filename))["sourceLanguages"]) == ["en"]


def test_words(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT %s FROM (VALUES ('Open file'), (E'\\tOpen  file\\n'), (''), ('  ')) "
                       "AS v(text)" % stats.WORDS)
        assert [row[0] for row in cursor.fetchall()] == [2, 2, 0, 0]


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

    def test_keeps_a_year_up_to_the_newest_day(self, tmp_path):
        old = tmp_path / "old.log"
        old.write_text(log_line("12/Nov/2025:10:00:00") + log_line("13/Nov/2025:10:00:00"),
                       encoding="utf-8")
        new = tmp_path / "new.log"
        new.write_text(log_line("13/Nov/2026:10:00:00"), encoding="utf-8")
        filename = str(tmp_path / "stats.json")
        stats.update_use(filename, [str(old), str(new)])
        assert [day["date"] for day in stats.load(filename)["use"]["days"]] == [
            "2025-11-13", "2026-11-13"]

    def test_only_use_needs_logs(self, amagama_config):
        result = CliRunner().invoke(manage, ["update_stats", "--only-use"])
        assert result.exit_code == 2
        assert "--only-use needs --logs." in result.output

    def test_logs_must_match(self, amagama_config, tmp_path):
        result = CliRunner().invoke(manage, ["update_stats", "--logs", str(tmp_path / "x*")])
        assert result.exit_code == 2
        assert "No logs match" in result.output


class TestCharts(object):

    def page(self, amagama_config, tmp_path, history, days):
        filename = tmp_path / "stats.json"
        amagama_config(ENABLE_WEB_UI=True, STATS_FILE=str(filename))
        write_figures(filename, {})
        figures = json.loads(filename.read_text(encoding="utf-8"))
        figures["sourceLanguages"]["en"]["history"] = [
            {"built": "2026-11-%02dT05:00:00.000000Z" % day, "sources": sources,
             "targets": targets, "words": 2 * sources, "languages": languages}
            for day, sources, targets, languages in history]
        figures["use"] = {"computed": "2026-11-14T00:00:00Z", "days": days}
        filename.write_text(json.dumps(figures), encoding="utf-8")
        return get("/stats/").get_data(as_text=True)

    def test_volume_and_use(self, amagama_config, tmp_path):
        page = self.page(amagama_config, tmp_path,
                         [(1, 8, 1000, 2), (8, 9, 1500, 3), (14, 10, 2500, 3)],
                         [{"date": "2026-11-%02d" % day, "ordinary": 1000, "prefetch": 10,
                           "bulk": 1, "limited": day % 2} for day in range(1, 15)])
        for key in ("targets", "sources", "languages"):
            assert ('role="img" aria-labelledby="chart-en-%s-title chart-en-%s-desc"'
                    % (key, key)) in page
        assert '<title id="chart-en-targets-title">Translations</title>' in page
        assert ('<desc id="chart-en-targets-desc">From 1 000 on 2026-11-01 to 2 500 on '
                '2026-11-14.</desc>') in page
        assert '<title id="chart-use-title">Lookups per day</title>' in page
        assert ('<desc id="chart-use-desc">14 154 lookups from 2026-11-01 to 2026-11-14, '
                '1 011 a day on average.</desc>') in page
        assert page.count('<rect class="ordinary"') == 14
        assert '<path class="stats-line limited"' in page
        assert "Refused (429)" in page
        # The numbers stay in the tables.
        assert '<th scope="row" class="date">2026-11-14</th>' in page

    def test_one_rebuild_and_one_day(self, amagama_config, tmp_path):
        page = self.page(amagama_config, tmp_path, [(14, 10, 2500, 3)],
                         [{"date": "2026-11-14", "ordinary": 1, "prefetch": 0, "bulk": 0,
                           "limited": 0}])
        assert "<svg" not in page
        assert "The charts of how the translation memory grows start after its next rebuild." \
            in page
        assert '<details class="stats-details" open>' in page

    def test_weeks(self, amagama_config, tmp_path):
        days = [{"date": (datetime.date(2026, 7, 1) + datetime.timedelta(days=n)).isoformat(),
                 "ordinary": n, "prefetch": 0, "bulk": 0, "limited": 0} for n in range(120)]
        page = self.page(amagama_config, tmp_path, [], days)
        assert '<title id="chart-use-title">Average lookups per day, by week</title>' in page
        assert ('<h3 class="stats-chart-title" aria-hidden="true">Average lookups per day, '
                'by week</h3>') in page
        assert page.count('<rect class="ordinary"') == 18
        assert "stats-line limited" not in page
        assert "Refused (429)</span>" not in page


def test_page_groups_blocks_by_title(amagama_config, tmp_path):
    filename = tmp_path / "stats.json"
    amagama_config(ENABLE_WEB_UI=True, STATS_FILE=str(filename))
    write_figures(filename, {"unchanged": 7})
    figures = json.loads(filename.read_text(encoding="utf-8"))

    def block(name, title, languages, targets, imported, checks, version=None):
        return {"name": name, "title": title, "version": version, "languages": languages,
                "targets": targets, "imported": imported, "checks": checks}
    # A project's components share its title; releases keep theirs apart.
    figures["sourceLanguages"]["en"]["blocks"] = [
        block("tor-browser", "Tor Project", 40, 300, "2026-11-01T00:00:00Z", {"unchanged": 2}),
        block("tor-support", "Tor Project", 55, 200, "2026-11-08T00:00:00Z", {"unchanged": 3}),
        block("tor-tails", "Tor Project", 20, None, None, {}),
        block("gnome-50", "GNOME", 90, 450, "2026-10-01T00:00:00Z", {}, "50"),
        block("gnome-51", "GNOME", 95, 600, "2026-10-02T00:00:00Z", {"unchanged": 2}, "51"),
    ]
    filename.write_text(json.dumps(figures), encoding="utf-8")
    page = get("/stats/").get_data(as_text=True)
    table = page[page.index('id="sources-en"'):page.index("</table>", page.index('id="sources-en"'))]
    rows = [[cell.split(">", 1)[1].split("<", 1)[0] for cell in row.split("<t")[1:]
             if cell.startswith(("h", "d")) and 'class="bar-column"' not in cell]
            for row in table.split("<tr>")[2:]]
    assert rows == [["GNOME 51", "95", "600", "2026-10-02"],
                    ["Tor Project", "55", "500", "2026-11-08"],
                    ["GNOME 50", "90", "450", "2026-10-01"]]
    # The checks by project add up too.
    checks = page[page.index("By project"):]
    assert '<th scope="row" dir="ltr">Tor Project</th><td class="number">5</td>' in checks
    # The API keeps one entry per block.
    api = get("/api/v1/stats/").get_json()
    assert len(api["sourceLanguages"]["en"]["blocks"]) == 5
