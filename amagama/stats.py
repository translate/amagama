#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The statistics page's figures, kept in a JSON file (STATS_FILE).

Rebuilds and update_stats compute the translation memory's figures from the
live schemas; update_stats --logs counts lookups from the access logs. The web
app only reads the file.
"""

import contextlib
import datetime
import fcntl
import functools
import json
import os
import tempfile

from psycopg2 import sql

from amagama import logsummary

VERSION = 1
# Rebuilds kept in each source language's history, and days of use.
HISTORY = 52
USE_DAYS = 366

# Words: the source text split on white space.
WORDS = r"COALESCE(array_length(regexp_split_to_array(btrim(text), '\s+'), 1), 0)"


def path(config, cache_dir=None):
    """STATS_FILE, else stats.json in cache_dir or UPDATER_CACHE_DIR."""
    return os.path.expanduser(config.get("STATS_FILE") or os.path.join(
        cache_dir or config.get("UPDATER_CACHE_DIR") or "~/.cache/amagama-updater",
        "stats.json"))


def load(filename):
    """The figures in filename, or None if there are none yet."""
    try:
        with open(filename, encoding="utf-8") as f:
            figures = json.load(f)
    except (OSError, ValueError):
        return None
    return figures if isinstance(figures, dict) and figures.get("version") == VERSION else None


@functools.lru_cache(maxsize=4)
def _read(filename, mtime, size):
    return load(filename)


def cached(filename):
    """load(filename), read again only when the file changes."""
    try:
        stat = os.stat(filename)
    except OSError:
        return None
    return _read(filename, stat.st_mtime_ns, stat.st_size)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc(stamp):
    return stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") \
        if stamp else None


@contextlib.contextmanager
def _updating(filename):
    """Yield the figures in filename (new ones if none) to change, then write
    them, readable by all, in place of the file. One change at a time."""
    directory = os.path.dirname(os.path.abspath(filename))
    os.makedirs(directory, exist_ok=True)
    with open(filename + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        figures = load(filename) or {"version": VERSION, "sourceLanguages": {}, "use": None}
        yield figures
        fd, new = tempfile.mkstemp(dir=directory, prefix=".stats-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(figures, f, ensure_ascii=False, sort_keys=True)
            os.chmod(new, 0o644)
            os.replace(new, filename)
        except BaseException:
            os.remove(new)
            raise


def _exists(cursor, schema, table, column=None):
    if column is None:
        cursor.execute("SELECT to_regclass(%s) IS NOT NULL",
                       (sql.Identifier(schema, table).as_string(cursor),))
    else:
        cursor.execute("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s AND column_name = %s)""",
                       (schema, table, column))
    return cursor.fetchone()[0]


def _failures(rows, names):
    """{key: {check: failures}} from (key, mask, targets) rows; names: {bit: check}."""
    found = {}
    for key, mask, n in rows:
        counts = found.setdefault(key, {})
        for bit, name in names.items():
            if mask & (1 << bit):
                counts[name] = counts.get(name, 0) + n
    return found


