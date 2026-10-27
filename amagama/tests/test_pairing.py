#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import os
import subprocess

import pytest
from click.testing import CliRunner
from translate.storage import aresource, jsonl10n, php, properties, tmx

from amagama.application import amagama_server_factory
from amagama.cli import manage
from amagama.tests.test_updater import block
from amagama.updater.pairing import paired_store, without_ids


def read(store_class, text, **kwargs):
    store = store_class(**kwargs)
    store.parse(text.encode("utf-8"))
    return store


def pairs(store):
    return [(unit.source, unit.target) for unit in store.units]


def test_android():
    english = read(aresource.AndroidResourceFile,
                   '<resources><string name="save">Save file</string>'
                   '<string name="open">Open file</string>'
                   '<plurals name="files"><item quantity="one">%d file</item>'
                   '<item quantity="other">%d files</item></plurals></resources>')
    afrikaans = read(aresource.AndroidResourceFile,
                     '<resources><string name="save">Stoor lêer</string>'
                     '<string name="gone">Weg</string>'
                     '<plurals name="files"><item quantity="one">%d lêer</item>'
                     '<item quantity="other">%d lêers</item></plurals></resources>')
    # Untranslated "open" isn't in the translation; "gone" has no English; plurals are left out.
    store = paired_store(afrikaans, english)
    assert pairs(store) == [("Save file", "Stoor lêer")]
    # ...and counted, to be reported as skipped.
    assert store.plurals == 1


def test_php():
    english = read(php.phpfile, "<?php\n$string['save'] = 'Save file';\n")
    afrikaans = read(php.phpfile, "<?php\n$string['save'] = 'Stoor lêer';\n")
    assert pairs(paired_store(afrikaans, english)) == [("Save file", "Stoor lêer")]


def test_joomla_ini():
    english = read(properties.propfile, 'COM_SAVE="Save file"\n', personality="joomla")
    afrikaans = read(properties.propfile, 'COM_SAVE="Stoor lêer"\n', personality="joomla")
    assert pairs(paired_store(afrikaans, english)) == [("Save file", "Stoor lêer")]


def test_json_exclude_ids():
    english = read(jsonl10n.JsonFile, '{"@metadata": {"authors": ["A"]}, "save": "Save file"}')
    afrikaans = read(jsonl10n.JsonFile, '{"@metadata": {"authors": ["B"]}, "save": "Stoor lêer"}')
    assert pairs(paired_store(afrikaans, english, ["@metadata*"])) == [("Save file", "Stoor lêer")]


def test_language_in_ids():
    english = read(jsonl10n.JsonFile, '{"main": {"en": {"identity": {"language": "en"}, '
                   '"names": {"de": "German", "af": "Afrikaans"}}}}')
    afrikaans = read(jsonl10n.JsonFile, '{"main": {"af": {"identity": {"language": "af"}, '
                     '"names": {"de": "Duits", "af": "Afrikaans"}}}}')
    # Without it, no id matches.
    assert pairs(paired_store(afrikaans, english)) == []
    assert pairs(paired_store(afrikaans, english, ["main.*.identity.*"], True)) == [
        ("German", "Duits"), ("Afrikaans", "Afrikaans")]


def test_language_in_ids_rails_and_none():
    english = read(jsonl10n.JsonFile, '{"en-GB": {"save": "Save"}}')
    afrikaans = read(jsonl10n.JsonFile, '{"af": {"save": "Stoor"}}')
    assert pairs(paired_store(afrikaans, english, (), True)) == [("Save", "Stoor")]
    # No segment names English in every id: ids are matched as they are.
    english = read(jsonl10n.JsonFile, '{"save": "Save", "open": "Open"}')
    afrikaans = read(jsonl10n.JsonFile, '{"save": "Stoor"}')
    assert pairs(paired_store(afrikaans, english, (), True)) == [("Save", "Stoor")]
    assert pairs(paired_store(afrikaans, read(jsonl10n.JsonFile, '{}'), (), True)) == []


def test_signal_descriptions():
    english = read(jsonl10n.JsonFile,
                   '{"save": {"messageformat": "Save file", "description": "Button"}}')
    afrikaans = read(jsonl10n.JsonFile,
                     '{"save": {"messageformat": "Stoor lêer", "description": "Button"}}')
    assert pairs(paired_store(afrikaans, english, ["*.description"])) == [
        ("Save file", "Stoor lêer")]


def test_same_as_english_kept():
    english = read(jsonl10n.JsonFile, '{"ok": "OK"}')
    afrikaans = read(jsonl10n.JsonFile, '{"ok": "OK"}')
    assert pairs(paired_store(afrikaans, english)) == [("OK", "OK")]


@pytest.mark.parametrize("text", ['{"save": ""}', '{}'])
def test_empty(text):
    english = read(jsonl10n.JsonFile, '{"save": "Save file"}')
    assert pairs(paired_store(read(jsonl10n.JsonFile, text), english)) == []


def test_units_import_as_translated():
    english = read(jsonl10n.JsonFile, '{"save": "Save file"}')
    afrikaans = read(jsonl10n.JsonFile, '{"save": "Stoor lêer"}')
    unit = paired_store(afrikaans, english).units[0]
    assert unit.istranslatable() and unit.istranslated()


def git_repo(path, files):
    for name, text in files.items():
        (path / name).parent.mkdir(parents=True, exist_ok=True)
        (path / name).write_text(text, encoding="utf-8")

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.org",
                        "-C", str(path)] + list(args), check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("add", ".")
    git("commit", "-q", "-m", "l10n")
    return path.as_uri()


