#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Block titles in the language of a request, from amagama/po/sources/."""

import functools
import os

from translate.storage import po

CATALOG_DIR = os.path.join(os.path.dirname(__file__), "po", "sources")
TEMPLATE = os.path.join(CATALOG_DIR, "sources.pot")

PATTERN = ("title with version", "{title} {version}")
DIRECTION = ("direction", "ltr")
TITLE_NOTE = ("The name of a project amaGama has translations from. Translate or "
              "transliterate it only where that's usual.")
PATTERN_NOTE = ("A project's name and release, such as GNOME 51. Keep {title} and "
                "{version}; reorder them or add words as your language needs.")
DIRECTION_NOTE = 'Translate as "rtl" if your language is written right to left.'


def read_catalogs(directory):
    """{language: {(context, msgid): msgstr}} of every translated catalog in directory."""
    found = {}
    for filename in sorted(os.listdir(directory)):
        lang, ext = os.path.splitext(filename)
        if ext != ".po":
            continue
        store = po.pofile.parsefile(os.path.join(directory, filename))
        found[lang] = {(unit.getcontext() or None, unit.source): unit.target
                       for unit in store.units
                       if not unit.isheader() and unit.istranslated()}
    return found


@functools.cache
def catalogs():
    """The block-title catalogs, as read_catalogs gives them."""
    return read_catalogs(CATALOG_DIR)


def negotiate(lang, accept_languages, translated=None):
    """The catalog for ?lang=, else the best for Accept-Language, else en.

    translated is the catalogs to choose from; the block titles' by default.
    """
    available = sorted(catalogs() if translated is None else translated) + ["en"]
    if lang:
        lang = lang.replace("-", "_")
        for code in available:
            if code.lower() == lang.lower():
                return code
    tags = {code.replace("_", "-"): code for code in available}
    best = accept_languages.best_match(sorted(tags))
    return tags[best] if best else "en"


def localise(sources, lang):
    """(direction, sources with title and displayTitle in lang)."""
    catalog = catalogs().get(lang, {})
    direction = catalog.get(DIRECTION, "ltr")
    pattern = catalog.get(PATTERN, PATTERN[1])
    if "{title}" not in pattern or "{version}" not in pattern:
        pattern = PATTERN[1]
    localised = []
    for source in sources:
        title = catalog.get((None, source["title"]), source["title"])
        display = pattern.replace("{title}", title).replace(
            "{version}", source["version"]) if source["version"] else title
        localised.append(dict(source, title=title, displayTitle=display))
    return ("rtl" if direction == "rtl" else "ltr"), localised


def template(blocks):
    """The sources.pot text: the title pattern and direction, then each block title once."""
    messages = [(PATTERN, PATTERN_NOTE), (DIRECTION, DIRECTION_NOTE)]
    messages += [((None, title), TITLE_NOTE)
                 for title in dict.fromkeys(block.title for block in blocks)]
    return pot("amaGama sources", messages)


def pot(project, messages):
    """The text of a template of messages, a list of ((context, msgid), note)."""
    store = po.pofile()
    store.updateheader(add=True, Project_Id_Version=project,
                       Content_Type="text/plain; charset=UTF-8",
                       Content_Transfer_Encoding="8bit")
    for (context, msgid), note in messages:
        unit = store.addsourceunit(msgid)
        if context:
            unit.setcontext(context)
        if note:
            unit.addnote(note, "developer")
    # Without a date or generator, the template changes only with its messages.
    return "".join(line for line in bytes(store).decode("utf-8").splitlines(True)
                   if not line.startswith(('"POT-Creation-Date:', '"X-Generator:')))
