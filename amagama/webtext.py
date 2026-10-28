#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The lookup page's text in the language of a request, from amagama/po/web/.

Templates mark text with _() for HTML, whose values may be markup, and with
gettext() for plain text the page's script shows. Placeholders are {name}.
"""

import functools
import os
import re

import jinja2.ext
from markupsafe import escape

from amagama import languagenames, titles

CATALOG_DIR = os.path.join(os.path.dirname(__file__), "po", "web")
TEMPLATE = os.path.join(CATALOG_DIR, "web.pot")
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

KEYWORDS = ("_", "gettext")
COMMENT_TAG = "Translators:"

PLACEHOLDER = re.compile(r"\{\w+\}")

# Messages the templates don't have: ((context, msgid), note).
MESSAGES = [
    (titles.DIRECTION, titles.DIRECTION_NOTE),
    (languagenames.PATTERN, languagenames.PATTERN_NOTE),
]


@functools.cache
def catalogs():
    """The page's catalogs, as titles.read_catalogs gives them."""
    return titles.read_catalogs(CATALOG_DIR)


def negotiate(lang, accept_languages):
    """The page's language for ?lang= and Accept-Language, as titles.negotiate."""
    return titles.negotiate(lang, accept_languages, catalogs())


class Translator:
    """The page's text in one language."""

    def __init__(self, lang):
        self.lang = lang
        self.catalog = catalogs().get(lang, {})
        context, msgid = titles.DIRECTION
        self.dir = "rtl" if self.gettext(msgid, context) == "rtl" else "ltr"
        # BCP 47, for the lang attribute.
        self.tag = lang.replace("_", "-")

    def gettext(self, msgid, context=None):
        """msgid translated, unless the translation's placeholders differ."""
        text = self.catalog.get((context, msgid), msgid)
        if sorted(PLACEHOLDER.findall(text)) != sorted(PLACEHOLDER.findall(msgid)):
            return msgid
        return text

    def __call__(self, msgid, **values):
        """msgid translated as HTML, with {name} replaced by values[name]."""
        text = escape(self.gettext(msgid))
        for name, value in values.items():
            # Markup.replace escapes value unless it's Markup.
            text = text.replace("{%s}" % name, value)
        return text


def extract(directory=TEMPLATES_DIR):
    """((None, msgid), note) of each message in the templates, in order."""
    found = {}
    for filename in sorted(os.listdir(directory)):
        if not filename.endswith(".html"):
            continue
        with open(os.path.join(directory, filename), "rb") as f:
            # babel_extract gives nothing for a template with a syntax error.
            jinja2.Environment().parse(f.read().decode("utf-8"))
            f.seek(0)
            for _line, _function, message, comments in jinja2.ext.babel_extract(
                    f, KEYWORDS, (COMMENT_TAG,), {}):
                if isinstance(message, tuple):
                    message = message[0]
                found.setdefault((None, message), " ".join(comments) or None)
    return list(found.items())


def template():
    """The web.pot text: the templates' messages, then MESSAGES."""
    return titles.pot("amaGama web page", extract() + MESSAGES)
