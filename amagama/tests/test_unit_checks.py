#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import os
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


def test_deployed_ttk(imported, styled):
    imported('gnome', 'af', ('_Open the file', 'Maak lêer oop'), ('Save the file', 'Stoor'))
    result = CliRunner().invoke(manage, ('deploy_db',), input='y\n')
    assert result.exit_code == 0, result.output
    output = check('--ttk')
    assert "  [af] '_Open the file' -> 'Maak lêer oop' (gnome)" in output


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


def test_reject_flags(imported, pg_connection):
    imported('gnome', 'af', ('Open the %s file now', 'Maak lêer oop'),
             ('Do you want to save?', 'Do you want to save?'),
             ('Save the file.', 'Stoor die lêer'), ('Close the file.', 'Maak die lêer toe.'))
    report = unit_checks.reject(pg_connection, 'en', names=['unchanged', 'endpunc'],
                                leave_out=['printf'])
    assert (report.left_out, report.flagged) == (1, 2)
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT bit, name, left_out FROM en.check_names ORDER BY bit")
        names = cursor.fetchall()
        cursor.execute("SELECT text, checks FROM en.targets ORDER BY text")
        targets = cursor.fetchall()
    assert names == [(0, 'printf', True), (1, 'unchanged', False), (2, 'endpunc', False)]
    assert targets == [('Do you want to save?', 2), ('Maak die lêer toe.', None),
                       ('Stoor die lêer', 4)]


update = test_pairing.update


def test_update_leaves_out(update, tmp_path, pg_connection):
    url = test_pairing.git_repo(tmp_path / "repo", {"po/af.po": po(
        ('Open the %s file now', 'Maak die lêer oop'), ('Save the file', 'Stoor die lêer'),
        ('_Close the window', 'Maak die venster toe'))})
    result = update(block("x", "git", url=url, files=["po/{lang}.po"], checker="gnome"))
    assert result.exit_code == 0, result.output
    assert "Left out 1 targets that look broken." in result.output
    assert "  printf: 1 (33.33%)" in result.output
    assert "Flagged 1 targets." in result.output
    assert "  accelerators: 1 (33.33%)" in result.output
    assert "  endpunc: 0 (0.00%)" in result.output
    assert test_pairing.lookup("af", "Open the %s file now") == []
    assert test_pairing.lookup("af", "Save the file") == ["Stoor die lêer"]
    # Deployed and swapped in, with the flags.
    with pg_connection.cursor() as cursor:
        cursor.execute("""SELECT t.text, n.name FROM en.targets t
            JOIN en.check_names n ON t.checks & (1::BIGINT << n.bit) <> 0""")
        assert cursor.fetchall() == [('Maak die venster toe', 'accelerators')]
        cursor.execute("SELECT to_regclass('en.targets_lang_sid_idx') IS NOT NULL")
        assert cursor.fetchone()[0]
    pg_connection.commit()


def test_update_unknown_check(update, amagama_config, tmp_path):
    url = test_pairing.git_repo(tmp_path / "repo", {"po/af.po": po(('Save', 'Stoor'))})
    amagama_config(UNIT_CHECKS_TTK=["endpunc", "spelling"])
    result = update(block("x", "git", url=url, files=["po/{lang}.po"]))
    assert result.exit_code == 1
    assert "Not checks: spelling" in result.output


@pytest.mark.parametrize("style, source, target, failed", [
    (None, "Open the file.", "Maak die lêer oop", ["endpunc"]),
    (None, "_Open", "Oop", []),
    # GNOME marks accelerators with _.
    ("gnome", "_Open", "Oop", ["accelerators"]),
    ("kde", "&Open", "&Oop", []),
    ("kde", "&Open", "Oop", ["accelerators"]),
])
def test_ttk_checks(style, source, target, failed):
    checks = unit_checks.Checks(unit_checks.check_names(["accelerators", "endpunc"]))
    assert checks.failed(checks.mask("af", style, source, target)) == failed


def test_ttk_check_by_language():
    checks = unit_checks.Checks(["endpunc"])
    # Japanese ends a sentence with its own full stop.
    assert checks.mask("ja", None, "Save the file.", "ファイルを保存します。") == 0


def test_slow_check_left_out_on_long_text(monkeypatch):
    monkeypatch.setattr(unit_checks, "SLOW_CHECKS", {"sentencecount": 30})
    checks = unit_checks.Checks(["sentencecount"])
    assert checks.mask("af", None, "Open it. Save it.", "Maak oop en stoor.") == 1
    assert checks.mask("af", None, "Open the file now. Save the file now.",
                       "Maak die lêer nou oop en stoor dit.") == 0


