#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime
import email.message
import io
import json
import time
import types
import urllib.error
import urllib.parse

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_crowdin import crowdin_repository  # noqa: F401 (fixture)
from amagama.tests.test_linguas import repository  # noqa: F401 (fixture)
from amagama.tests.test_updater import block
from amagama.updater import discovery
from amagama.updater.fetchers import FetchError
from amagama.updater.sources import load_sources


def searcher(counts, pages=None):
    """A get() for search(): counts maps a size range to its total; each
    page gives repositories named by range and page."""
    asked = []

    def get(url, token):
        assert token == "t"
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        text, page = query["q"][0], int(query["page"][0])
        asked.append((text.rsplit("size:", 1)[1], page))
        size = text.rsplit("size:", 1)[1]
        total = counts.get(size, 0)
        shown = min(total - (page - 1) * discovery.PAGE, discovery.PAGE)
        return {"total_count": total, "incomplete_results": False,
                "items": [{"repository": {"full_name": "o/%s-%d-%d" % (size, page, n)}}
                          for n in range(max(shown, 0))]}
    return get, asked


class Clock(object):
    """discovery's time: sleeping moves monotonic(), as does each request."""

    def __init__(self, request=0):
        self.now, self.request, self.slept = 0.0, request, []

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def monotonic(self):
        return self.now


