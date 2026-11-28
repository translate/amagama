#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""GitLab.com candidates for discover_sources --forge gitlab."""

import http.client
import io
import json
import urllib.error

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.tests.test_linguas import repository  # noqa: F401 (fixture)
from amagama.tests.test_updater import block
from amagama.updater import discovery, gitlab
from amagama.updater.fetchers import FetchError
from amagama.updater.sources import load_sources


def project(number, path, stars=50, activity="2026-11-01T00:00:00Z", **settings):
    return dict({"id": number, "path_with_namespace": path, "web_url": "https://gitlab.com/" + path,
                 "star_count": stars, "last_activity_at": activity, "archived": False,
                 "default_branch": "main", "empty_repo": False}, **settings)


@pytest.mark.parametrize(("key", "spdx"), [
    ("mit", "MIT"), ("gpl-3.0", "GPL-3.0"), ("apache-2.0", "Apache-2.0"), ("other", None),
    (None, None), ("not-a-licence", None),
    # GitLab says "or later" with a plus.
    ("gpl-2.0+", "GPL-2.0-or-later"), ("lgpl-2.1+", "LGPL-2.1-or-later"), ("mit+", None)])
def test_spdx(key, spdx):
    assert gitlab.spdx(key) == spdx


def test_repository():
    found = gitlab.repository(project(1, "g/app", stars=30, forked_from_project={"id": 2},
                                      description="A mirror of https://git.example/app"), "gpl-3.0")
    assert found["url"] == "https://gitlab.com/g/app"
    assert (found["stargazerCount"], found["isFork"], found["isArchived"]) == (30, True, False)
    # GitLab's GPL-3.0 is GPL-3.0-only, as GitHub's is.
    assert discovery.licence(found) == "GPL-3.0-only"
    assert discovery.licence(gitlab.repository(project(1, "g/a"), "gpl-3.0+")) == "GPL-3.0-or-later"


class Lab(gitlab.GitLab):
    """A GitLab answering from pages {(path, params as a tuple): JSON}."""

    def __init__(self, pages, cache_path=None):
        super(Lab, self).__init__("tok", cache_path, pause=0)
        self.pages, self.asked = pages, []

    def get(self, endpoint, **params):
        key = (endpoint, params.get("path"), params.get("page"))
        self.asked.append(key)
        return self.pages.get(key)


def test_projects_stop_below_min_stars():
    first = [project(n, "g/p%d" % n, stars=200 - n) for n in range(100)]
    second = [project(100 + n, "g/q%d" % n, stars=90 - n) for n in range(100)]
    lab = Lab({("projects", None, 1): first, ("projects", None, 2): second,
               ("projects", None, 3): []})
    found = lab.projects(min_stars=50)
    assert len(found) == 100 + 41
    # The second page went below 50 stars: no third is asked for.
    assert [page for _, _, page in lab.asked] == [1, 2]


def test_projects_stop_at_gitlab_limit(monkeypatch):
    monkeypatch.setattr(gitlab, "MAX_PAGES", 2)
    full = [project(n, "g/p%d" % n, stars=500) for n in range(100)]
    lab = Lab({("projects", None, 1): full, ("projects", None, 2): full})
    assert len(lab.projects(min_stars=1)) == 200 and lab.truncated
    # Page 3 is past the limit: not asked for.
    assert [page for _, _, page in lab.asked] == [1, 2]


def test_has_config(tmp_path):
    app, tool, empty = project(1, "g/app"), project(2, "g/tool"), project(3, "g/e", empty_repo=True)
    pages = {("projects/1/repository/tree", None, 1): [{"name": "crowdin.yml"}, {"name": "po"}],
             ("projects/1/repository/tree", "po", 1): [{"name": "LINGUAS"}, {"name": "af.po"}],
             ("projects/2/repository/tree", None, 1): [{"name": "po"}],
             ("projects/2/repository/tree", "po", 1): [{"name": "af.po"}]}
    cache = tmp_path / "gitlab.json"
    lab = Lab(pages, str(cache))
    assert lab.has_config(app, "crowdin") and lab.has_config(app, "linguas")
    assert not lab.has_config(tool, "crowdin") and not lab.has_config(tool, "linguas")
    assert not lab.has_config(empty, "linguas")
    assert lab.has_config(app, "linguas")
    # Each listing once, and none for an empty repository.
    assert len(lab.asked) == 4
    lab.save()
    # A later run reads the cache, until the project changes.
    again = Lab(pages, str(cache))
    assert again.has_config(app, "crowdin") and again.asked == []
    assert again.has_config(dict(app, last_activity_at="2026-11-20T00:00:00Z"), "crowdin")
    assert len(again.asked) == 1


