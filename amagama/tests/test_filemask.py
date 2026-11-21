#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest

from amagama.updater.filemask import Selection, compile_mask, fill, wildcards
from amagama.updater.sources import Block


@pytest.mark.parametrize("masks, path, lang", [
    (["po/{lang}.po"], "po/af.po", "af"),
    (["po/{lang}.po"], "po/pt_BR.po", "pt_BR"),
    (["po/{lang}.po"], "po/sr@latin.po", "sr@latin"),
    (["po/{lang}.po"], "po/mni@meiteimayek.po", "mni@meiteimayek"),
    (["po/{lang}.po"], "po/sr@ijekavianlatin.po", "sr@ijekavianlatin"),
    (["{lang}/*.po"], "ca@valencia/app.po", "ca@valencia"),
    # Qt: the language follows the last underscore that starts a code.
    (["translations/*_{lang}.ts"], "translations/qtbase_af.ts", "af"),
    (["translations/*_{lang}.ts"], "translations/qt_help_af.ts", "af"),
    (["translations/*_{lang}.ts"], "translations/qtbase_pt_BR.ts", "pt_BR"),
    (["translations/*_{lang}.ts"], "translations/qtbase_zh_TW.ts", "zh_TW"),
    # Odoo: one i18n directory per addon.
    (["addons/*/i18n/{lang}.po"], "addons/sale/i18n/fr_BE.po", "fr_BE"),
    # Django: every locale directory, anywhere.
    (["**/locale/{lang}/LC_MESSAGES/django.po"],
     "django/contrib/admin/locale/de/LC_MESSAGES/django.po", "de"),
    (["**/locale/{lang}/LC_MESSAGES/django.po"], "locale/de/LC_MESSAGES/django.po", "de"),
    # Android qualifiers; values-night isn't a language.
    (["res/values-{lang}/strings.xml"], "res/values-pt-rBR/strings.xml", "pt-rBR"),
    (["res/values-{lang}/strings.xml"], "res/values-b+sr+Latn/strings.xml", "b+sr+Latn"),
    (["res/values-{lang}/strings.xml"], "res/values-night/strings.xml", None),
    # Apple: Base.lproj is the template's, not a language.
    (["{lang}.lproj/Localizable.strings"], "zh-Hans.lproj/Localizable.strings", "zh-Hans"),
    (["{lang}.lproj/Localizable.strings"], "Base.lproj/Localizable.strings", None),
    # A POSIX locale directory with a codeset.
    (["{lang}/LC_MESSAGES/*.po"], "de_DE.UTF-8/LC_MESSAGES/app.po", "de_DE.UTF-8"),
    (["{lang}/LC_MESSAGES/*.po"], "sr_RS.UTF-8@latin/LC_MESSAGES/app.po", "sr_RS.UTF-8@latin"),
    (["*/{lang}.js"], "dist/app.min.js", None),
    (["po/{lang}.po"], "po/af.mo", None),
    (["po/{lang}.po"], "src/po/af.po", None),
    (["po/*.po"], "po/af.po", "fallback"),
    (None, "af/x.po", "fallback"),
    (None, "af/x.pot", None),
])
def test_select(masks, path, lang):
    assert Selection(masks).select(path, "fallback") == lang


def test_exclude():
    selection = Selection(["i18n/{lang}.json"], ["**/qqq.json", "i18n/en.json"])
    assert selection.select("i18n/af.json", None) == "af"
    assert selection.select("i18n/qqq.json", None) is None
    assert selection.select("i18n/en.json", None) is None


def test_gives_language():
    assert Selection(["po/{lang}.po", "{lang}/*.po"]).gives_language
    assert not Selection(["po/{lang}.po", "po/*.po"]).gives_language
    assert not Selection().gives_language


@pytest.mark.parametrize("mask", ["", "/po/{lang}.po", "../{lang}.po", "a/../{lang}.po",
                                  "{lang}/{lang}.po"])
def test_bad_mask(mask):
    with pytest.raises(ValueError, match="bad filemask"):
        compile_mask(mask)


@pytest.mark.parametrize("mask, template, path, expected", [
    ("res/values-{lang}/strings.xml", "res/values/strings.xml",
     "res/values-af/strings.xml", "res/values/strings.xml"),
    ("{lang}/*.php", "en/*.php", "af/moodle.php", "en/moodle.php"),
    ("{lang}/*.ini", "en-GB/*.ini", "af-ZA/com_content.ini", "en-GB/com_content.ini"),
    ("**/i18n/{lang}.json", "**/i18n/en.json", "extensions/Foo/i18n/af.json",
     "extensions/Foo/i18n/en.json"),
    ("**/i18n/{lang}.json", "**/i18n/en.json", "i18n/af.json", "i18n/en.json"),
])
def test_fill(mask, template, path, expected):
    assert fill(template, compile_mask(mask).match(path)) == expected
    assert wildcards(template) == wildcards(mask)


@pytest.mark.parametrize("mask, path, lang", [
    ("**/*_{lang}.properties", "src/foo_bar_de.properties", "de"),
    ("**/*_{lang}.properties", "src/my_app_fr.properties", "fr"),
    ("**/*_{lang}.properties", "src/web_ui_de.properties", "de"),
    ("**/*_{lang}.properties", "src/app_pt_br.properties", "pt_br"),
    ("**/*_{lang}.properties", "src/messages_pt_BR.properties", "pt_BR"),
    ("**/*_{lang}.properties", "src/app_zh_Hans.properties", "zh_Hans"),
    ("**/*_{lang}.properties", "src/app_ast.properties", "ast"),
    ("*-{lang}.json", "core-ast-es.json", "es"),
    ("*-{lang}.json", "core-zh-hant.json", "zh-hant"),
    ("*-{lang}.json", "app-ca-valencia.json", "ca-valencia"),
    # The shortest reading is a language of two letters or more.
    ("*{lang}.po", "xx_de.po", "de"),
    # A three-letter code counts by the language it names: German in Switzerland.
    ("**/*_{lang}.properties", "src/app_deu_ch.properties", "deu_ch")])
def test_lang_after_wildcard(mask, path, lang):
    """A * before {lang} can end in more than one place: the likelier
    language is read."""
    assert Selection([mask]).select(path, None) == lang


def test_template_from_chosen_reading():
    """The English template comes from the reading the language was read
    from: foo_bar_de's is foo_bar, not foo."""
    block = Block("x", "X", "git", url="https://git.example/x", format="properties",
                  files=["src/*_{lang}.properties"], template="src/*.properties")
    assert block.template_for("src/foo_bar_de.properties") == "src/foo_bar.properties"
    assert block.template_for("src/app_pt_br.properties") == "src/app.properties"
