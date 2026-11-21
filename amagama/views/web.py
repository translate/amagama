#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Public web query for the amaGama translation memory server"""

import datetime
import functools
import hashlib
import itertools
import os

from flask import Blueprint, current_app, make_response, render_template, request

from amagama import charts, languagenames, stats, titles, unit_checks, webtext


web_ui = Blueprint('web_ui', __name__, static_folder='static')


@functools.lru_cache(maxsize=64)
def _file_version(path, mtime):
    with open(path, 'rb') as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


@web_ui.url_defaults
def _versioned_static(endpoint, values):
    """Give each static file's URL a hash of the file, so a changed file has
    a new address and browsers don't keep the old one."""
    if endpoint != 'web_ui.static' or 'filename' not in values:
        return
    path = os.path.join(current_app.static_folder, values['filename'])
    try:
        values['v'] = _file_version(path, os.stat(path).st_mtime_ns)
    except OSError:
        pass


@web_ui.route('/', methods=('GET', ))
def translate():
    """Serve the query web page, in the language of ?lang= or Accept-Language."""
    _ = webtext.Translator(webtext.negotiate(request.args.get('lang'), request.accept_languages))
    available = current_app.tmdb.available_languages
    codes = tuple(sorted(set(available['sourceLanguages']) | set(available['targetLanguages'])))
    context, pattern = languagenames.PATTERN
    languages = {
        'source': available['sourceLanguages'],
        'target': available['targetLanguages'],
        'described': languagenames.describe_all(codes, _.lang, _.gettext(pattern, context)),
    }
    response = make_response(render_template(
        "translate.html", _=_, gettext=_.gettext, year=datetime.date.today().year,
        languages=languages))
    response.vary.add('Accept-Language')
    return response


# The languages and blocks the page names for each check.
TOP = 10
KINDS = ("ordinary", "prefetch", "bulk")


def number(value):
    """value with its thousands apart, as SI writes them."""
    return "{:,}".format(value).replace(",", "\u202f")


def percent(part, whole):
    """part as a percentage of whole, to one decimal."""
    return "%.1f%%" % (100 * part / whole) if whole else "0.0%"


def _share(part, whole):
    """part's width in a bar of whole, in percent."""
    return round(100 * part / whole, 1) if whole else 0


def _grouped(blocks):
    """One row per title shown: a project's components or branches with one
    title add up, with the most languages of any and the newest import."""
    rows = {}
    for block in blocks:
        row = rows.get(block["displayTitle"])
        if row is None:
            rows[block["displayTitle"]] = dict(block, checks=dict(block["checks"]))
            continue
        if block["targets"] is not None:
            row["targets"] = (row["targets"] or 0) + block["targets"]
        row["languages"] = max(row["languages"], block["languages"])
        row["imported"] = max(row["imported"] or "", block["imported"] or "") or None
        for name, n in block["checks"].items():
            row["checks"][name] = row["checks"].get(name, 0) + n
    return list(rows.values())


