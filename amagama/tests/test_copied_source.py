#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest

from amagama.tests.test_pairing import block, git_repo, lookup, update  # noqa: F401
from amagama.tmdb import copied_source


@pytest.mark.parametrize("source, target, expected", [
    ("Open the file.", "Open the file.", True),
    ("Recent Searches", "Recent Searches", True),
    ("Don't save", "Don't save", True),
    ("Open <b>the</b> file", "Open <b>the</b> file", True),
    # The same once ellipses are normalised, as for matching.
    ("Open file...", "Open file…", True),
    ("Open the file.", "Maak die lêer oop.", False),
    # A word, an acronym, placeholders, a URL: often the same in every language.
    ("Settings", "Settings", False),
    ("Wi-Fi", "Wi-Fi", False),
    ("TCP WAN", "TCP WAN", False),
    ("%1$d MB / %2$d MB", "%1$d MB / %2$d MB", False),
    ("{count} min", "{count} min", False),
    ("https://example.org/help/index.html", "https://example.org/help/index.html", False),
    ('$t(entity.track, {"count": 2})', '$t(entity.track, {"count": 2})', False),
])
def test_copied_source(source, target, expected):
    assert copied_source(source, target) is expected


def po(*pairs):
    return 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n' + "".join(
        '\nmsgid "%s"\nmsgstr "%s"\n' % pair for pair in pairs)


@pytest.mark.parametrize("segment", [False, True])
def test_update_skips_copied_sources(update, tmp_path, segment):  # noqa: F811 - fixture
    """Crowdin and others export an untranslated string as its source."""
    url = git_repo(tmp_path / "repo", {
        "po/af.po": po(("Open the file.", "Open the file."),
                       ("Close it. Then quit.", "Close it. Then quit."),
                       ("Settings", "Settings"), ("TCP WAN", "TCP WAN"),
                       ("Save file", "Stoor lêer")),
        "po/en_GB.po": po(("Open the file.", "Open the file.")),
    })
    result = update(block("x", "git", url=url, files=["po/{lang}.po"], segment=segment))
    assert result.exit_code == 0, result.output
    assert "Imported 2 files: 4 units (2 skipped)." in result.output
    assert lookup("af", "Open the file.") == []
    assert lookup("af", "Then quit.") == []
    assert lookup("af", "Settings") == ["Settings"]
    assert lookup("af", "TCP WAN") == ["TCP WAN"]
    assert lookup("af", "Save file") == ["Stoor lêer"]
    assert lookup("en_GB", "Open the file.") == ["Open the file."]
