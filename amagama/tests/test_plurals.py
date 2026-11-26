#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

"""Plurals are stored with all their forms (#3351)."""

import pytest
from click.testing import CliRunner
from translate.misc.multistring import multistring
from translate.storage.base import TranslationStore, TranslationUnit

from amagama import plurals
from amagama.application import amagama_server_factory
from amagama.cli import manage

RUSSIAN = ("(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && (n%100<10 || n%100>=20) "
           "? 1 : 2)")


@pytest.mark.parametrize("lang, expected", [
    ("af", (2, "(n != 1)")),
    ("pt_br", (2, "(n > 1)")),
    ("ru", (3, RUSSIAN)),
    ("sr@latin", None),
    ("xx", None),
])
def test_rule_from_the_table(lang, expected):
    found = plurals.rule(TranslationStore(), lang)
    if lang == "sr@latin":
        # Serbian's rule, by the simpler code.
        assert found and found[0] == 3
    else:
        assert found == expected


def unit(sources, targets):
    u = TranslationUnit(multistring(sources))
    u.target = multistring(targets)
    return u


def test_entries():
    rule = (3, RUSSIAN)
    assert plurals.entries(unit(["Save file"], ["Сохранить"]), rule) is None
    assert plurals.entries(unit(["%d file", "%d files"], ["%d файл", "%d файла", "%d файлов"]),
                           rule) == ([("%d file", "%d файл"), ("%d files", "%d файлов")],
                                     ["%d файл", "%d файла", "%d файлов"], 3)
    # Qt numerus: one English text, so the singular only.
    assert plurals.entries(unit(["%n file(s)"], ["%n файл", "%n файла", "%n файлов"]), rule) \
        == ([("%n file(s)", "%n файл")], ["%n файл", "%n файла", "%n файлов"], 3)
    # Fewer forms than the rule has: the missing one's pair is left out.
    assert plurals.entries(unit(["%d file", "%d files"], ["a", "b"]), rule) \
        == ([("%d file", "a")], ["a", "b"], 3)
    # No rule known: the first form, and the second for the plural.
    assert plurals.entries(unit(["%d file", "%d files"], ["a", "b", "c"]), None) \
        == ([("%d file", "a"), ("%d files", "b")], ["a", "b", "c"], 3)


def run(*args, input=None):
    result = CliRunner().invoke(manage, args, input=input)
    assert result.exit_code == 0, result.output
    return result


def lookup(client, source, lang="af"):
    response = client.get("/api/v1/en/%s/unit/%s" % (lang, source))
    assert response.status_code == 200
    return response.get_json()


@pytest.fixture
def loaded(amagama_config, tmp_path):
    path = tmp_path / "af.po"
    path.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n\n'
                    'msgid "Save file"\nmsgstr "Stoor lêer"\n\n'
                    'msgid "%d file was saved"\nmsgid_plural "%d files were saved"\n'
                    'msgstr[0] "%d lêer is gestoor"\nmsgstr[1] "%d lêers is gestoor"\n\n'
                    'msgid "%d item"\nmsgid_plural "%d items"\n'
                    'msgstr[0] "%d item"\nmsgstr[1] ""\n', encoding="utf-8")
    run("initdb", "-s", "en")
    run("build_tmdb", "-s", "en", "-t", "af", "-i", str(path))


@pytest.mark.parametrize("deployed", [False, True])
def test_lookups(loaded, deployed):
    if deployed:
        run("deploy_db", input="y\n")
    app = amagama_server_factory()
    with app.test_client() as client:
        forms = ["%d lêer is gestoor", "%d lêers is gestoor"]
        plural = lookup(client, "%d files were saved")
        assert [(s["source"], s["target"], s["forms"], s["nplurals"]) for s in plural][0] == (
            "%d files were saved", "%d lêers is gestoor", forms, 2)
        singular = lookup(client, "%d file was saved")
        assert (singular[0]["target"], singular[0]["forms"]) == ("%d lêer is gestoor", forms)
        # Not a plural: no forms.
        assert "forms" not in lookup(client, "Save file")[0]
        # A partly translated plural: the filled form only, so "%d items" has
        # no entry of its own (the singular's is a near match).
        assert [s["source"] for s in lookup(client, "%d items")] == ["%d item"]
        assert [(s["target"], s["forms"]) for s in lookup(client, "%d item")] == [
            ("%d item", ["%d item", ""])]


