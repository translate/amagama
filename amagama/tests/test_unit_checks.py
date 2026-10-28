#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import random

import pytest
from click.testing import CliRunner

from amagama import unit_checks
from amagama.cli import manage
from amagama.tests import test_pairing
from amagama.tests.test_updater import block
from amagama.unit_checks import Report, failed_checks, length_step, script, target_script


@pytest.mark.parametrize("source, target, lang, failed", [
    ("Open %s file", "Maak %s lêer oop", "af", []),
    ("Open %s file", "Maak lêer oop", "af", ["printf"]),
    ("Open %(name)s", "Maak %(naam)s oop", "af", ["printf"]),
    ("Open %1$s and %2$s", "%2$s en %1$s", "af", []),
    # Placeholders may change places; numbered ones are KDE's and Qt's.
    ("This item costs %1 %2.", "Isto custa %2 %1.", "gl", []),
    ("In %1, a mount point for %2", "În %s, un punct de montare pentru %s", "ro", ["printf"]),
    ("1 minute in the future", "%d menit yang akan datang", "id", ["printf"]),
    ("Products", "%PRODUCTNAME ", "sq", ["printf"]),
    # A date format's fields follow the language.
    ("Today %I:%M %p", "Vandaag %H:%M", "nl", []),
    # Percentages: Basque writes %95, and "100% line" has no placeholder.
    ("Error margin (95%)", "Errore-tartea (%95)", "eu", []),
    ("to 50% maximum brightness", "distira maximoaren %50era", "eu", []),
    ("a single 100% line", "einer einzigen 100-%-Zeile", "de", []),
    ("%d%%", "%Id%%", "ar", []),
    ("Tickets to watch: {$a}", "Kaartjies wat gevolg word", "af", ["braces"]),
    ("{$a->name} has {$a->count}", "{$a->name} het {$ a-> count}", "af", ["braces"]),
    ("Save the {0} image", "Stoor die prent", "af", ["braces"]),
    ("Invalid \"{}\" in \"{}\"", "Ongeldige \"{}\"", "af", ["braces"]),
    ("Click <b>%{change_url}</b>", "Klik <b>%{change..._url}</b>", "af", ["braces"]),
    # Other braces are often text to translate.
    ("{count} files", "{aantal} lêers", "af", []),
    ("-o {filename}", "-o {lêernaam}", "af", []),
    ("\\textsc{small caps}", "\\textsc{klein hoofletters}", "af", []),
    # ICU message format translates the text inside its braces.
    ("{count, plural, one {# file} other {# files}}",
     "{count, plural, one {# lêer} other {# lêers}}", "af", []),
    ("<b>Bold</b> text", "<b>Vet</b> teks", "af", []),
    ("<b>Bold</b> text", "Vet teks", "af", ["tags"]),
    # An attribute may be translated.
    ('<img alt="Logo" src="a.png"/>', '<img alt="Embleem" src="a.png"/>', "af", []),
    ("Line\\n", "Lyn", "af", ["escapes"]),
    ("Do you want to save?", "Do you want to save?", "af", ["unchanged"]),
    ("Do you want to save?", "Do you want to save?", "en_gb", []),
    # A name or a term may be the same.
    ("Firefox", "Firefox", "af", []),
    ("Open file", "Open file", "af", []),
])
def test_checks(source, target, lang, failed):
    assert failed_checks(source, target, lang) == failed


@pytest.mark.parametrize("text, expected", [
    ("Lêer", "LATIN"),
    ("Файл", "CYRILLIC"),
    ("මකන්න", "SINHALA"),
    ("ファイル を開く", "KATAKANA"),
    ("Open Файл файл", "CYRILLIC"),
    ("%s 123 …", None),
    ("%(name)s <b>{count}</b> &amp; ගොනුව", "SINHALA"),
    ("ｆｉｌｅ", None),
])
def test_script(text, expected):
    assert script(text) == expected


