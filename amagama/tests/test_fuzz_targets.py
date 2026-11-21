#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The fuzz targets in fuzz/, run over their seed corpora without Atheris."""

import contextlib
import importlib.util
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
    monkeypatch.setitem(sys.modules, "atheris", atheris)

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
    """Every reader has a seed, and each gives units an import would keep."""
    target = load(FUZZ / "fuzz_readers.py")
    paths = seeds("fuzz_readers")
    assert sorted(path.name for path in paths) == target.NAMES
    for path in paths:
        name, _, data = path.read_bytes().partition(b"\n")
        units = [unit for store in target.read(name.decode(), data) for unit in store.units
                 if target.tmdb.TMDB._usable_unit(target.IMPORTER, unit)]
        assert units, path.name
