import importlib.util
import sys

import pytest

from amagama.updater import compressed

DATA = b'msgid "Open file"\nmsgstr "Maak l\xc3\xaaer oop"\n' * 1000


def zstandard_backend(monkeypatch):
    """A copy of the module as Python < 3.14 loads it, without compression.zstd."""
    pytest.importorskip("zstandard")
    monkeypatch.setitem(sys.modules, "compression", None)
    spec = importlib.util.spec_from_file_location("compressed_fallback", compressed.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def round_trip(writer, reader, tmp_path):
    path = tmp_path / "af.po"
    path.write_bytes(DATA)
    writer.compress(str(path))
    assert not path.exists()
    packed = tmp_path / ("af.po" + compressed.SUFFIX)
    assert packed.stat().st_size < len(DATA) / 10
    reader.decompress(str(packed), str(tmp_path / "out.po"))
    assert (tmp_path / "out.po").read_bytes() == DATA


def test_round_trip(tmp_path):
    round_trip(compressed, compressed, tmp_path)


def test_zstandard_backend(tmp_path, monkeypatch):
    fallback = zstandard_backend(monkeypatch)
    round_trip(fallback, fallback, tmp_path)


@pytest.mark.parametrize("which", ["fallback writes", "fallback reads"])
def test_backends_read_each_other(tmp_path, monkeypatch, which):
    fallback = zstandard_backend(monkeypatch)
    if which == "fallback writes":
        round_trip(fallback, compressed, tmp_path)
    else:
        round_trip(compressed, fallback, tmp_path)