def test_broken_ttk_check_passes(monkeypatch):
    def broken(self, str1, str2):
        raise KeyError(str1)
    monkeypatch.setattr(unit_checks.checks.StandardChecker, "endpunc", broken, raising=False)
    checks = unit_checks.Checks(["endpunc"])
    assert checks.mask("af", None, "Open the file.", "Maak oop") == 0


def test_check_names():
    assert unit_checks.check_names(["endpunc", "length", "printf", "endpunc"]) == [
        "printf", "length", "endpunc"]
    # A project checker's own.
    assert unit_checks.check_names(["gconf"]) == ["gconf"]
    with pytest.raises(ValueError, match="Not checks: spelling"):
        unit_checks.check_names(["spelling"])


def test_check_names_fit_a_mask(monkeypatch):
    names = unit_checks.default_names() + sorted(unit_checks.ttk_names())
    assert len(names) <= unit_checks.MAX_CHECKS
    monkeypatch.setattr(unit_checks, "MAX_CHECKS", 2)
    with pytest.raises(ValueError, match="At most 2 checks"):
        unit_checks.check_names(["printf", "braces", "tags"])


def test_default_ttk_checks():
    assert "sentencecount" in unit_checks.TTK_CHECKS
    # amaGama has its own; these need what amaGama hasn't got.
    assert not {"printf", "escapes", "unchanged", "spellcheck", "untranslated"} & set(
        unit_checks.TTK_CHECKS)


@pytest.fixture
def styled(amagama_config, tmp_path):
    """A sources file giving block gnome GNOME's checker."""
    sources = tmp_path / "sources.toml"
    sources.write_text(block("gnome", "translationproject", checker="gnome")
                       + block("odd", "translationproject"))
    amagama_config(UPDATER_SOURCES=str(sources), UNIT_CHECKS_WORKERS=1)


def test_command_ttk(imported, styled):
    imported('gnome', 'af', ('_Open the file', 'Maak die lêer oop'),
             ('Save the file.', 'Stoor die lêer.'))
    imported('odd', 'af', ('_Close the file', 'Maak die lêer toe'),
             ('Save the file.', 'Stoor die lêer'))
    output = check('--ttk', '--examples', '1')
    assert "printf: 0 (0.00%)" in output
    # Only GNOME marks accelerators with _.
    assert "accelerators: 1 (25.00%)" in output
    assert "  [af] '_Open the file' -> 'Maak die lêer oop' (gnome)" in output
    assert "endpunc: 1 (25.00%)" in output
    assert "sentencecount: 0 (0.00%)" in output


def test_command_chosen_checks(imported, styled):
    imported('gnome', 'af', ('_Open the file', 'Maak die lêer oop'),
             ('Open the %s file now', 'Maak lêer oop'))
    output = check('--check', 'accelerators', '--check', 'printf')
    assert output.splitlines()[1:] == [
        "printf: 1 (50.00%)", "  Languages: af 1 (50.0%)", "  Blocks: gnome 1",
        "  [af] 'Open the %s file now' -> 'Maak lêer oop' (gnome)",
        "accelerators: 1 (50.00%)", "  Languages: af 1 (50.0%)", "  Blocks: gnome 1",
        "  [af] '_Open the file' -> 'Maak die lêer oop' (gnome)"]


def test_command_unknown_check(imported):
    result = CliRunner().invoke(manage, ('check_units', '-s', 'en', '--check', 'spelling'))
    assert result.exit_code == 2
    assert "Not checks: spelling" in result.output


def test_command_workers(imported, styled, monkeypatch):
    # Batches of one, so the rows don't fit the first.
    monkeypatch.setattr(unit_checks, "BATCH", 1)
    imported('gnome', 'af', *[('Save file %d.' % n, 'Stoor lêer %d' % n) for n in range(10)])
    output = check('--check', 'endpunc', '--check', 'printf', '--check', 'script',
                   '--workers', '2', '--examples', '10')
    assert "endpunc: 10 (100.00%)" in output
    assert "printf: 0 (0.00%)" in output
    assert "  [af] 'Save file 9.' -> 'Stoor lêer 9' (gnome)" in output


def die(batch):
    os._exit(1)


def test_dead_check_worker(monkeypatch):
    monkeypatch.setattr(unit_checks, "BATCH", 1)
    monkeypatch.setattr(unit_checks, "_check_batch", die)
    rows = [("af", n, "Save.", "Stoor", None) for n in range(3)]
    with pytest.raises(RuntimeError, match="A check worker died"):
        list(unit_checks.masks(rows, unit_checks.Checks(["endpunc"]), workers=2))


