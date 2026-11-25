#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.

import csv
import gzip
import importlib.util
import json
import pathlib

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "devsupport" / "metrics.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="no devsupport/ beside the package")

LEFT_OUT = {"targets": 5, "checks": {"printf": 3, "braces": 2, "script": 0}}
REBUILD = {"started": "2026-11-23T20:01:39Z", "imported": "2026-11-24T05:23:45Z",
           "swapped": "2026-11-24T11:16:07Z", "files": 100, "units": 900, "skipped": 300,
           "unreadable": 2}


@pytest.fixture
def metrics():
    spec = importlib.util.spec_from_file_location("metrics", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stats(built="2026-11-24T05:23:45.016916Z", days=(("2026-11-23", 10),), rebuild=None):
    en = {"built": built, "sources": 30, "targets": 70, "words": 40,
          "languages": [{"code": "af"}, {"code": "zu"}], "blocks": [{}, {}, {}],
          "checks": {"leftOut": LEFT_OUT},
          "history": [{"built": "2026-11-01T14:25:30.571869Z", "sources": 20, "targets": 36,
                       "words": 19, "languages": 1},
                      {"built": built, "sources": 30, "targets": 70, "words": 40,
                       "languages": 2}]}
    if rebuild:
        en["rebuild"] = rebuild
    return {"version": 1, "sourceLanguages": {"en": en},
            "use": {"days": [{"date": date, "ordinary": n, "prefetch": 0, "bulk": 1,
                              "limited": 0} for date, n in days]}}


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_first_run(metrics, tmp_path):
    history = tmp_path / "history.csv"
    history.write_text(
        "source,built,started,files,units,skipped,swapped\n"
        "en,2026-10-27T06:28:22Z,2026-10-27T02:31:00Z,78640,11057216,5376065,"
        "2026-10-27T06:31:17Z\n"
        "en,2026-11-24T05:23:45Z,2026-11-23T20:01:39Z,592825,101911253,39187012,\n",
        encoding="utf-8")
    out = tmp_path / "metrics"
    out.mkdir()
    use = tmp_path / "use-history.csv"
    use.write_text("date,ordinary,prefetch,bulk,limited\n2026-10-20,400,0,2,0\n"
                   "2026-11-23,7,0,0,0\n", encoding="utf-8")
    metrics.update(stats(), str(out), [str(history), str(use)])
    rows = read(out / "rebuilds.csv")
    assert [row["built"] for row in rows] == [
        "2026-10-27T06:28:22Z", "2026-11-01T14:25:30Z", "2026-11-24T05:23:45Z"]
    # Only the journal knew the October rebuild.
    assert (rows[0]["files"], rows[0]["targets"]) == ("78640", "")
    # The journal's figures and the statistics' fill one row.
    last = rows[2]
    assert (last["started"], last["files"], last["targets"], last["languages"],
            last["blocks"]) == ("2026-11-23T20:01:39Z", "592825", "70", "2", "3")
    assert (last["left_out"], last["left_out_printf"], last["left_out_script"]) == \
        ("5", "3", "0")
    assert list(rows[0])[:len(metrics.COLUMNS)] == metrics.COLUMNS
    # Days from before the statistics, and each day's highest counts.
    assert read(out / "use.csv") == [
        {"date": "2026-10-20", "ordinary": "400", "prefetch": "0", "bulk": "2", "limited": "0"},
        {"date": "2026-11-23", "ordinary": "10", "prefetch": "0", "bulk": "1", "limited": "0"}]
    [saved] = (out / "builds").iterdir()
    assert saved.name == "en-2026-11-24T05-23-45Z.json.gz"
    assert json.loads(gzip.decompress(saved.read_bytes()))["targets"] == 70
    assert (out / "README.md").exists()


def test_later_runs_only_add(metrics, tmp_path):
    metrics.update(stats(days=(("2026-11-23", 10), ("2026-11-24", 3))), str(tmp_path))
    first_build = (tmp_path / "builds" / "en-2026-11-24T05-23-45Z.json.gz").read_bytes()
    # The next build has the rebuild's record; the 23rd has fallen out of the
    # statistics' days, and the 24th has been counted again with more logs.
    later = stats(built="2026-11-29T09:00:00.5Z", days=(("2026-11-24", 8), ("2026-11-29", 4)),
                  rebuild=REBUILD)
    metrics.update(later, str(tmp_path))
    rows = read(tmp_path / "rebuilds.csv")
    assert [row["built"] for row in rows] == [
        "2026-11-01T14:25:30Z", "2026-11-24T05:23:45Z", "2026-11-29T09:00:00Z"]
    assert (rows[2]["files"], rows[2]["unreadable"]) == ("100", "2")
    assert [(day["date"], day["ordinary"]) for day in read(tmp_path / "use.csv")] == [
        ("2026-11-23", "10"), ("2026-11-24", "8"), ("2026-11-29", "4")]
    assert sorted(path.name for path in (tmp_path / "builds").iterdir()) == [
        "en-2026-11-24T05-23-45Z.json.gz", "en-2026-11-29T09-00-00Z.json.gz"]
    assert (tmp_path / "builds" / "en-2026-11-24T05-23-45Z.json.gz").read_bytes() == first_build


def test_same_stats_same_files(metrics, tmp_path):
    metrics.update(stats(rebuild=REBUILD), str(tmp_path))
    before = {path.name: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    metrics.update(stats(rebuild=REBUILD), str(tmp_path))
    assert {path.name: path.read_bytes() for path in tmp_path.rglob("*")
            if path.is_file()} == before


def test_readme_kept_current(metrics, tmp_path):
    """An older README on the branch is replaced with the current one."""
    (tmp_path / "README.md").write_text("old columns\n", encoding="utf-8")
    metrics.update(stats(), str(tmp_path))
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == metrics.README
