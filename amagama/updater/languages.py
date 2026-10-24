"""Turn the language codes sources use into canonical POSIX locale codes.

Sources write the same language many ways: BCP 47 (pt-BR, sr-Latn,
zh-Hans), Android resource qualifiers (pt-rBR, b+sr+Latn), POSIX (pt_BR,
sr@latin), with a region the source always adds (af-ZA). This gives one
form - language, optional _REGION, optional @variant - as GNOME, KDE and the
Translation Project use.
"""

import re

# Codes ISO 639 replaced.
RENAMED = {"iw": "he", "in": "id", "ji": "yi", "jw": "jv", "mo": "ro"}

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
        lang = RENAMED.get(match["lang"].lower(), match["lang"].lower())
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
    lang = RENAMED.get(lang, lang)
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
