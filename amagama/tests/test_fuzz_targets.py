#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The fuzz targets in fuzz/, run over their seed corpora without Atheris."""

import contextlib
import importlib.util
import os
import pathlib
import sys
import types

import pytest

FUZZ = pathlib.Path(__file__).resolve().parents[2] / "fuzz"
TARGETS = sorted(FUZZ.glob("fuzz_*.py"))


@pytest.fixture
def load(monkeypatch):
    atheris = types.ModuleType("atheris")
    atheris.instrument_imports = contextlib.nullcontext
    atheris.instrument_func = lambda function: function
    monkeypatch.setitem(sys.modules, "atheris", atheris)
    # The targets import fuzzing.py from beside them.
    monkeypatch.syspath_prepend(str(FUZZ))

    def load(path):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        target = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(target)
        return target
    return load


def seeds(name):
    return sorted((FUZZ / "corpus" / name).iterdir())


@pytest.mark.parametrize("path", TARGETS, ids=lambda path: path.stem)
def test_seeds(load, path):
    target = load(path)
    assert seeds(path.stem)
    for seed in seeds(path.stem):
        target.TestOneInput(seed.read_bytes())


@pytest.mark.skipif(not TARGETS, reason="no fuzz/ beside the package")
def test_reader_seeds(load):
    """Every reader is in one family, the family's target has a seed for it,
    and each seed gives units an import would keep."""
    readers = load(FUZZ / "readers.py")
    families = readers.FAMILIES.values()
    assert sorted(name for names in families for name in names) == readers.NAMES
    for family, names in readers.FAMILIES.items():
        paths = seeds("fuzz_readers_" + family)
        assert sorted(path.name for path in paths) == sorted(names), family
        for path in paths:
            name, _, data = path.read_bytes().partition(b"\n")
            units = [unit for store in readers.read(name.decode(), data) for unit in store.units
                     if readers.tmdb.TMDB._usable_unit(readers.IMPORTER, unit)]
            assert units, path.name


@pytest.mark.skipif(not TARGETS, reason="no fuzz/ beside the package")
def test_request_seeds(load):
    """fuzz_requests has seeds for each of the targets it picks from."""
    target = load(FUZZ / "fuzz_requests.py")
    picked = {path.read_bytes()[0] % len(target.TARGETS) for path in seeds("fuzz_requests")}
    assert picked == set(range(len(target.TARGETS)))


@pytest.mark.skipif(not TARGETS, reason="no fuzz/ beside the package")
def test_within(load):
    """-max_total_time loses the time spent starting, and nothing else changes."""
    fuzzing = load(FUZZ / "fuzzing.py")
    argv = ["/out/fuzz_readers", "-rss_limit_mb=2560", "-max_total_time=1200", "corpus"]
    assert fuzzing.within(argv, 41.2) == [
        "/out/fuzz_readers", "-rss_limit_mb=2560", "-max_total_time=1158", "corpus"]
    assert fuzzing.within(["-max_total_time=30"], 100) == ["-max_total_time=1"]
    assert fuzzing.within(["-runs=5", "corpus"], 100) == ["-runs=5", "corpus"]


@pytest.mark.skipif(not TARGETS, reason="no fuzz/ beside the package")
def test_running_for(load):
    fuzzing = load(FUZZ / "fuzzing.py")
    spent = fuzzing.running_for()
    if os.path.exists("/proc/self/stat") and os.path.exists("/proc/uptime"):
        with open("/proc/uptime") as f:
            assert 0 < spent < float(f.read().split()[0])
    else:
        assert spent == 0.0


@pytest.mark.skipif(not TARGETS, reason="no fuzz/ beside the package")
def test_long_text_with_escaped_emoji(load):
    """The XML readers' target on a long Compose string with an emoji as a
    surrogate-pair escape: it used to raise UnicodeEncodeError (#3823)."""
    target = load(FUZZ / "fuzz_readers_xml.py")
    readers = load(FUZZ / "readers.py")
    text = "Open the file \\uD83D\\uDE00 " * 60
    data = ('<resources><string name="open">%s</string></resources>' % text).encode("utf-8")
    units = [unit for store in readers.read("compose", data) for unit in store.units
             if readers.tmdb.TMDB._usable_unit(readers.IMPORTER, unit)]
    assert units
    target.TestOneInput(b"compose\n" + data)


@pytest.mark.skipif(not TARGETS, reason="no fuzz/ beside the package")
def test_nul_in_json(load):
    """The data readers' target on a JSON string with \\u0000: the database
    can't hold it, so it must not reach add_list() (#3825)."""
    target = load(FUZZ / "fuzz_readers_data.py")
    readers = load(FUZZ / "readers.py")
    data = b'{"open": "Open\\u0000 it", "save": "Save"}'
    assert any("\x00" in str(unit.source) for store in readers.read("json", data)
               for unit in store.units)
    target.TestOneInput(b"json\n" + data)