def test_names_pages():
    first = [{"name": "file%03d" % n} for n in range(100)]
    lab = Lab({("projects/1/repository/tree", None, 1): first,
               ("projects/1/repository/tree", None, 2): [{"name": "crowdin.yml"}]})
    # The configuration is on the second page of a big top level.
    assert lab.has_config(project(1, "g/app"), "crowdin")
    assert [page for _, _, page in lab.asked] == [1, 2]


def test_licence_key():
    lab = Lab({("projects/1", None, None): {"id": 1, "license": {"key": "mit"}},
               ("projects/2", None, None): {"id": 2, "license": None}})
    assert lab.licence_key(project(1, "g/app")) == "mit"
    assert lab.licence_key(project(2, "g/tool")) is None


class Response(io.BytesIO):

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def test_get(monkeypatch, pauses):
    requests, answers = [], [
        urllib.error.HTTPError("u", 429, "limited", http.client.HTTPMessage(), None),
        Response(b'[{"id": 1}]')]

    def urlopen(request, timeout):
        requests.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(gitlab.urllib.request, "urlopen", urlopen)
    lab = gitlab.GitLab("tok", pause=0)
    assert lab.get("projects", page=1) == [{"id": 1}]
    assert requests[0].get_header("Private-token") == "tok"
    assert requests[0].full_url == "https://gitlab.com/api/v4/projects?page=1"
    # The 429 waited and was tried again.
    assert len(pauses) == 1

    # A directory's listing passes its path as a parameter.
    monkeypatch.setattr(gitlab.urllib.request, "urlopen",
                        lambda request, timeout: requests.append(request) or Response(
                            b'[{"name": "LINGUAS"}]'))
    assert lab.names(project(1, "g/app"), "po") == ["LINGUAS"]
    assert requests[-1].full_url.endswith("/projects/1/repository/tree?page=1&per_page=100&path=po")

    def missing(request, timeout):
        raise urllib.error.HTTPError("u", 404, "missing", http.client.HTTPMessage(), None)
    monkeypatch.setattr(gitlab.urllib.request, "urlopen", missing)
    assert lab.get("projects/9") is None

    def forbidden(request, timeout):
        raise urllib.error.HTTPError("u", 403, "forbidden", http.client.HTTPMessage(), None)
    monkeypatch.setattr(gitlab.urllib.request, "urlopen", forbidden)
    with pytest.raises(FetchError, match="403"):
        lab.get("projects")


def command_lab(monkeypatch, projects, configs, licences, single=None):
    monkeypatch.setattr(gitlab.GitLab, "projects", lambda self, min_stars: projects)
    monkeypatch.setattr(gitlab.GitLab, "project", lambda self, path: (single or {}).get(path))
    monkeypatch.setattr(gitlab.GitLab, "has_config",
                        lambda self, found, signal: found["web_url"] in configs)
    monkeypatch.setattr(gitlab.GitLab, "licence_key",
                        lambda self, found: licences.get(found["web_url"]))


def test_get_errors(monkeypatch, pauses):
    answers = [urllib.error.URLError(ConnectionResetError("reset")), Response(b"<html>")]

    def urlopen(request, timeout):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(gitlab.urllib.request, "urlopen", urlopen)
    # A dropped connection is tried again; a page that isn't JSON isn't.
    with pytest.raises(FetchError, match="Expecting value") as error:
        gitlab.GitLab("tok", pause=0).get("projects")
    assert not error.value.transient
    assert len(pauses) == 1


def test_project_path(monkeypatch):
    asked = []
    monkeypatch.setattr(gitlab.GitLab, "get", lambda self, endpoint, **params: asked.append(
        endpoint) or {"id": 1})
    assert gitlab.GitLab("tok").project("g/sub group/app") == {"id": 1}
    assert asked == ["projects/g%2Fsub%20group%2Fapp"]


def test_command(amagama_config, repository, tmp_path, monkeypatch):  # noqa: F811
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"),
                   UPDATER_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    app = dict(project(1, "g/app", stars=60), web_url=repository.as_uri())
    command_lab(monkeypatch, [app, project(2, "g/plain"), project(3, "g/old", archived=True),
                              project(4, "g/closed")],
                configs={repository.as_uri(), "https://gitlab.com/g/closed"},
                licences={repository.as_uri(): "gpl-2.0"})
    output = tmp_path / "linguas-gitlab.toml"
    result = CliRunner().invoke(manage, (
        "discover_sources", "linguas", "--forge", "gitlab", "--prefix", "linguas-gitlab",
        "--min-stars", "25", "--pushed-since", "2025-01-01", "--min-units", "2",
        "--output", str(output)))
    assert result.exit_code == 0, result.output
    assert "4 projects with at least 25 stars." in result.output
    assert "Skipped 1 repositories: archived." in result.output
    assert "Skipped 1 repositories: no linguas configuration where it's looked for." \
        in result.output
    assert "Skipped 1 repositories: no licence detected." in result.output
    assert output.read_text().startswith(
        "# Generated by: amagama-manage discover_sources linguas --forge gitlab "
        "--prefix linguas-gitlab --min-stars 25 --pushed-since 2025-01-01 --min-units 2 ")
    [found] = load_sources(str(output))
    assert (found.name, found.licence) == ("linguas-gitlab-repo", "GPL-2.0-only")


