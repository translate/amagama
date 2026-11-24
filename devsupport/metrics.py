#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""Keep the statistics' figures over time, on the metrics branch.

    python devsupport/metrics.py STATS DIRECTORY [HISTORY...]

STATS is /api/v1/stats/ saved to a file, DIRECTORY a checkout of the
metrics branch, and each HISTORY a CSV file of what the statistics didn't
keep: rows of rebuilds (as rebuilds.csv) or of lookups per day (as use.csv,
its first column date). In DIRECTORY:

- rebuilds.csv: a row per source language and rebuild;
- use.csv: the lookups per day;
- builds/: each rebuild's figures as they were first seen, gzipped.

A row's figures are only filled in, never emptied, so what HISTORY or an
earlier snapshot recorded stays when the statistics no longer have it.
Only the standard library is used.
"""

import csv
import gzip
import json
import os
import sys

COLUMNS = ["source", "built", "started", "imported", "swapped", "files", "units", "skipped",
           "unreadable", "sources", "targets", "words", "languages", "blocks", "left_out"]
USE_COLUMNS = ["date", "ordinary", "prefetch", "bulk", "limited"]

README = """\
# amaGama metrics

Snapshots of https://amagama-live.translatehouse.org/api/v1/stats/, kept by
the Metrics workflow (`devsupport/metrics.py` on main); see the API docs for
what each figure means.

- `rebuilds.csv`: a row per source language and rebuild. `built` is when its
  imports finished (to the second); `started`, `imported` and `swapped` when
  it started, finished importing and swapped in; `files`, `units`, `skipped`
  and `unreadable` what it imported; `left_out` and `left_out_<check>` the
  targets its checks left out; `note` says what else to know about a row.
  Rebuilds from before the statistics kept these were filled in from the
  update journal, and the first row is the 2019 database (Legacy OSS) every
  rebuild loads first.
- `use.csv`: lookups per day, by kind.
- `builds/`: each rebuild's figures, as first seen.
"""


def built_key(built):
    """built to the second, so the journal's times match the statistics'."""
    return built[:19] + "Z" if built else None


def _row(source, entry):
    row = {"source": source, "built": built_key(entry.get("built"))}
    for column in ("sources", "targets", "words", "languages", "blocks"):
        row[column] = entry.get(column)
    row.update(entry.get("rebuild") or {})
    left_out = entry.get("leftOut") or {}
    if left_out:
        row["left_out"] = left_out.get("targets")
        for check, failures in (left_out.get("checks") or {}).items():
            row["left_out_" + check] = failures
    return row


def rows(stats):
    """The rebuild rows in STATS: each source language's history and its
    current build, which also gives the number of blocks."""
    found = []
    for source, figures in sorted((stats.get("sourceLanguages") or {}).items()):
        for entry in figures.get("history") or []:
            found.append(_row(source, entry))
        current = dict(figures, languages=len(figures.get("languages") or []),
                       blocks=len(figures.get("blocks") or []),
                       leftOut=(figures.get("checks") or {}).get("leftOut"))
        found.append(_row(source, current))
    return [row for row in found if row["built"]]


def merge(kept, new):
    """kept ({(source, built): row}) with new's rows' figures filled in."""
    for row in new:
        old = kept.setdefault((row["source"], row["built"]), {})
        old.update({column: value for column, value in row.items()
                    if value is not None and value != ""})
    return kept


def read_csv(path, key):
    try:
        with open(path, encoding="utf-8", newline="") as f:
            return {key(row): {column: value for column, value in row.items() if value != ""}
                    for row in csv.DictReader(f)}
    except FileNotFoundError:
        return {}


def write_csv(path, columns, found):
    """found's rows to path, columns first, then the others in order."""
    extra = sorted({column for row in found for column in row} - set(columns))
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, columns + extra, lineterminator="\n")
        writer.writeheader()
        writer.writerows(found)


def merge_use(kept, days):
    """kept ({date: row}) with days' counts, each kind's highest kept: the
    server counts a day again as its logs grow."""
    for day in days:
        old = kept.setdefault(day["date"], {"date": day["date"]})
        for column, value in day.items():
            if column != "date":
                old[column] = max(int(old.get(column) or 0), int(value))
    return kept


def save_builds(directory, stats):
    """Each source language's current figures, the first time its build is seen."""
    os.makedirs(os.path.join(directory, "builds"), exist_ok=True)
    saved = []
    for source, figures in sorted((stats.get("sourceLanguages") or {}).items()):
        built = built_key(figures.get("built"))
        if not built:
            continue
        path = os.path.join(directory, "builds", "%s-%s.json.gz" % (source,
                                                                    built.replace(":", "-")))
        if os.path.exists(path):
            continue
        # mtime 0: the same figures give the same file.
        with gzip.GzipFile(path, "wb", mtime=0) as f:
            f.write(json.dumps(figures, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        saved.append(path)
    return saved


def _is_use(path):
    with open(path, encoding="utf-8", newline="") as f:
        return next(csv.reader(f), [""])[0] == "date"


def update(stats, directory, histories=()):
    rebuilds_path = os.path.join(directory, "rebuilds.csv")
    use_path = os.path.join(directory, "use.csv")
    kept = read_csv(rebuilds_path, lambda row: (row["source"], row["built"]))
    days = read_csv(use_path, lambda row: row["date"])
    for history in histories:
        if _is_use(history):
            merge_use(days, read_csv(history, lambda row: row["date"]).values())
        else:
            merge(kept, read_csv(history, lambda row: (row["source"], row["built"])).values())
    merge(kept, rows(stats))
    write_csv(rebuilds_path, COLUMNS, [kept[key] for key in sorted(kept, key=lambda k: (k[1], k[0]))])
    merge_use(days, (stats.get("use") or {}).get("days") or [])
    write_csv(use_path, USE_COLUMNS, [days[date] for date in sorted(days)])
    save_builds(directory, stats)
    readme = os.path.join(directory, "README.md")
    if not os.path.exists(readme):
        with open(readme, "w", encoding="utf-8") as f:
            f.write(README)


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    with open(argv[1], encoding="utf-8") as f:
        stats = json.load(f)
    update(stats, argv[2], argv[3:])


if __name__ == "__main__":
    main(sys.argv)