def test_search_splits_by_size(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(discovery, "time", clock)
    get, asked = searcher({"0..63": 1500, "0..31": 150, "32..63": 3, "64..127": 1})
    names, incomplete = discovery.search("filename:LINGUAS", "t", pause=6, get=get)
    # Largest files first, from where configuration files' sizes are.
    assert asked == [("2048..%d" % discovery.MAX_SIZE, 1), ("1024..2047", 1), ("512..1023", 1),
                     ("256..511", 1), ("128..255", 1), ("64..127", 1), ("0..63", 1),
                     ("32..63", 1), ("0..31", 1), ("0..31", 2)]
    assert len(names) == 154 and names == sorted(names)
    assert not incomplete
    assert clock.slept == [6] * 9


def test_search_incomplete(monkeypatch):
    monkeypatch.setattr(discovery, "time", Clock())
    monkeypatch.setattr(discovery, "SIZES", (0,))
    monkeypatch.setattr(discovery, "MAX_SIZE", 0)
    # One size with more than the cap can't be split: its first 1,000.
    get, asked = searcher({"0..0": 1200})
    names, incomplete = discovery.search("q", "t", get=get)
    assert len(names) == 1000 and incomplete
    assert len(asked) == 10


def test_search_deadline(monkeypatch):
    # Each request takes 50 seconds, and each pause 10.
    clock = Clock()

    def get(url, token):
        clock.now += 50
        return inner(url, token)
    inner, asked = searcher({"128..255": 250, "64..127": 5})
    monkeypatch.setattr(discovery, "time", clock)
    logged = []
    names, incomplete = discovery.search("q", "t", pause=10, get=get, deadline=340,
                                         log=logged.append)
    # Past the deadline, 128..255's last page and the smaller parts wait.
    assert asked == [("2048..%d" % discovery.MAX_SIZE, 1), ("1024..2047", 1), ("512..1023", 1),
                     ("256..511", 1), ("128..255", 1), ("128..255", 2)]
    assert len(names) == 200 and incomplete
    assert logged[0] == "q size:2048..%d, page 1: 0 results (50 s)" % discovery.MAX_SIZE
    assert logged[-1] == "Out of time: 3 parts left, from q size:128..255."


def test_search_no_deadline(monkeypatch):
    monkeypatch.setattr(discovery, "time", Clock())
    get, asked = searcher({})
    assert discovery.search("q", "t", get=get, deadline=None) == ([], False)
    assert len(asked) == len(discovery.SIZES)


def test_search_prs_splits_by_date(monkeypatch):
    clock = Clock()
    slept = clock.slept
    monkeypatch.setattr(discovery, "time", clock)
    asked = []

    def get(url, token):
        assert url.startswith(discovery.ISSUES)
        text = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["q"][0]
        dates = text.rsplit("merged:", 1)[1]
        asked.append(dates)
        total = {"2026-10-01..2026-10-11": 1200}.get(dates, 2)
        return {"total_count": total, "items": [
            {"repository_url": "https://api.github.com/repos/o/%s-%d" % (dates[:10], n)}
            for n in range(min(total, 2))]}
    names, incomplete = discovery.search_prs('"New Crowdin updates" in:title', "t",
                                             datetime.date(2026, 10, 1),
                                             datetime.date(2026, 10, 11), pause=2, get=get)
    assert asked == ["2026-10-01..2026-10-11", "2026-10-01..2026-10-06", "2026-10-07..2026-10-11"]
    assert names == ["o/2026-10-01-0", "o/2026-10-01-1", "o/2026-10-07-0", "o/2026-10-07-1"]
    assert not incomplete and slept == [2, 2]


def test_search_prs_one_day_over_the_cap(monkeypatch):
    monkeypatch.setattr(discovery, "time", Clock())
    day = datetime.date(2026, 10, 1)
    names, incomplete = discovery.search_prs("q", "t", day, day, get=lambda url, token: {
        "total_count": 1500, "items": []})
    assert (names, incomplete) == ([], True)


def stats_get(repositories):
    queries = []

    def get(url, token, data):
        assert url == discovery.GRAPHQL
        queries.append(data["query"])
        return {"data": {"r%d" % n: repositories.get(n)
                         for n in range(data["query"].count("repository("))}}
    return get, queries


def test_stats(monkeypatch):
    monkeypatch.setattr(discovery, "time", types.SimpleNamespace(sleep=lambda s: None))
    get, queries = stats_get({0: {"url": "https://github.com/a/x"}})
    found = discovery.stats(["b/y", "a/x", "c/z"], "t", batch=2, get=get)
    # Each batch's first repository is there; the others aren't.
    assert found == {"a/x": {"url": "https://github.com/a/x"}, "c/z": {"url": "https://github.com/a/x"}}
    assert len(queries) == 2
    assert 'r0: repository(owner: "a", name: "x")' in queries[0]
    assert 'r1: repository(owner: "b", name: "y")' in queries[0]


def test_stats_error():
    with pytest.raises(FetchError, match="Bad credentials"):
        discovery.stats(["a/x"], "t", get=lambda url, token, data: {"errors": ["Bad credentials"]})


def repo(name, stars=500, pushed="2025-06-01T00:00:00Z", archived=False, fork=False, spdx="MIT"):
    return {"url": "https://github.com/" + name, "stargazerCount": stars, "pushedAt": pushed,
            "isArchived": archived, "isFork": fork,
            "licenseInfo": {"spdxId": spdx} if spdx else None}


def test_select():
    found = {"a": repo("o/a", stars=200), "b": repo("o/b", stars=900), "c": repo("o/c", archived=True),
             "d": repo("o/d", fork=True), "e": repo("o/e", stars=99),
             "f": repo("o/f", pushed="2024-12-31T23:00:00Z"), "g": repo("o/g", pushed=None),
             "h": repo("o/h", spdx="NOASSERTION"), "i": repo("o/i", spdx=None)}
    kept, skipped = discovery.select(found, 100, datetime.date(2025, 1, 1))
    assert [r["url"] for r in kept] == ["https://github.com/o/b", "https://github.com/o/a"]
    assert skipped == {("repositories", "archived"): 1, ("repositories", "fork"): 1,
                       ("repositories", "fewer than 100 stars"): 1,
                       ("repositories", "not pushed since 2025-01-01"): 2,
                       ("repositories", "no licence detected"): 2}
    kept, _ = discovery.select(found, 0, None, licensed=False)
    assert len(kept) == 7


@pytest.mark.parametrize("spdx, expected", [
    ("MIT", "MIT"), ("GPL-3.0", "GPL-3.0-only"), ("LGPL-2.1", "LGPL-2.1-only"),
    ("GPL-2.0-or-later", "GPL-2.0-or-later"), ("NOASSERTION", None), (None, None)])
def test_licence(spdx, expected):
    assert discovery.licence(repo("o/a", spdx=spdx)) == expected


@pytest.mark.parametrize("url, description, homepage, issues, expected", [
    ("https://github.com/GNOME/meld", "Read-only mirror of https://gitlab.gnome.org/GNOME/meld",
     "https://gitlab.gnome.org/GNOME/meld", False, "https://gitlab.gnome.org/GNOME/meld"),
    ("https://github.com/xfce-mirror/thunar", "Mirror repository, PRs are not watched, please "
     "use Xfce's GitLab", "https://gitlab.xfce.org/xfce/thunar", False,
     "https://gitlab.xfce.org/xfce/thunar"),
    # Issues off, the code linked in the description.
    ("https://github.com/videolan/vlc", "VLC media player. Code here: "
     "https://code.videolan.org/videolan/vlc.", "http://www.videolan.org/vlc", False,
     "https://code.videolan.org/videolan/vlc"),
    ("https://github.com/o/app", "A mirror (https://git.example/o/app.git/)", None, True,
     "https://git.example/o/app.git"),
    ("https://github.com/madnight/nano", "Mirror of git://git.sv.gnu.org/nano.git",
     "https://nano-editor.org", False, "git://git.sv.gnu.org/nano.git"),
    ("https://github.com/GeopJr/Collision", "[MIRROR] Check hashes for your files",
     "https://codeberg.org/GeopJr/Collision", False, "https://codeberg.org/GeopJr/Collision"),
    # Not mirrors: a website, a link to another project, issues on.
    ("https://github.com/elementary/files", "File browser", "https://elementary.io", True, None),
    # A homepage counts only for a repository that says it's a mirror.
    ("https://github.com/Hjdskes/gcolor3", "A simple color chooser -- moved to GNOME GitLab",
     "https://www.hjdskes.nl/projects/gcolor3/", False, None),
    ("https://github.com/o/app", "Mirror of https://git.example/o/other", None, False, None),
    ("https://github.com/o/app", "Mirror", "https://github.com/p/app", False, None),
    ("https://github.com/o/app", "App", "https://git.example/o/app", True, None),
    ("https://github.com/o/app", None, None, False, None)])
def test_upstream(url, description, homepage, issues, expected):
    found = {"url": url, "description": description, "homepageUrl": homepage,
             "hasIssuesEnabled": issues}
    assert discovery.upstream(found) == expected


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/o/app", "o/app"), ("https://github.com/o/app.git/", "o/app"),
    ("https://gitlab.gnome.org/GNOME/meld", None), ("https://github.com/o", None),
    ("https://github.com/o/app/tree/main", None), (None, None)])