@pytest.mark.parametrize("source, target, expected", [
    ("Rotate this clip", "이 클립 회전", "HANGUL"),
    ("Open the file", "Ava fail", "LATIN"),
    ("Atmosphere", "වායුගෝලය", "SINHALA"),
    # Kept as they are: a symbol or a name in its own script.
    ("μ", "μ", None),
    ("Ελληνικά", "Ελληνικά", None),
    ("а, б, .., аа, бб (Serbian)", "а, б, .., аа, бб (Serbo)", None),
    ("%s 123", "%s 123", None),
])
def test_target_script(source, target, expected):
    assert target_script(source, target, "LATIN") == expected


def test_length_step():
    assert length_step("short", "much much longer") is None
    assert length_step("a" * 20, "") is None
    assert length_step("a" * 20, "b" * 20) == 0
    assert length_step("a" * 20, "b" * 80) == 2
    assert length_step("a" * 80, "b" * 20) == -2


def test_examples_at_random():
    report = Report(examples=10, rng=random.Random(1))
    for tid in range(1000):
        report.add("printf", "af", tid, "source", "target")
    tids = [example[3] for example in report.examples["printf"]]
    assert len(tids) == 10
    # Not just the first failures.
    assert max(tids) >= 500
    assert report.failures("printf") == 1000


def po(*units):
    return 'msgid ""\nmsgstr "Content-Type: text/plain; charset=UTF-8\\n"\n' + ''.join(
        '\nmsgid "%s"\nmsgstr "%s"\n' % unit for unit in units)


@pytest.fixture
def imported(amagama_config, tmp_path):
    runner = CliRunner()
    assert runner.invoke(manage, ('initdb', '-s', 'en')).exit_code == 0

    def imported(block, lang, *units):
        path = tmp_path / ('%s-%s.po' % (block, lang))
        path.write_text(po(*units), encoding='utf-8')
        result = runner.invoke(manage, ('build_tmdb', '-s', 'en', '-t', lang, '-i', str(path),
                                        '--block', block))
        assert result.exit_code == 0, result.output
    return imported


def check(*args):
    result = CliRunner().invoke(manage, ('check_units', '-s', 'en') + args)
    assert result.exit_code == 0, result.output
    return result.output


def test_command(imported, monkeypatch):
    # Three targets can't show a share under 1%.
    monkeypatch.setattr(unit_checks, "SCRIPT_MIN_SHARE", 0.5)
    monkeypatch.setattr(unit_checks, "SCRIPT_MIN_TARGETS", 0)
    imported('gnome', 'et',
             ('Open the %s file now', 'Ava %s fail kohe'),
             ('Delete the selected file', 'Kustuta valitud fail'),
             ('Save all of the open files', 'Salvesta kõik avatud failid'))
    imported('odd', 'et',
             ('Delete everything', 'මකන්න'),
             ('Close the %s window now', 'Sulge aken'),
             ('Do you want to save?', 'Do you want to save?'))
    output = check()
    assert "Checked 'en': 6 targets in 1 languages." in output
    assert "printf: 1 (16.67%)" in output
    assert "  Languages: et 1 (16.7%)" in output
    assert "  Blocks: odd 1" in output
    assert "  [et] 'Close the %s window now' -> 'Sulge aken' (odd)" in output
    assert "unchanged: 1 (16.67%)" in output
    assert "script: 1 (16.67%)" in output
    assert "  [et] 'Delete everything' -> 'මකන්න' (odd)" in output
    assert "tags: 0 (0.00%)" in output


def test_length(imported):
    imported('gnome', 'af',
             ('Delete the selected file', 'Skrap die gekose lêer'),
             ('Save all of the open files', 'Stoor al die oop lêers'),
             ('Close all of the windows', 'Maak al die vensters toe'),
             ('Show the hidden files too', 'Wys ook die versteekte lêers, '
              'en al die ander lêers wat jy nog nooit gesien het nie'))
    output = check('-t', 'af')
    assert "length: 1 (25.00%)" in output
    assert "script: 0 (0.00%)" in output


