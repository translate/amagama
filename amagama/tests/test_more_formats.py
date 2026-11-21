#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Flutter's ARB, PO keyed by id, Laravel, Compose Multiplatform,
WebExtension and .NET RESX files, each read with its English template."""

import json

import pytest

from amagama.tests.test_pairing import git_repo, lookup, update  # noqa: F401 - fixture
from amagama.tests.test_updater import block
from amagama.updater import crowdin, repositories, transifex, weblate


def arb(lang, **messages):
    data = {"@@locale": lang}
    for key, value in messages.items():
        data[key] = value
        data["@" + key] = {"description": "A note", "placeholders": {"name": {}}}
    return json.dumps(data, ensure_ascii=False)


def po_mono(*entries, charset="UTF-8"):
    text = 'msgid ""\nmsgstr "Content-Type: text/plain; charset=%s\\n"\n' % charset
    for key, value, *fuzzy in entries:
        text += '\n%smsgid "%s"\nmsgstr "%s"\n' % ("#, fuzzy\n" if fuzzy else "", key, value)
    return text


def laravel(**messages):
    return "<?php\n\nreturn [\n%s];\n" % "".join(
        "    '%s' => '%s',\n" % (key, value) for key, value in messages.items())


def resources(**messages):
    return "<resources>%s</resources>" % "".join(
        '<string name="%s">%s</string>' % item for item in messages.items())


def webextension(**messages):
    return json.dumps({key: {"message": value, "description": "A note"}
                       for key, value in messages.items()}, ensure_ascii=False)


def resx(**messages):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<root>%s</root>' % "".join(
        '<data name="%s" xml:space="preserve"><value>%s</value></data>' % item
        for item in messages.items()))


# (format, files, mask, template): each with a key missing from the English.
CASES = [
    ("arb", {"lib/l10n/app_en.arb": arb("en", hello="Hello {name}", save="Save"),
             "lib/l10n/app_af.arb": arb("af", hello="Hallo {name}", save="Stoor",
                                        gone="Weg"),
             "lib/l10n/intl_pt_BR.arb": arb("pt_BR", save="Salvar")},
     ["lib/l10n/app_{lang}.arb", "lib/l10n/intl_{lang}.arb"], "lib/l10n/app_en.arb"),
    ("po-mono", {"po/en.po": po_mono(("hello", "Hello {name}"), ("save", "Save")),
                 "po/af.po": po_mono(("hello", "Hallo {name}"), ("save", ""),
                                     ("gone", "Weg")),
                 "po/pt_BR.po": po_mono(("save", "Salvar"), ("hello", "Olá", True))},
     ["po/{lang}.po"], "po/en.po"),
    ("laravel", {"lang/en/app.php": laravel(hello="Hello :name", save="Save"),
                 "lang/af/app.php": laravel(hello="Hallo :name", save="Stoor", gone="Weg"),
                 "lang/pt_BR/app.php": laravel(save="Salvar")},
     ["lang/{lang}/app.php"], "lang/en/app.php"),
    ("compose", {"src/composeResources/values/strings.xml":
                 resources(hello="Hello %1$s", save="Save"),
                 "src/composeResources/values-af/strings.xml":
                 resources(hello="Hallo %1$s", save="Stoor", gone="Weg"),
                 "src/composeResources/values-pt-rBR/strings.xml": resources(save="Salvar"),
                 "src/composeResources/values-night/strings.xml": resources(save="Save")},
     ["src/composeResources/values-{lang}/strings.xml"],
     "src/composeResources/values/strings.xml"),
    ("webextension", {"_locales/en/messages.json":
                      webextension(hello="Hello $NAME$", save="Save"),
                      "_locales/af/messages.json":
                      webextension(hello="Hallo $NAME$", save="Stoor", gone="Weg"),
                      "_locales/pt_BR/messages.json": webextension(save="Salvar")},
     ["_locales/{lang}/messages.json"], "_locales/en/messages.json"),
    ("resx", {"Strings/Resources.resx": resx(hello="Hello {0}", save="Save"),
              "Strings/Resources.af.resx": resx(hello="Hallo {0}", save="Stoor", gone="Weg"),
              "Strings/Resources.pt-BR.resx": resx(save="Salvar")},
     ["Strings/Resources.{lang}.resx"], "Strings/Resources.resx"),
]


@pytest.mark.parametrize("format, files, masks, template", CASES, ids=[c[0] for c in CASES])
def test_update(update, tmp_path, format, files, masks, template):  # noqa: F811 - fixture
    url = git_repo(tmp_path / "repo", files)
    result = update(block("app", "git", url=url, format=format, files=masks, template=template))
    assert result.exit_code == 0, result.output
    assert "Imported 2 files" in result.output, result.output
    assert lookup("pt_BR", "Save") == ["Salvar"]
    if format == "po-mono":
        # An empty msgstr isn't a translation, nor is a fuzzy one.
        assert lookup("af", "Save") == []
        assert lookup("pt_BR", "Hello {name}") == []
        assert lookup("af", "Hello {name}") == ["Hallo {name}"]
        return
    assert lookup("af", "Save") == ["Stoor"]
    placeholder = {"arb": "{name}", "laravel": ":name", "compose": "%1$s",
                   "webextension": "$NAME$", "resx": "{0}"}[format]
    assert lookup("af", "Hello %s" % placeholder) == ["Hallo %s" % placeholder]


def test_po_mono_charset(update, tmp_path):  # noqa: F811 - fixture
    (tmp_path / "repo/po").mkdir(parents=True)
    (tmp_path / "repo/po/de.po").write_bytes(
        po_mono(("save", "Größe speichern"), charset="ISO-8859-1").encode("iso-8859-1"))
    url = git_repo(tmp_path / "repo", {"po/en.po": po_mono(("save", "Save"))})
    result = update(block("app", "git", url=url, format="po-mono", files=["po/{lang}.po"],
                          template="po/en.po"))
    assert result.exit_code == 0, result.output
    assert lookup("de", "Save") == ["Größe speichern"]


@pytest.mark.parametrize("path, expected", [
    ("app/src/commonMain/composeResources/values-af/strings.xml", "compose"),
    ("app/src/main/res/values-af/strings.xml", "android"),
    ("extension/_locales/af/messages.json", "webextension"),
    ("lib/l10n/app_af.arb", "arb"),
    ("Strings/Resources.af.resx", "resx"),
])
def test_format_by_path(path, expected):
    assert repositories.format_of(path) == expected


def test_generator_types():
    assert {crowdin.TYPES[name] for name in ("arb", "chrome", "resx")} == \
        {"arb", "webextension", "resx"}
    assert {weblate.FORMATS[transifex.TYPES[name]] for name in ("CHROME", "RESX")} == \
        {"webextension", "resx"}
