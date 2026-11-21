#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Files in dot-prefixed directories must remain eligible for import."""

import subprocess

import pytest

from amagama.updater.fetchers import Git, files_under
from amagama.updater.filemask import Selection


@pytest.mark.parametrize("mask,path,language", [
    (".github/msstore-config/Listings/{lang}.json",
     ".github/msstore-config/Listings/fr.json", "fr"),
    ("docs/.vitepress/i18n/strings/{lang}.json",
     "docs/.vitepress/i18n/strings/pt_BR.json", "pt_BR"),
    ("assets/.translations/{lang}.po", "assets/.translations/de.po", "de"),
])
def test_files_under_hidden_directory_matches_mask(tmp_path, mask, path, language):
    file = tmp_path / path
    file.parent.mkdir(parents=True)
    file.write_text("translated")

    found = list(files_under(tmp_path))
    assert found == [path]
    assert Selection([mask]).select(found[0], None) == language


def test_files_under_skips_only_git_directories(tmp_path):
    paths = (".git/config", "nested/.git/objects/pack", ".gitignore",
             ".github/workflows/de.yml", "nested/.translations/de.po", "regular/en.po")
    for path in paths:
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("content")

    assert set(files_under(tmp_path)) == {
        ".gitignore", ".github/workflows/de.yml", "nested/.translations/de.po", "regular/en.po"
    }


def test_files_under_preserves_order_and_regular_files(tmp_path):
    for path in ("z.txt", "a.txt", "folder/z.po", "folder/a.po"):
        file = tmp_path / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("content")

    assert list(files_under(tmp_path)) == ["a.txt", "z.txt", "folder/a.po", "folder/z.po"]


def test_files_under_repeated_calls_include_hidden_files(tmp_path):
    path = tmp_path / "docs/.vitepress/i18n/strings/en.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    expected = ["docs/.vitepress/i18n/strings/en.json"]

    assert list(files_under(tmp_path)) == expected
    assert list(files_under(tmp_path)) == expected


def test_git_fetch_includes_hidden_translation_paths(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    for path in (".github/msstore-config/Listings/fr.json",
                 "assets/.translations/de.po", "regular/en.po"):
        file = repo / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("translated")
    subprocess.run(["git", "-C", str(repo), "add", "--all"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=Test",
                    "-c", "user.email=test@example.test", "commit", "-qm", "fixture"], check=True)

    dest = tmp_path / "fetched"
    found = Git({"url": repo.as_uri(), "ref": "main"}).fetch(str(dest))
    assert {path for path, language in found} == {
        ".github/msstore-config/Listings/fr.json", "assets/.translations/de.po", "regular/en.po"
    }
    assert all(language is None for _, language in found)
    assert not (dest / ".git").exists()