@pytest.fixture
def update(amagama_config, tmp_path):
    def update(sources):
        path = tmp_path / "sources.toml"
        path.write_text(sources)
        amagama_config(UPDATER_SOURCES=str(path), UPDATER_CACHE_DIR=str(tmp_path / "cache"),
                       UPDATER_WORKERS=1)
        return CliRunner().invoke(manage, ("update", "-s", "en"))
    return update


def lookup(lang, source):
    app = amagama_server_factory()
    with app.test_client() as client:
        response = client.get("/api/v1/en/%s/unit/%s" % (lang, source))
    return [c["target"] for c in response.get_json()] if response.status_code == 200 else []


def android(name, text):
    return '<resources><string name="%s">%s</string></resources>' % (name, text)


def test_update_android(update, tmp_path):
    url = git_repo(tmp_path / "repo", {
        "res/values/strings.xml": android("save", "Save file"),
        "res/values-af/strings.xml": android("save", "Stoor lêer"),
        "res/values-pt-rBR/strings.xml": android("save", "Salvar arquivo"),
        "res/values-night/strings.xml": android("save", "Save file"),
        "res/values-af-watch/strings.xml": android("save", "Stoor"),
        "res/values-en-rXC/strings.xml": android("save", "[Šåvé ƒîļé]"),
    })
    result = update(block("android", "git", url=url, format="android",
                          files=["res/values-{lang}/strings.xml"],
                          template="res/values/strings.xml"))
    assert result.exit_code == 0, result.output
    assert "Imported 2 files: 2 units (0 skipped)." in result.output
    assert lookup("af", "Save file") == ["Stoor lêer"]
    assert lookup("pt_BR", "Save file") == ["Salvar arquivo"]
    assert lookup("en_XC", "Save file") == []


def test_update_android_plurals_skipped(update, tmp_path):
    plurals = ('<resources><plurals name="files"><item quantity="one">%s</item>'
               '<item quantity="other">%s</item></plurals></resources>')
    url = git_repo(tmp_path / "repo", {
        "res/values/plurals.xml": plurals % ("%d file", "%d files"),
        "res/values-af/plurals.xml": plurals % ("%d lêer", "%d lêers"),
    })
    result = update(block("android", "git", url=url, format="android",
                          files=["res/values-{lang}/plurals.xml"],
                          template="res/values/plurals.xml"))
    assert result.exit_code == 0, result.output
    assert "Imported 1 files: 0 units (1 skipped)." in result.output


def test_update_json_template_among_files(update, tmp_path):
    url = git_repo(tmp_path / "repo", {
        "i18n/en.json": '{"@metadata": {"authors": ["A"]}, "save": "Save file"}',
        "i18n/af.json": '{"@metadata": {"authors": ["B"]}, "save": "Stoor lêer"}',
        "i18n/qqq.json": '{"save": "Label of the save button"}',
    })
    result = update(block("wiki", "git", url=url, format="json", files=["i18n/{lang}.json"],
                          exclude=["i18n/qqq.json"], template="i18n/en.json",
                          exclude_ids=["@metadata*"]))
    assert result.exit_code == 0, result.output
    # en.json is the template, not an import of its own.
    assert "Imported 1 files: 1 units (0 skipped)." in result.output
    assert lookup("af", "Save file") == ["Stoor lêer"]


def test_update_language_in_ids(update, tmp_path):
    url = git_repo(tmp_path / "repo", {
        "main/en/names.json": '{"main": {"en": {"names": {"de": "German"}}}}',
        "main/af/names.json": '{"main": {"af": {"names": {"de": "Duits"}}}}',
    })
    result = update(block("cldr", "git", url=url, format="json", files=["main/{lang}/names.json"],
                          template="main/en/names.json", language_in_ids=True))
    assert result.exit_code == 0, result.output
    assert lookup("af", "German") == ["Duits"]


def test_update_missing_template(update, tmp_path):
    url = git_repo(tmp_path / "repo", {
        "en/moodle.php": "<?php\n$string['save'] = 'Save file';\n",
        "af/moodle.php": "<?php\n$string['save'] = 'Stoor lêer';\n",
        "af/extra.php": "<?php\n$string['x'] = 'Ekstra';\n",
    })
    result = update(block("moodle", "git", url=url, format="php", files=["{lang}/*.php"],
                          template="en/*.php"))
    assert result.exit_code == 0, result.output
    assert "Couldn't read 1 files:" in result.output
    assert "moodle/af/extra.php: no English template extra.php" in result.output
    assert lookup("af", "Save file") == ["Stoor lêer"]


def test_bilingual_exclude_ids():
    store = read(tmx.tmxfile,
                 '<tmx version="1.4"><header srclang="en"/><body>'
                 '<tu tuid="app:save"><tuv xml:lang="en"><seg>Save file</seg></tuv>'
                 '<tuv xml:lang="af"><seg>Stoor lêer</seg></tuv></tu>'
                 '<tu tuid="site:blog"><tuv xml:lang="en"><seg>Read the blog</seg></tuv>'
                 '<tuv xml:lang="af"><seg>Lees die blog</seg></tuv></tu></body></tmx>')
    assert pairs(without_ids(store, ["site:*"])) == [("Save file", "Stoor lêer")]


def test_template_reread_when_changed(tmp_path):
    from amagama.updater.pipeline import template_store
    path = tmp_path / "en.json"
    path.write_text('{"save": "Save"}')
    assert [u.target or u.source for u in template_store(str(path), "json").units] == ["Save"]
    path.write_text('{"save": "Save file", "open": "Open"}')
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 10**9))
    assert [u.target or u.source for u in template_store(str(path), "json").units] == [
        "Save file", "Open"]