def test_masks_read_rows_as_checked(monkeypatch):
    monkeypatch.setattr(unit_checks, "BATCH", 2)
    read = []

    def rows():
        for n in range(10):
            read.append(n)
            yield ("af", n, "Save.", "Stoor", None)
    masks = unit_checks.masks(rows(), unit_checks.Checks(["endpunc"]))
    assert next(masks)[1] == 1
    # The first batch, not the whole table.
    assert len(read) == 2
    assert len(list(masks)) == 9


def test_check_batch():
    checks = unit_checks.Checks(["endpunc"])
    unit_checks._start_worker(checks)
    assert unit_checks._check_batch([("af", 1, "Save.", "Stoor", None),
                                     ("af", 2, "Save.", "Stoor.", None)]) == [1, 0]


def test_failures_copied_in_batches(imported, monkeypatch):
    monkeypatch.setattr(unit_checks, "COPY_BATCH", 2)
    imported('gnome', 'af', *[('Save file %d.' % n, 'Stoor lêer %d' % n) for n in range(5)])
    output = check('--check', 'endpunc')
    assert "endpunc: 5 (100.00%)" in output
    assert "  Blocks: gnome 5" in output


def test_command_invalid_sources(imported, amagama_config, tmp_path):
    sources = tmp_path / "sources.toml"
    sources.write_text(block("a", "translationproject", checker="gtk"))
    amagama_config(UPDATER_SOURCES=str(sources))
    result = CliRunner().invoke(manage, ('check_units', '-s', 'en', '--ttk'))
    assert result.exit_code == 1
    assert "block a: checker must be one of" in result.output


def test_sample(imported, styled):
    imported('gnome', 'af', *[('Save file %d.' % n, 'Stoor lêer %d' % n) for n in range(5)])
    imported('odd', 'de', ('Save the file.', 'Datei speichern'), ('Open it.', 'Öffnen.'))
    output = check('--sample', 'endpunc', '--examples', '2', '--seed', '1')
    lines = output.splitlines()
    assert lines[1] == "endpunc: 6 (85.71%)"
    assert len(lines) == 5
    assert [line[:6] for line in lines[2:]] == ["  [af]", "  [af]", "  [de]"]
    assert "  [de] 'Save the file.' -> 'Datei speichern' (odd)" in lines
    # The same seed, the same sample.
    assert check('--sample', 'endpunc', '--examples', '2', '--seed', '1') == output


def test_update_leaves_out_promoted_check(update, amagama_config, tmp_path):
    url = test_pairing.git_repo(tmp_path / "repo", {"po/af.po": po(
        ('Save the file.', 'Stoor die lêer'), ('Close every window.', 'Maak elke venster toe.'))})
    amagama_config(UNIT_CHECKS_LEAVE_OUT=["endpunc"])
    result = update(block("x", "git", url=url, files=["po/{lang}.po"]))
    assert result.exit_code == 0, result.output
    assert "Left out 1 targets that look broken." in result.output
    assert "  endpunc: 1 (50.00%)" in result.output
    assert "  [af] 'Save the file.' -> 'Stoor die lêer' (x)" in result.output
    assert test_pairing.lookup("af", "Save the file.") == []
    assert test_pairing.lookup("af", "Close every window.") == ["Maak elke venster toe."]


def test_by_source(imported, styled, tmp_path):
    imported('gnome', 'af', ('_Open the file', 'Maak die lêer oop'), ('Save the file.', 'Stoor.'))
    imported('gnome', 'de', ('Save the file.', 'Speichern'))
    imported('odd', 'af', ('_Close the file', 'Maak die lêer toe'), ('Save the file.', 'Stoor.'))
    table = tmp_path / "by-source.tsv"
    check('--check', 'accelerators', '--check', 'endpunc', '--by-source', str(table))
    assert table.read_text(encoding="utf-8").splitlines() == [
        "source\tblock\tlanguage\ttargets\taccelerators\tendpunc",
        "en\tgnome\taf\t2\t1\t0",
        "en\tgnome\tde\t1\t0\t1",
        "en\todd\taf\t2\t0\t0"]
    check('--check', 'endpunc', '-t', 'de', '--by-source', str(table))
    assert table.read_text(encoding="utf-8").splitlines() == [
        "source\tblock\tlanguage\ttargets\tendpunc", "en\tgnome\tde\t1\t1"]


