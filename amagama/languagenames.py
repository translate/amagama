#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Names of the languages amaGama stores, from ISO 639, 3166 and 15924 as
pycountry has them, translated by iso-codes.

Stored codes are lower case with underscores: af, pt_br, sr_latin, en_shaw.
"""

import functools
import gettext
import re

import pycountry

from amagama.updater.languages import SCRIPT_NAMES

PATTERN = ("language with detail", "{language} ({detail})")
PATTERN_NOTE = ("A language and its country or script, such as Portuguese (Brazil) or "
                "Serbian (Latin). Keep {language} and {detail}.")

_REGION = re.compile(r"[a-z]{2}|\d{3}")


@functools.cache
def _codes():
    """({language code: name}, {country code: name}, {script code or name: (code, name)})."""
    languages = {}
    for language in pycountry.languages:
        languages[language.alpha_3] = language.name
        if hasattr(language, "alpha_2"):
            languages[language.alpha_2] = language.name
    countries = {country.alpha_2.lower(): country.name for country in pycountry.countries}
    scripts = {script.alpha_4.lower(): (script.alpha_4, script.name)
               for script in pycountry.scripts}
    for code, name in SCRIPT_NAMES.items():
        scripts[name] = scripts[code]
    return languages, countries, scripts


@functools.cache
def _translation(domain, lang):
    return gettext.translation(domain, pycountry.LOCALES_DIR, languages=[lang], fallback=True)


def describe(code, lang, pattern=PATTERN[1]):
    """{"tag": BCP 47 tag, "name": name in lang} of a stored code; no name
    if ISO 639 doesn't have the language.

    pattern words a language with its country, script or variant.
    """
    languages, countries, scripts = _codes()
    base, *parts = code.split("_")
    subtags = [base]
    details = []
    for part in parts:
        if _REGION.fullmatch(part):
            subtags.append(part.upper())
            name = countries.get(part)
            details.append(_translation("iso3166-1", lang).gettext(name) if name else part.upper())
        elif part in scripts:
            script, name = scripts[part]
            subtags.append(script)
            details.append(_translation("iso15924", lang).gettext(name))
        else:
            subtags.append(part)
            details.append(part)
    described = {"tag": "-".join(subtags)}
    if base in languages:
        name = _translation("iso639-3", lang).gettext(languages[base])
        for detail in details:
            name = pattern.replace("{language}", name).replace("{detail}", detail)
        described["name"] = name
    return described


@functools.lru_cache(maxsize=64)
def describe_all(codes, lang, pattern=PATTERN[1]):
    """{code: describe(code, lang, pattern)} for a tuple of codes."""
    return {code: describe(code, lang, pattern) for code in codes}
