#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import subprocess

import pytest
from click.testing import CliRunner

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_updater import block
from amagama.updater.chromium import listed_translations, message_id, paired_store, read_grd

# Three messages from Chromium's ui/strings/ui_strings.grd, with the ids its
# translations use.
GRD = """<?xml version="1.0" encoding="UTF-8"?>
<grit latest_public_release="0" current_release="1">
  <release seq="1">
    <messages fallback_to_english="true">
      <message name="IDS_TIME_SECS" desc="Seconds">
               {SECONDS, plural, =1 {1 sec} other {# secs}}
      </message>
      <message name="IDS_CONCAT_TWO_STRINGS_WITH_COMMA" desc="Two strings">
        <ph name="TYPE_1">$1<ex>2 weak</ex></ph>, <ph name="TYPE_2">$2<ex>7 old</ex></ph>
      </message>
      <message name="IDS_APP_TAB_KEY" desc="Tab key" meaning="Tab key on the keyboard">
        Tab
      </message>
      <message name="IDS_NOT_TRANSLATED" desc="x" translateable="false">
        Chromium
      </message>
      <part file="extra.grdp" />
    </messages>
  </release>
</grit>
"""

GRDP = """<?xml version="1.0" encoding="utf-8"?>
<grit-part>
  <message name="IDS_SPACED" desc="Kept spaces">
    ''' Save file '''
  </message>
</grit-part>
"""

XTB = """<?xml version="1.0" ?>
<!DOCTYPE translationbundle>
<translationbundle lang="af">
<translation id="9044832324875206639">{SECONDS,plural, =1{1 sek.}other{# sek.}}</translation>
<translation id="822964464349305906"><ph name="TYPE_1" />, <ph name="TYPE_2" /></translation>
<translation id="5606865426897221650">Oortjie</translation>
<translation id="%s"> Stoor lêer </translation>
<translation id="1">Nie in die Engels nie</translation>
<translation id="%s"><ph name="TYPE_3" /></translation>
</translationbundle>
"""


def test_message_id():
    assert message_id("{SECONDS, plural, =1 {1 sec} other {# secs}}") == "9044832324875206639"
    assert message_id("TYPE_1, TYPE_2") == "822964464349305906"
    assert message_id("Tab", "Tab key on the keyboard") == "5606865426897221650"


def files(tmp_path):
    (tmp_path / "ui_strings.grd").write_text(GRD, encoding="utf-8")
    (tmp_path / "extra.grdp").write_text(GRDP, encoding="utf-8")
    xtb = XTB % (message_id("Save file"), message_id("TYPE_1, TYPE_2"))
    (tmp_path / "ui_strings_af.xtb").write_text(xtb, encoding="utf-8")
    return str(tmp_path / "ui_strings_af.xtb"), str(tmp_path / "ui_strings.grd")


def test_read_grd(tmp_path):
    xtb, grd = files(tmp_path)
    english = read_grd(grd)
    assert english["822964464349305906"] == ("$1, $2", {"TYPE_1": "$1", "TYPE_2": "$2"})
    # The part file is read; ''' keeps spaces in grit's output, not in the id.
    # Untranslatable messages aren't read.
    assert english[message_id("Save file")][0] == "Save file"
    assert len(english) == 4


def test_paired_store(tmp_path):
    xtb, grd = files(tmp_path)
    assert [(u.source, u.target) for u in paired_store(xtb, grd).units] == [
        ("{SECONDS, plural, =1 {1 sec} other {# secs}}", "{SECONDS,plural, =1{1 sek.}other{# sek.}}"),
        ("$1, $2", "$1, $2"),
        ("Tab", "Oortjie"),
        ("Save file", "Stoor lêer"),
    ]


@pytest.fixture
def chromium_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ui/strings/translations").mkdir(parents=True)
    (repo / "ui/strings/ui_strings.grd").write_text(GRD, encoding="utf-8")
    (repo / "ui/strings/extra.grdp").write_text(GRDP, encoding="utf-8")
    xtb = XTB % (message_id("Save file"), message_id("TYPE_1, TYPE_2"))
    (repo / "ui/strings/translations/ui_strings_af.xtb").write_text(xtb, encoding="utf-8")

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                        "-C", str(repo)] + list(args), check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "strings")
    return repo.as_uri()