def test_by_source_to_stdout(imported, styled, tmp_path):
    imported('gnome', 'af', ('Save the file.', 'Stoor.'))
    runner = CliRunner()
    assert runner.invoke(manage, ('initdb', '-s', 'fr')).exit_code == 0
    path = tmp_path / 'fr-af.po'
    path.write_text(po(('Enregistrer.', 'Stoor')), encoding='utf-8')
    assert runner.invoke(manage, ('build_tmdb', '-s', 'fr', '-t', 'af', '-i', str(path),
                                  '--block', 'odd')).exit_code == 0
    result = runner.invoke(manage, ('check_units', '-s', 'en', '-s', 'fr', '--check', 'endpunc',
                                    '--by-source', '-'))
    assert result.exit_code == 0, result.output
    # One table, the report beside it.
    assert result.stdout.splitlines() == [
        "source\tblock\tlanguage\ttargets\tendpunc",
        "en\tgnome\taf\t1\t0",
        "fr\todd\taf\t1\t1"]
    assert "Checked 'en': 1 targets in 1 languages." in result.stderr
    assert "Checked 'fr': 1 targets in 1 languages." in result.stderr


def test_suggested_weights():
    report = Report()
    report.targets = 12000
    report.source_targets = {("good", "af"): 5000, ("good", "de"): 5000, ("poor", "af"): 1000,
                             ("small", "af"): 999, ("weighted", "af"): 1001}
    report.weighed.update({None: 1000, "good": 400, "poor": 300, "small": 999, "weighted": 200})
    # 8.33% of all targets fail: poor 30% loses 2 x 21.67%, weighted 20% from
    # its 0.8; good is better than average, small too small to judge.
    assert report.suggested_weights({"weighted": 0.8}) == [
        ("poor", 1.0, 0.55, 0.3), ("weighted", 0.8, 0.6, 200 / 1001)]
    # At most half.
    report.weighed["poor"] = 1000
    assert report.suggested_weights()[0] == ("poor", 1.0, 0.5, 1.0)
    # Half, up to a step of 0.05; none without a lower step.
    assert report.suggested_weights({"poor": 0.33})[0] == ("poor", 0.33, 0.2, 1.0)
    assert report.suggested_weights({"poor": 0.08})[0] == ("poor", 0.08, 0.05, 1.0)
    assert [w[0] for w in report.suggested_weights({"poor": 0.04})] == ["weighted"]


def test_command_suggest_weights(imported, amagama_config, tmp_path, monkeypatch):
    monkeypatch.setattr(unit_checks, "WEIGHT_MIN_TARGETS", 1)
    sources = tmp_path / "sources.toml"
    sources.write_text(block("good", "translationproject")
                       + block("poor", "translationproject", weight=0.8))
    amagama_config(UPDATER_SOURCES=str(sources), UNIT_CHECKS_WORKERS=1)
    imported('good', 'af', ('Save the file.', 'Stoor die lêer.'), ('Open it.', 'Maak oop.'))
    imported('poor', 'af', ('Close the file.', 'Maak die lêer toe'), ('Quit now.', 'Hou op.'))
    output = check('--check', 'endpunc', '--suggest-weights')
    assert "Suggested weights, by endpunc (25.00% of all targets fail): " in output
    assert "  poor: 0.8 -> 0.4 (50.00% fail)" in output
    # By the checks rebuilds leave out, which none fail.
    output = check('--suggest-weights')
    assert "Suggested weights, by printf, braces, script (0.00% of all targets fail): none" \
        in output


def test_update_suggests_weights(update, amagama_config, tmp_path, monkeypatch):
    monkeypatch.setattr(unit_checks, "WEIGHT_MIN_TARGETS", 1)
    url = test_pairing.git_repo(tmp_path / "repo", {"po/af.po": po(
        ('Open the %s file now', 'Maak die lêer oop'), ('Save the file', 'Stoor die lêer'))})
    url2 = test_pairing.git_repo(tmp_path / "repo2", {"po/af.po": po(
        ('Close every window', 'Maak elke venster toe'))})
    amagama_config(UNIT_CHECKS_SUGGEST_WEIGHTS=True)
    result = update(block("x", "git", url=url, files=["po/{lang}.po"])
                    + block("y", "git", url=url2, files=["po/{lang}.po"]))
    assert result.exit_code == 0, result.output
    assert "Suggested weights, by printf, braces, script (33.33% of all targets fail): \n" \
        "  x: 1 -> 0.65 (50.00% fail)" in result.output
