#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Turn the language codes sources use into canonical POSIX locale codes.

Sources write the same language many ways: BCP 47 (pt-BR, sr-Latn,
zh-Hans), Android resource qualifiers (pt-rBR, b+sr+Latn), POSIX (pt_BR,
sr@latin), with a region the source always adds (af-ZA). This gives one
form - language, optional _REGION, optional @variant - as GNOME, KDE and the
Translation Project use, with ISO 639's shortest code for the language.
"""

import functools
import re

import pycountry

# Codes ISO 639 replaced.
RENAMED = {"iw": "he", "in": "id", "ji": "yi", "jw": "jv", "mo": "ro"}


@functools.cache
def _two_letter_codes():
    """ISO 639-2/3 codes (and 639-2's bibliographic ones) to their 639-1 code."""
    codes = {}
    for language in pycountry.languages:
        if hasattr(language, "alpha_2"):
            codes[language.alpha_3] = language.alpha_2
            if hasattr(language, "bibliographic"):
                codes[language.bibliographic] = language.alpha_2
    return codes


@functools.cache
def _special_codes():
    """ISO 639's codes for no particular language: uncoded, multiple,
    undetermined, no linguistic content."""
    return {language.alpha_3 for language in pycountry.languages
            if getattr(language, "type", None) == "S"}


def _short(lang):
    """lang's current code, two letters where ISO 639-1 has one."""
    lang = RENAMED.get(lang, lang)
    return _two_letter_codes().get(lang, lang)

# A language's usual script (CLDR's likely script) is left out: sr-Cyrl is
# sr. Any other script becomes a POSIX @modifier, so text in two scripts of
# one language stays apart: sr-Latn is sr@latin, aeb-Arab aeb@arabic.
USUAL_SCRIPTS = {
    "az": "latn", "be": "cyrl", "bs": "latn", "ckb": "arab", "ff": "latn", "ha": "latn",
    "kk": "cyrl", "kmr": "latn", "ks": "arab", "ku": "latn", "ky": "cyrl", "mn": "cyrl",
    "ms": "latn", "pa": "guru", "sd": "arab", "shi": "tfng", "sr": "cyrl",
    "tg": "cyrl", "tt": "cyrl", "ug": "arab", "uz": "latn", "yue": "hant",
}

# Scripts written by name in a modifier; others by their lower-case code.
SCRIPT_NAMES = {"latn": "latin", "cyrl": "cyrillic", "arab": "arabic", "deva": "devanagari"}

# Chinese scripts are left to the region: their usual one if there's none.
CHINESE = {"hans": "CN", "hant": "TW"}

VARIANTS = {"valencia": "@valencia", "tarask": "@tarask"}

_POSIX = re.compile(r"^(?P<lang>[a-z]{2,3})(?:_(?P<region>[A-Za-z]{2}|\d{3}))?"
                    r"(?:@(?P<modifier>[a-z]+))?$", re.IGNORECASE)


def normalise_language(code):
    """The canonical form of code, or None if it isn't a language code."""
    code = code.strip()
    match = _POSIX.match(code)
    if match:
        lang = _short(match["lang"].lower())
        if lang in _special_codes():
            return None
        region = "_" + match["region"].upper() if match["region"] else ""
        modifier = match["modifier"].lower() if match["modifier"] else ""
        # GNOME writes sr@Latn.
        modifier = "@" + SCRIPT_NAMES.get(modifier, modifier) if modifier else ""
        return lang + region + modifier
    if code.startswith("b+"):  # Android BCP 47 qualifier
        code = code[2:].replace("+", "-")
    parts = re.split(r"[-_]", code)
    lang = parts.pop(0).lower()
    if not re.fullmatch(r"[a-z]{2,3}", lang):
        return None
    lang = _short(lang)
    if lang in _special_codes():
        return None
    script = region = modifier = ""
    for part in parts:
        if re.fullmatch(r"[A-Za-z]{4}", part) and not script and not region:
            script = part.lower()
        elif re.fullmatch(r"r?[A-Za-z]{2}|\d{3}", part) and not region:
            region = part[-2:].upper() if not part.isdigit() else part
        elif part.lower() in VARIANTS:
            modifier = VARIANTS[part.lower()]
        elif re.fullmatch(r"[A-Za-z0-9]{3,8}", part):
            continue  # other variants (Mozilla's ja-JP-mac): keep the rest
        else:
            return None
    if lang == "zh" and script in CHINESE:
        region = region or CHINESE[script]
    elif script and script != USUAL_SCRIPTS.get(lang):
        modifier = "@" + SCRIPT_NAMES.get(script, script)
    return lang + ("_" + region if region else "") + modifier


def map_language(code, overrides=None):
    """A block's language code in canonical form, after its own overrides.

    An override to "" skips the language; returns None for that and for
    codes that aren't languages.
    """
    if overrides and code in overrides:
        return overrides[code] or None
    return normalise_language(code)


# Android's pseudo-locales: generated test text, not translations.
ANDROID_PSEUDO = {"en-rXA", "ar-rXB", "en-rXC"}

_ANDROID_LOCALE = re.compile(r"^(?:[a-z]{2,3}(?:-r[A-Z]{2})?|b\+[a-z]{2,3}(?:\+[A-Za-z0-9]{2,8})*)$")


def is_android_locale(qualifier):
    """Whether a values- directory's qualifier is a locale alone.

    values-af-watch and values-fr-feminine hold variants for a device or
    grammatical gender, keyed like values-af but with other English.
    """
    return bool(_ANDROID_LOCALE.match(qualifier)) and qualifier not in ANDROID_PSEUDO