def test_rule_out_of_range():
    """A Plural-Forms giving a form past its nplurals isn't used."""
    from translate.storage import po
    store = po.pofile()
    store.updateheader(add=True, plural_forms="nplurals=2; plural=n==1 ? 1 : 5;")
    assert plurals.rule(store, "af") == (2, "(n != 1)")


def build(tmp_path, name, units, lang="af", header=""):
    path = tmp_path / ("%s.po" % name)
    path.write_text('msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
                    + header + "\n" + units, encoding="utf-8")
    run("build_tmdb", "-s", "en", "-t", lang, "-i", str(path))


def forms_of(pg_connection):
    with pg_connection.cursor() as cursor:
        cursor.execute("""SELECT text, forms, nplurals FROM en.targets
            WHERE forms IS NOT NULL ORDER BY text""")
        found = cursor.fetchall()
    pg_connection.commit()
    return found


def test_first_form_empty(amagama_config, tmp_path, pg_connection):
    """Arabic's first form is for 0: empty, the others are still taken."""
    run("initdb", "-s", "en")
    build(tmp_path, "ar", 'msgid "%d file"\nmsgid_plural "%d files"\n'
          + "".join('msgstr[%d] "%s"\n' % (i, "" if i == 0 else "<form %d>" % i)
                    for i in range(6)),
          lang="ar", header='"Plural-Forms: nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : '
          'n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;\\n"\n')
    forms = [""] + ["<form %d>" % i for i in range(1, 6)]
    assert forms_of(pg_connection) == [("<form 1>", forms, 6), ("<form 4>", forms, 6)]


def test_plural_after_ordinary(amagama_config, tmp_path, pg_connection):
    """A plural's forms fill the row an ordinary translation with the same
    text made first."""
    run("initdb", "-s", "en")
    build(tmp_path, "one", 'msgid "%d file"\nmsgstr "%d lêer"\n')
    build(tmp_path, "two", 'msgid "%d file"\nmsgid_plural "%d files"\n'
          'msgstr[0] "%d lêer"\nmsgstr[1] "%d lêers"\n')
    forms = ["%d lêer", "%d lêers"]
    assert forms_of(pg_connection) == [("%d lêer", forms, 2), ("%d lêers", forms, 2)]


def test_forms_without_transcripts(amagama_config, tmp_path, pg_connection):
    """A KDE transcript is dropped from the forms, as from the target."""
    run("initdb", "-s", "en")
    build(tmp_path, "kde", 'msgid "%d file"\nmsgid_plural "%d files"\n'
          'msgstr[0] "%d lêer"\nmsgstr[1] "%d lêers|/|$[x %d]"\n')
    forms = ["%d lêer", "%d lêers"]
    assert forms_of(pg_connection) == [("%d lêer", forms, 2), ("%d lêers", forms, 2)]


def test_neither_form_in_the_file(amagama_config, tmp_path, pg_connection):
    """A plural missing both the forms its rule picks is left out, and the
    rest of the file is imported."""
    run("initdb", "-s", "en")
    build(tmp_path, "af", 'msgid "Save file"\nmsgstr "Stoor lêer"\n\n'
          'msgid "%d file"\nmsgid_plural "%d files"\nmsgstr[0] "a"\nmsgstr[1] "b"\n',
          header='"Plural-Forms: nplurals=4; plural=n==1 ? 2 : 3;\\n"\n')
    with pg_connection.cursor() as cursor:
        cursor.execute("SELECT text FROM en.targets ORDER BY text")
        assert cursor.fetchall() == [("Stoor lêer",)]
    pg_connection.commit()