def test_languages_chosen(imported):
    imported('gnome', 'af', ('Open the %s file now', 'Maak lêer oop'))
    imported('gnome', 'de', ('Open the %s file now', 'Datei öffnen'))
    output = check('-t', 'de', '--examples', '0')
    assert "Checked 'en': 1 targets in 1 languages." in output
    assert "  Languages: de 1 (100.0%)" in output
    assert "[de]" not in output


def test_without_imports(imported, pg_connection):
    imported('gnome', 'af', ('Open the %s file now', 'Maak lêer oop'))
    with pg_connection.cursor() as cursor:
        cursor.execute("DROP TABLE en.target_imports")
    pg_connection.commit()
    output = check()
    assert "  [af] 'Open the %s file now' -> 'Maak lêer oop'\n" in output
    assert "Blocks:" not in output


def test_deployed(imported):
    imported('gnome', 'af', ('Open the %s file now', 'Maak lêer oop'), ('Save the file', 'Stoor'))
    result = CliRunner().invoke(manage, ('deploy_db',), input='y\n')
    assert result.exit_code == 0, result.output
    output = check()
    assert "Checked 'en': 2 targets in 1 languages." in output
    assert "  [af] 'Open the %s file now' -> 'Maak lêer oop' (gnome)" in output


def test_script_judged_in_larger_languages(imported, monkeypatch):
    monkeypatch.setattr(unit_checks, "SCRIPT_MIN_SHARE", 0.5)
    imported('gnome', 'et', ('Open the file', 'Ava fail'), ('Close the file', 'Sulge fail'))
    imported('odd', 'et', ('Atmosphere', 'වායුගෝලය'))
    assert "script: 0 (0.00%)" in check()
    monkeypatch.setattr(unit_checks, "SCRIPT_MIN_TARGETS", 3)
    assert "script: 1 (33.33%)" in check()


def test_reject(imported, pg_connection, monkeypatch):
    monkeypatch.setattr(unit_checks, "SCRIPT_MIN_TARGETS", 0)
    monkeypatch.setattr(unit_checks, "SCRIPT_MIN_SHARE", 0.5)
    imported('gnome', 'et',
             ('Open the %s file now', 'Ava %s fail kohe'),
             ('Delete the selected file', 'Kustuta valitud fail'),
             ('Save all of the open files', 'Salvesta kõik avatud failid'))
    imported('odd', 'et',
             ('Delete everything', 'මකන්න'),
             ('Close the %s window now', 'Sulge aken'),
             ('Do you want to save?', 'Do you want to save?'))
    report = unit_checks.reject(pg_connection, 'en')
    assert {name: report.failures(name) for name in unit_checks.FILTER} == {
        'printf': 1, 'braces': 0, 'script': 1}
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT text FROM en.targets ORDER BY text")
        kept = [text for text, in cursor.fetchall()]
        cursor.execute("""SELECT COUNT(*) FROM en.target_imports l
            LEFT JOIN en.targets t ON t.tid = l.tid WHERE t.tid IS NULL""")
        orphans = cursor.fetchone()[0]
    # Left in English is only reported.
    assert kept == ['Ava %s fail kohe', 'Do you want to save?', 'Kustuta valitud fail',
                    'Salvesta kõik avatud failid']
    assert orphans == 0


update = test_pairing.update


def test_update_leaves_out(update, tmp_path):
    url = test_pairing.git_repo(tmp_path / "repo", {"po/af.po": po(
        ('Open the %s file now', 'Maak die lêer oop'), ('Save the file', 'Stoor die lêer'))})
    result = update(block("x", "git", url=url, files=["po/{lang}.po"]))
    assert result.exit_code == 0, result.output
    assert "Left out 1 targets that look broken." in result.output
    assert "  printf: 1 (50.00%)" in result.output
    assert test_pairing.lookup("af", "Open the %s file now") == []
    assert test_pairing.lookup("af", "Save the file") == ["Stoor die lêer"]
