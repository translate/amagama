import pytest

from amagama.updater.filemask import Selection, compile_mask, fill, wildcards


@pytest.mark.parametrize("masks, path, lang", [
    (["po/{lang}.po"], "po/af.po", "af"),
    (["po/{lang}.po"], "po/pt_BR.po", "pt_BR"),
    (["po/{lang}.po"], "po/sr@latin.po", "sr@latin"),
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