def _source_language(slang, figures, _, names):
    """What the page shows of one source language's figures."""
    described = names(tuple([slang] + [lang["code"] for lang in figures["languages"]]))
    option = _.gettext("{name} — {code}")

    def language(code):
        name = described.get(code, {}).get("name")
        return option.replace("{name}", name).replace("{code}", code) if name else code

    # The figures are cached for every request: the page's copies are its own.
    history = [dict(entry) for entry in reversed(figures.get("history", []))]
    built = [(datetime.datetime.fromisoformat(entry["built"]), entry)
             for entry in reversed(history) if entry.get("built")]
    volume = [(key, charts.line([(when, entry[key]) for when, entry in built]))
              for key in ("targets", "sources", "languages")]
    most = max((entry["targets"] for entry in history), default=0)
    for entry in history:
        entry["bar"] = _share(entry["targets"], most)
    languages = sorted((dict(lang) for lang in figures["languages"]),
                       key=lambda lang: (-lang["targets"], lang["code"]))
    most = max((lang["targets"] for lang in languages), default=0)
    for lang in languages:
        lang["name"] = language(lang["code"])
        lang["tag"] = described.get(lang["code"], {}).get("tag")
        lang["bar"] = _share(lang["targets"], most)
        lang["coverageBar"] = round(100 * lang["coverage"], 1)
    direction, blocks = titles.localise(figures["blocks"], _.lang)
    blocks = _grouped(blocks)
    blocks.sort(key=lambda block: (-(block["targets"] or 0), block["displayTitle"].lower()))
    most = max((block["targets"] or 0 for block in blocks), default=0)
    for block in blocks:
        block["bar"] = _share(block["targets"] or 0, most)
    checks = []
    for name in figures["checks"]["flagged"]:
        failures = figures["checks"]["failures"].get(name, 0)
        worst = sorted(((lang["checks"].get(name, 0), lang) for lang in languages
                        if lang["checks"].get(name)), key=lambda item: (-item[0], item[1]["code"]))
        bad_blocks = sorted(((block["checks"].get(name, 0), block) for block in blocks
                             if block["checks"].get(name)),
                            key=lambda item: (-item[0], item[1]["displayTitle"].lower()))
        checks.append({
            "name": name, "failures": failures,
            "category": figures["checks"]["categories"].get(name),
            "share": percent(failures, figures["targets"]),
            "languages": [(lang["name"], n, percent(n, lang["targets"]))
                          for n, lang in worst[:TOP]],
            "blocks": [(block["displayTitle"], n, percent(n, block["targets"] or 0))
                       for n, block in bad_blocks[:TOP]],
        })
    order = {name: n for n, name in enumerate(unit_checks.CATEGORIES)}
    checks.sort(key=lambda check: (order.get(check["category"], len(order)), -check["failures"],
                                   check["name"]))
    failing = [check for check in checks if check["failures"]]
    return {
        "code": slang,
        "name": language(slang),
        "figures": figures,
        "history": history,
        "charts": [(key, chart) for key, chart in volume if chart],
        "languages": languages,
        "blocks": blocks,
        "blocksDir": direction,
        "checks": failing,
        "groups": [(key, list(group))
                   for key, group in itertools.groupby(failing, lambda check: check["category"])],
        "passed": sorted(check["name"] for check in checks if not check["failures"]),
        "leftOut": figures["checks"].get("leftOut"),
    }


def _use(use):
    """What the page shows of the lookups: a chart, and the days newest first."""
    if not use or not use.get("days"):
        return None
    days = [dict(day, total=sum(day.get(kind, 0) for kind in KINDS)) for day in use["days"]]
    most = max(day["total"] for day in days)
    for day in days:
        day["bars"] = [(kind, _share(day.get(kind, 0), most)) for kind in KINDS]
    totals = {kind: sum(day.get(kind, 0) for day in days) for kind in KINDS + ("total", "limited")}
    return {"days": list(reversed(days)), "totals": totals, "computed": use.get("computed"),
            "chart": charts.columns(days, KINDS, "limited")}


@web_ui.route('/stats/', methods=('GET', ))
def statistics():
    """Serve the statistics page, from the figures the last rebuild computed."""
    _ = webtext.Translator(webtext.negotiate(request.args.get('lang'), request.accept_languages))
    figures = stats.cached(stats.path(current_app.config))
    context, pattern = languagenames.PATTERN
    pattern = _.gettext(pattern, context)

    def names(codes):
        return languagenames.describe_all(codes, _.lang, pattern)
    page = None
    if figures is not None:
        page = {
            "computed": (figures.get("computed") or "")[:10],
            "sourceLanguages": [_source_language(slang, figures["sourceLanguages"][slang], _, names)
                                for slang in sorted(figures.get("sourceLanguages", {}))],
            "use": _use(figures.get("use")),
        }
    response = make_response(render_template(
        "stats.html", _=_, gettext=_.gettext, year=datetime.date.today().year, page=page,
        number=number, percent=percent, lang=request.args.get('lang')))
    response.vary.add('Accept-Language')
    return response
