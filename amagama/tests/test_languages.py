#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import pytest

from amagama.updater.languages import is_android_locale, map_language, normalise_language


@pytest.mark.parametrize("code, expected", [
    ("af", "af"),
    ("af-ZA", "af_ZA"),
    ("pt_BR", "pt_BR"),
    ("pt-BR", "pt_BR"),
    ("pt_br", "pt_BR"),
    ("es-419", "es_419"),
    # Android resource qualifiers.
    ("pt-rBR", "pt_BR"),
    ("b+sr+Latn", "sr@latin"),
    ("b+es+419", "es_419"),
    # Scripts: a second script becomes a modifier, the usual one is dropped.
    ("sr-Latn", "sr@latin"),
    ("sr_Latn", "sr@latin"),
    ("sr-Latn-RS", "sr_RS@latin"),
    ("sr-Cyrl", "sr"),
    ("kmr-Latn", "kmr"),
    # A script that isn't the usual one stays, so two scripts don't merge.
    ("aeb-arab", "aeb@arabic"),
    ("aeb-latn", "aeb@latin"),
    ("cdo-hant", "cdo@hant"),
    ("sat-Olck", "sat@olck"),
    ("ks-Deva", "ks@devanagari"),
    ("sr@latin", "sr@latin"),
    ("sr@Latn", "sr@latin"),
    ("zh_Hans_CN", "zh_CN"),
    ("zh-Hans-SG", "zh_SG"),
    ("sr_RS@latin", "sr_RS@latin"),
    ("uz-Cyrl", "uz@cyrillic"),
    ("uz@cyrillic", "uz@cyrillic"),
    # Chinese scripts stand for their usual region.
    ("zh-Hans", "zh_CN"),
    ("zh_Hans", "zh_CN"),
    ("zh-Hant", "zh_TW"),
    ("zh-Hant-HK", "zh_HK"),
    ("zh_CN", "zh_CN"),
    # Variants.
    ("ca@valencia", "ca@valencia"),
    ("ca-valencia", "ca@valencia"),
    ("ca-ES-valencia", "ca_ES@valencia"),
    ("be@tarask", "be@tarask"),
    ("ja-JP-mac", "ja_JP"),
    # Replaced codes.
    ("iw", "he"),
    ("in", "id"),
    ("iw_IL", "he_IL"),
    ("ast", "ast"),
    ("fil", "fil"),
    # Not languages.
    ("night", None),
    ("x!", None),
    ("pt-!!", None),
    ("", None),
    ("po", "po"),
    # ISO 639's two-letter code where there is one.
    ("arg", "an"),
    ("bel", "be"),
    ("deu", "de"),
    ("ger", "de"),
    ("afr_ZA", "af_ZA"),
    ("srp-Latn", "sr@latin"),
    ("sah", "sah"),
    ("fil", "fil"),
    # ISO 639's codes for no particular language.
    ("und", None),
    ("zxx", None),
    ("mul_ZA", None),
])
def test_normalise(code, expected):
    assert normalise_language(code) == expected


def test_overrides():
    overrides = {"no": "nb", "xxx": "", "pt": "pt_PT"}
    assert map_language("no", overrides) == "nb"
    assert map_language("xxx", overrides) is None
    assert map_language("pt", overrides) == "pt_PT"
    assert map_language("pt-rBR", overrides) == "pt_BR"
    assert map_language("de") == "de"


@pytest.mark.parametrize("qualifier, expected", [
    ("af", True),
    ("pt-rBR", True),
    ("b+sr+Latn", True),
    ("b+fr+Latn+CA", True),
    ("af-watch", False),
    ("fr-feminine", False),
    ("b+sr+Latn-television", False),
    ("en-rGB-land", False),
    ("en-rXC", False),
    ("ar-rXB", False),
    ("night", False),
])
def test_android_locale(qualifier, expected):
    assert is_android_locale(qualifier) == expected