def test_command_rereads_regenerated(amagama_config, tmp_path, monkeypatch):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("linguas-gitlab-few", "git", url="https://gitlab.com/g/few",
                             files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    looked_up = []

    def lookup(self, path):
        looked_up.append(path)
        return project(5, "g/few", stars=3)
    command_lab(monkeypatch, [], configs=set(), licences={})
    monkeypatch.setattr(gitlab.GitLab, "project", lookup)
    result = CliRunner().invoke(manage, (
        "discover_sources", "linguas", "--forge", "gitlab", "--prefix", "linguas-gitlab",
        "--min-stars", "25", "--output", str(tmp_path / "out.toml")))
    assert result.exit_code == 0, result.output
    # A block being regenerated is looked up though it has too few stars now, and dropped.
    assert looked_up == ["g/few"]
    assert "0 projects with at least 25 stars, and 1 more from the blocks being regenerated." \
        in result.output
    assert "Skipped 1 repositories: fewer than 25 stars." in result.output
    assert "linguas-gitlab-few" not in (tmp_path / "out.toml").read_text()


def test_command_drops_gone_project(amagama_config, tmp_path, monkeypatch):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("gitlab-linguas-gone", "git", url="https://gitlab.com/g/gone",
                             files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    command_lab(monkeypatch, [], configs=set(), licences={})
    output = tmp_path / "out.toml"
    result = CliRunner().invoke(manage, (
        "discover_sources", "linguas", "--forge", "gitlab", "--prefix", "gitlab-linguas",
        "--min-stars", "25", "--output", str(output)))
    assert result.exit_code == 0, result.output
    # Deleted or made private: its block goes.
    assert "gitlab-linguas-gone" not in output.read_text()
    assert "Kept" not in result.output


def test_command_out_of_time(amagama_config, tmp_path, monkeypatch):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("gitlab-linguas-app", "git", url="https://gitlab.com/g/app",
                             files=["po/{lang}.po"]))
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"))
    monkeypatch.setenv("GITLAB_TOKEN", "t")
    command_lab(monkeypatch, [project(1, "g/app", stars=60)],
                configs={"https://gitlab.com/g/app"}, licences={})
    output = tmp_path / "out.toml"
    report = tmp_path / "report.md"
    result = CliRunner().invoke(manage, (
        "discover_sources", "linguas", "--forge", "gitlab", "--prefix", "gitlab-linguas",
        "--min-stars", "25", "--search-minutes", "0.000001", "--output", str(output),
        "--report", str(report)))
    assert result.exit_code == 0, result.output
    assert "Out of time: 1 projects not read." in result.output
    # Not read, so its block is kept as it was.
    assert "Kept 1 blocks whose repositories weren't read: gitlab-linguas-app." in result.output
    assert 'name = "gitlab-linguas-app"' in output.read_text()
    assert "Not every project was read this run" in report.read_text()


@pytest.mark.parametrize(("arguments", "error"), [
    ((), "set GITLAB_TOKEN"),
    (("--bot-prs-days", "7"), "no translation bot's pull requests on GitLab")])
def test_command_usage(amagama_config, tmp_path, monkeypatch, arguments, error):
    amagama_config(UPDATER_SOURCES=str(tmp_path / "missing.toml"))
    if arguments:
        monkeypatch.setenv("GITLAB_TOKEN", "t")
    else:
        monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    result = CliRunner().invoke(manage, ("discover_sources", "crowdin", "--forge", "gitlab",
                                         "--prefix", "crowdin-gitlab") + arguments)
    assert result.exit_code == 2
    assert error in result.output


def test_cache_survives_bad_file(tmp_path):
    cache = tmp_path / "gitlab.json"
    cache.write_text("{not json")
    lab = gitlab.GitLab("tok", str(cache), pause=0)
    assert lab.cache == {}
    lab.cache["names"] = {"1:x": ["po"]}
    lab.save()
    assert json.loads(cache.read_text()) == {"names": {"1:x": ["po"]}}