def lookup(source):
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/af/unit/%s" % source)
    return [c["target"] for c in response.get_json()] if response.status_code == 200 else []


def update(amagama_config, tmp_path, url, **extra):
    settings = {"files": ["*/strings/translations/*_{lang}.xtb"],
                "template": "*/strings/*.grd", "format": "xtb"}
    settings.update(extra)
    settings = {key: value for key, value in settings.items() if value is not None}
    sources = tmp_path / "sources.toml"
    sources.write_text(block("chromium", "git", url=url, **settings))
    amagama_config(UPDATER_SOURCES=str(sources), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                   UPDATER_WORKERS=1)
    return CliRunner().invoke(manage, ("update", "-s", "en"))


def test_update(amagama_config, tmp_path, chromium_repo):
    result = update(amagama_config, tmp_path, chromium_repo, keep=["**/*.grdp"])
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 4 units (0 skipped)." in result.output
    assert lookup("Tab") == ["Oortjie"]
    assert lookup("$1, $2") == ["$1, $2"]
    assert lookup("Save file") == ["Stoor lêer"]


def test_update_without_parts(amagama_config, tmp_path, chromium_repo):
    # The .grd's part file isn't kept, so its translations can't be read.
    result = update(amagama_config, tmp_path, chromium_repo)
    assert result.exit_code == 0, result.output
    assert "Couldn't read 1 files:" in result.output
    assert "extra.grdp" in result.output


TRANSLATIONS = """
  <translations>
    <file path="translations/ui_strings_af.xtb" lang="af" />
    <if expr="is_android">
      <file path="../android/ui_strings_zu.xtb" lang="zu" />
    </if>
    <file path="translations/ui_strings_ar-XB.xtb" lang="ar-XB" />
  </translations>
  <release"""


def test_listed_translations(tmp_path):
    (tmp_path / "ui/strings").mkdir(parents=True)
    (tmp_path / "ui/strings/ui_strings.grd").write_text(
        GRD.replace("\n  <release", TRANSLATIONS, 1), encoding="utf-8")
    assert listed_translations(str(tmp_path), ["ui/strings/ui_strings.grd"]) == {
        "ui/strings/translations/ui_strings_af.xtb": ("ui/strings/ui_strings.grd", "af"),
        "ui/android/ui_strings_zu.xtb": ("ui/strings/ui_strings.grd", "zu"),
        "ui/strings/translations/ui_strings_ar-XB.xtb": ("ui/strings/ui_strings.grd", "ar-XB"),
    }


def test_listed_translations_bad_grd(tmp_path):
    (tmp_path / "bad.grd").write_text("<grit>", encoding="utf-8")
    with pytest.raises(ValueError, match="bad.grd"):
        listed_translations(str(tmp_path), ["bad.grd"])


def test_update_grd_listed(amagama_config, tmp_path):
    repo = tmp_path / "repo"
    (repo / "ui/strings/translations").mkdir(parents=True)
    (repo / "ui/strings/ui_strings.grd").write_text(
        GRD.replace("\n  <release", TRANSLATIONS, 1), encoding="utf-8")
    (repo / "ui/strings/extra.grdp").write_text(GRDP, encoding="utf-8")
    xtb = XTB % (message_id("Save file"), message_id("TYPE_1, TYPE_2"))
    for name in ("ui_strings_af.xtb", "ui_strings_ar-XB.xtb", "ui_strings_xh.xtb"):
        (repo / "ui/strings/translations" / name).write_text(xtb, encoding="utf-8")
    subprocess.run(["sh", "-c", "git init -q -b main && git add . && git -c user.name=t "
                    "-c user.email=t@example.org commit -q -m strings"], cwd=repo, check=True)
    # Languages come from the .grd: xh isn't listed, ar-XB is a pseudo-locale.
    result = update(amagama_config, tmp_path, repo.as_uri(), files=["**/*.xtb"], template=None,
                    keep=["**/*.grdp"], languages={"ar-XB": ""})
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 4 units (0 skipped)." in result.output
    assert lookup("Tab") == ["Oortjie"]