def compute(tmdb, slang):
    """The figures of the slang schema: volume, languages, blocks and checks."""
    schema = tmdb.schema(slang)
    cursor = tmdb.get_cursor()
    s = sql.Identifier(schema)
    cursor.execute(sql.SQL("SELECT COUNT(*), COALESCE(SUM(%s), 0) FROM {}.sources" % WORDS)
                   .format(s))
    sources, words = cursor.fetchone()
    cursor.execute(sql.SQL("""WITH w AS (SELECT sid, %s AS words FROM {0}.sources),
            per AS (SELECT lang, sid, COUNT(*) AS n FROM {0}.targets GROUP BY lang, sid)
        SELECT per.lang, SUM(per.n), COUNT(*), COALESCE(SUM(w.words), 0)
        FROM per JOIN w ON w.sid = per.sid GROUP BY per.lang ORDER BY per.lang""" % WORDS)
                   .format(s))
    languages = [{"code": lang, "targets": int(targets), "sources": int(translated),
                  "words": int(lang_words),
                  "coverage": round(translated / sources, 4) if sources else 0}
                 for lang, targets, translated, lang_words in cursor.fetchall()]

    flagged = {}
    left_out_names = []
    if _exists(cursor, schema, "check_names") and _exists(cursor, schema, "targets", "checks"):
        cursor.execute(sql.SQL("SELECT bit, name, left_out FROM {}.check_names ORDER BY bit")
                       .format(s))
        for bit, name, left_out in cursor.fetchall():
            if left_out:
                left_out_names.append(name)
            else:
                flagged[bit] = name
    by_language = {}
    if flagged:
        cursor.execute(sql.SQL("""SELECT lang, checks, COUNT(*) FROM {}.targets
            WHERE checks IS NOT NULL GROUP BY 1, 2""").format(s))
        by_language = _failures(cursor.fetchall(), flagged)
    for language in languages:
        language["checks"] = by_language.get(language["code"], {})

    blocks = []
    if _exists(cursor, schema, "imports"):
        titled = _exists(cursor, schema, "blocks")
        cursor.execute(sql.SQL("""SELECT i.block, {1}, COUNT(DISTINCT i.lang), MAX(i.imported)
            FROM {0}.imports i {2} WHERE i.block IS NOT NULL GROUP BY 1, 2, 3 ORDER BY 1""").format(
            s, sql.SQL("b.title, b.version" if titled else "NULL, NULL"),
            sql.SQL("LEFT JOIN {}.blocks b ON b.name = i.block").format(s) if titled
            else sql.SQL("")))
        blocks = [{"name": name, "title": title or name, "version": version,
                   "languages": langs, "imported": _utc(imported), "targets": None, "checks": {}}
                  for name, title, version, langs, imported in cursor.fetchall()]
    if blocks and _exists(cursor, schema, "target_imports"):
        cursor.execute(sql.SQL("""SELECT block, COUNT(*) FROM (
                SELECT DISTINCT i.block, l.tid FROM {0}.target_imports l
                JOIN {0}.imports i ON i.iid = l.iid WHERE i.block IS NOT NULL) d
            GROUP BY block""").format(s))
        targets = dict(cursor.fetchall())
        by_block = {}
        if flagged:
            cursor.execute(sql.SQL("""SELECT i.block, t.checks, COUNT(DISTINCT t.tid)
                FROM {0}.targets t JOIN {0}.target_imports l ON l.tid = t.tid
                JOIN {0}.imports i ON i.iid = l.iid
                WHERE t.checks IS NOT NULL AND i.block IS NOT NULL GROUP BY 1, 2""").format(s))
            by_block = _failures(cursor.fetchall(), flagged)
        for block in blocks:
            block["targets"] = int(targets.get(block["name"], 0))
            block["checks"] = by_block.get(block["name"], {})

    # The rebuild's mark, in full: two rebuilds can fall in one second.
    built = None
    if blocks:
        cursor.execute(sql.SQL("SELECT MAX(imported) FROM {}.imports WHERE block IS NOT NULL")
                       .format(s))
        built = cursor.fetchone()[0].astimezone(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%fZ")
    totals = {name: sum(lang.get(name, 0) for lang in by_language.values())
              for name in flagged.values()}
    cursor.connection.commit()
    return {
        "built": built,
        "sources": int(sources),
        "words": int(words),
        "targets": sum(language["targets"] for language in languages),
        "languages": languages,
        "blocks": blocks,
        "checks": {"flagged": sorted(flagged.values()), "failures": totals,
                   "leftOutChecks": left_out_names, "leftOut": None},
    }


def left_out(report, names):
    """What a rebuild's unit_checks.reject() report left out: the targets, and
    the failures of each of names (a target can fail several)."""
    return {"targets": report.left_out, "checks": {name: report.failures(name) for name in names}}


def _history(previous, figures):
    """previous's history with figures' rebuild added or replaced, last HISTORY kept."""
    entry = {key: figures[key] for key in ("built", "sources", "targets", "words")}
    entry["languages"] = len(figures["languages"])
    history = [old for old in (previous or {}).get("history", [])
               if old.get("built") != entry["built"]]
    history.append(entry)
    history.sort(key=lambda old: old.get("built") or "")
    return history[-HISTORY:]


def update_tm(filename, tmdb, slangs=None, rejected=None):
    """Compute the figures of slangs (None: every live source language) into
    filename. rejected is {slang: left_out()} of a rebuild just done; otherwise
    a language keeps what the rebuild of its data left out, if known."""
    rejected = rejected or {}
    everything = slangs is None
    slangs = sorted(tmdb.source_langs if slangs is None else slangs)
    computed = {slang: compute(tmdb, slang) for slang in slangs}
    with _updating(filename) as figures:
        languages = figures.setdefault("sourceLanguages", {})
        for slang, new in computed.items():
            previous = languages.get(slang)
            if slang in rejected:
                new["checks"]["leftOut"] = rejected[slang]
            elif previous and previous.get("built") == new["built"]:
                new["checks"]["leftOut"] = previous.get("checks", {}).get("leftOut")
            new["history"] = _history(previous, new)
            new["computed"] = _now()
            languages[slang] = new
        if everything:
            for gone in set(languages) - set(tmdb.source_langs):
                del languages[gone]
        figures["computed"] = _now()
    return computed


def update_use(filename, logs):
    """Add the lookups per day in logs to filename, keeping each day's highest
    counts (a day can be split between logs) for the last USE_DAYS days."""
    counted = logsummary.lookups_per_day(logs)
    with _updating(filename) as figures:
        days = {day["date"]: day for day in (figures.get("use") or {}).get("days", [])}
        for date, counts in counted.items():
            old = days.get(date, {})
            days[date] = dict({key: max(n, old.get(key, 0)) for key, n in counts.items()},
                              date=date)
        figures["use"] = {"computed": _now(),
                          "days": [days[date] for date in sorted(days)[-USE_DAYS:]]}
    return counted