def test_github_name(url, expected):
    assert discovery.github_name(url) == expected


def test_default_pushed_since():
    assert discovery.default_pushed_since(datetime.date(2026, 10, 26)) == datetime.date(2025, 1, 1)


class Response(io.BytesIO):

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def http_error(code, **headers):
    message = email.message.Message()
    for key, value in headers.items():
        message[key.replace("_", "-")] = value
    return urllib.error.HTTPError("https://api.github.com/x", code, "limited", message, None)


def test_github_waits_out_rate_limits(monkeypatch):
    slept, requests = [], []
    answers = [http_error(403, Retry_After="7"), http_error(502), TimeoutError("timed out"),
               Response(b'{"ok": 1}')]

    def urlopen(request, timeout):
        requests.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(discovery.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(discovery, "time", types.SimpleNamespace(sleep=slept.append, time=lambda: 0))
    assert discovery.github("https://api.github.com/x", "tok", {"q": 1}) == {"ok": 1}
    assert slept == [8, 60, 60]
    assert requests[0].get_header("Authorization") == "Bearer tok"
    assert json.loads(requests[0].data) == {"q": 1}


def test_github_errors(monkeypatch):
    def urlopen(request, timeout):
        raise http_error(403)  # not a rate limit: no retry

    monkeypatch.setattr(discovery.urllib.request, "urlopen", urlopen)
    with pytest.raises(FetchError) as e:
        discovery.github("https://api.github.com/x", "tok")
    assert e.value.status == 403


def test_github_network_error(monkeypatch):
    slept = []

    def urlopen(request, timeout):
        raise urllib.error.URLError("no route")

    monkeypatch.setattr(discovery.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(discovery, "time", types.SimpleNamespace(sleep=slept.append))
    with pytest.raises(FetchError, match="no route"):
        discovery.github("https://api.github.com/x", "tok", retries=2, wait=5)
    assert slept == [5, 5]


def test_github_not_json(monkeypatch):
    monkeypatch.setattr(discovery.urllib.request, "urlopen", lambda request, timeout: Response(b"<html>"))
    with pytest.raises(FetchError):
        discovery.github("https://api.github.com/x", "tok")


def test_wait():
    assert discovery._wait({"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000000100"}, 60) \
        <= discovery.MAX_WAIT
    assert discovery._wait({}, 60) == 60
    # GitHub rounds Retry-After down.
    assert discovery._wait({"Retry-After": "1"}, 60) == 2


def test_command(amagama_config, repository, tmp_path, monkeypatch):  # noqa: F811
    sources = tmp_path / "sources.toml"
    sources.write_text(block("tool", "git", url="https://github.com/org/tool", files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search",
                        lambda query, token, pause=6, **kwargs: (["org/repo", "org/tool"], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {
        "org/repo": dict(repo("org/repo", spdx="GPL-2.0"), url=repository.as_uri()),
        "org/tool": repo("org/tool"), "org/old": repo("org/old", archived=True)})
    output = tmp_path / "linguas.toml"
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--pushed-since", "2025-01-01", "--min-units", "2",
                                         "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert "2 repositories found." in result.output
    assert "1 blocks." in result.output
    assert "Skipped 1 repositories: archived." in result.output
    assert "Skipped 1 repositories: imported by another block." in result.output
    assert output.read_text().startswith(
        "# Generated by: amagama-manage discover_sources linguas --prefix linguas --min-stars 100 "
        "--pushed-since 2025-01-01 --min-units 2 --min-age-days 60 --tag discovered\n")
    [found] = load_sources(str(output))
    assert (found.name, found.licence) == ("linguas-repo", "GPL-2.0-only")
    assert found.selection.mask_texts == ["po/{lang}.po"]


def test_command_keeps_disabled(amagama_config, repository, tmp_path, monkeypatch):  # noqa: F811
    sources = tmp_path / "sources.toml"
    sources.write_text('include = ["linguas.toml"]\n')
    output = tmp_path / "linguas.toml"
    output.write_text('[[block]]\nname = "linguas-mine"\ndisabled = "duplicates tool"\n'
                      'title = "repo"\nsource = "git"\nurl = "%s"\nfiles = ["po/{lang}.po"]\n'
                      'licence = "MIT"\n\n'
                      '[[block]]\nname = "linguas-gone"\ndisabled = "duplicates other"\n'
                      'title = "gone"\nsource = "git"\nurl = "https://git.example/o/gone"\n'
                      'files = ["po/{lang}.po"]\n' % repository.as_uri())
    amagama_config(UPDATER_SOURCES=str(sources))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, **kwargs: (["org/repo"], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {
        "org/repo": dict(repo("org/repo"), url=repository.as_uri())})
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--min-units", "2", "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert "2 blocks, 2 disabled (1 not found again)." in result.output
    # Regenerated, the block keeps its name, and stays disabled with its reason;
    # one the search didn't find again is kept as it was.
    text = output.read_text()
    assert 'name = "linguas-mine"\ndisabled = "duplicates tool"\n' in text
    assert ('name = "linguas-gone"\ndisabled = "duplicates other"\ntitle = "gone"\n'
            'url = "https://git.example/o/gone"\n') in text
    assert load_sources(str(sources)) == []


def test_command_mirror(amagama_config, repository, tmp_path, monkeypatch):  # noqa: F811
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, **kwargs: (["org/repo"], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {"org/repo": dict(
        repo("org/repo"), url=repository.as_uri(), description="Read-only mirror",
        homepageUrl="https://gitlab.example/org/repo", hasIssuesEnabled=False)})
    output = tmp_path / "linguas.toml"
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--min-units", "2", "--weight", "0.8",
                                         "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert "1 mirrors: their blocks use the repositories they copy." in result.output
    [found] = load_sources(str(output))
    # Read from the mirror; the block fetches from the repository it copies.
    assert (found.name, found.fetcher.settings["url"], found.licence, found.weight) == (
        "linguas-repo", "https://gitlab.example/org/repo", "MIT", 0.8)


def test_command_mirrors_first(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, **kwargs: (["o/a", "o/b"], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {
        "o/a": repo("o/a", stars=900),
        "o/b": dict(repo("o/b"), description="Mirror of https://git.example/o/b")})
    given = []

    def blocks(urls, *args, **kwargs):
        given.append((urls, kwargs["mirrors"]))
        return [], {}

    monkeypatch.setitem(discovery.SIGNALS, "linguas", dict(discovery.SIGNALS["linguas"],
                                                           blocks=blocks))
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas"))
    assert result.exit_code == 0, result.output
    # The original before a copy with more stars.
    assert given == [(["https://git.example/o/b", "https://github.com/o/a"],
                      {"https://git.example/o/b": "https://github.com/o/b"})]


def test_command_rereads_regenerated(amagama_config, tmp_path, monkeypatch):
    sources = tmp_path / "sources.toml"
    sources.write_text(
        block("linguas-gone", "git", url="https://github.com/org/gone", files=["po/{lang}.po"])
        + block("linguas-meld", "git", url="https://gitlab.gnome.org/GNOME/meld",
                mirror="https://github.com/GNOME/meld", files=["po/{lang}.po"])
        + block("other-tool", "git", url="https://github.com/org/tool", files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, **kwargs: (["org/repo"], False))
    asked = []

    def stats(names, token):
        asked.append(names)
        return {}

    monkeypatch.setattr(discovery, "stats", stats)
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas"))
    assert result.exit_code == 0, result.output
    assert "1 repositories found, and 2 more from the blocks being regenerated." in result.output
    # Another prefix's blocks aren't read again.
    assert asked == [["GNOME/meld", "org/gone", "org/repo"]]


def test_command_bot_prs(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, **kwargs: (["org/a"], False))
    since = []

    def search_prs(query, token, start, **kwargs):
        since.append((query, start))
        return ["org/a", "org/b"], False

    monkeypatch.setattr(discovery, "search_prs", search_prs)
    asked = []
    monkeypatch.setattr(discovery, "stats", lambda names, token: asked.append(names) or {})
    result = CliRunner().invoke(manage, ("discover_sources", "crowdin", "--prefix", "crowdin",
                                         "--bot-prs-days", "30"))
    assert result.exit_code == 0, result.output
    assert "2 repositories with a bot's pull request, 1 of them not found by code search." \
        in result.output
    assert since == [(q, datetime.date.today() - datetime.timedelta(days=30))
                     for q in discovery.SIGNALS["crowdin"]["bot_prs"]]
    assert asked == [["org/a", "org/b"]]


def test_command_bot_prs_signal(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--bot-prs-days", "30"))
    assert result.exit_code == 2
    assert "linguas has no translation bot's pull requests." in result.output


def test_command_search_pause(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    # A workflow's token gets about one code search a minute.
    monkeypatch.setenv("AMAGAMA_SEARCH_PAUSE", "61")
    pauses = []
    monkeypatch.setattr(discovery, "search",
                        lambda query, token, pause=6, **kwargs: pauses.append(pause) or ([], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {})
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas"))
    assert result.exit_code == 0, result.output
    assert pauses == [61]


def test_command_search_minutes(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setenv("AMAGAMA_SEARCH_MINUTES", "180")
    deadlines = []

    def search(query, token, pause=6, deadline=None, log=None):
        deadlines.append(deadline)
        log("searched")
        return [], True
    monkeypatch.setattr(discovery, "search", search)
    monkeypatch.setattr(discovery, "stats", lambda names, token: {})
    report = tmp_path / "report.md"
    started = time.monotonic()
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--output", str(tmp_path / "linguas.toml"),
                                         "--report", str(report)))
    assert result.exit_code == 0, result.output
    [deadline] = deadlines
    assert started + 180 * 60 <= deadline <= time.monotonic() + 180 * 60
    assert "searched\n" in result.output
    assert report.read_text().splitlines()[2] == (
        "Code search didn't give all its results this run: new candidates may be missing.")


def test_command_no_search_minutes(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    deadlines = []
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, deadline=None, log=None:
                        deadlines.append(deadline) or ([], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {})
    report = tmp_path / "report.md"
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--output", str(tmp_path / "linguas.toml"),
                                         "--report", str(report)))
    assert result.exit_code == 0, result.output
    assert deadlines == [None]
    assert "Code search" not in report.read_text()


def test_command_writes_by_name(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    monkeypatch.setattr(discovery, "search", lambda query, token, pause=6, **kwargs: ([], False))
    monkeypatch.setattr(discovery, "stats", lambda names, token: {})

    def blocks(urls, *args, **kwargs):
        # Read most stars first.
        return [{"name": "linguas-%s" % name, "title": name, "source": "git",
                 "url": "https://git.example/%s" % name, "files": ["po/{lang}.po"]}
                for name in ("zeta", "Alpha", "beta")], {}

    monkeypatch.setitem(discovery.SIGNALS, "linguas", dict(discovery.SIGNALS["linguas"], blocks=blocks))
    output = tmp_path / "linguas.toml"
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas",
                                         "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert [b.name for b in load_sources(str(output))] == ["linguas-Alpha", "linguas-beta", "linguas-zeta"]


def test_command_needs_token(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas"))
    assert result.exit_code == 2
    assert "set GITHUB_TOKEN" in result.output


def test_command_fetch_error(amagama_config, tmp_path, monkeypatch):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")

    def search(query, token, pause=6, **kwargs):
        raise FetchError("https://api.github.com/search/code: HTTP Error 401")

    monkeypatch.setattr(discovery, "search", search)
    result = CliRunner().invoke(manage, ("discover_sources", "linguas", "--prefix", "linguas"))
    assert result.exit_code == 1
    assert "HTTP Error 401" in result.output


def test_command_crowdin(amagama_config, crowdin_repository, tmp_path, monkeypatch):  # noqa: F811
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    queries = []

    def search(query, token, pause=6, **kwargs):
        queries.append(query)
        return ["org/repo"], False

    monkeypatch.setattr(discovery, "search", search)
    monkeypatch.setattr(discovery, "stats", lambda names, token: {"org/repo": {
        "url": crowdin_repository, "stargazerCount": 500, "pushedAt": "2026-01-01T00:00:00Z",
        "isArchived": False, "isFork": False, "licenseInfo": {"spdxId": "MIT"}}})
    output = tmp_path / "crowdin.toml"
    result = CliRunner().invoke(manage, ("discover_sources", "crowdin", "--prefix", "crowdin",
                                         "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert queries == discovery.SIGNALS["crowdin"]["queries"]
    assert "1 repositories found." in result.output
    [found] = load_sources(str(output))
    assert (found.name, found.licence, found.selection.mask_texts) == (
        "crowdin-repo", "MIT", ["po/{lang}.po"])
