#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import hashlib
import io
import lzma
import tarfile

import pytest

from amagama.updater import compressed, fetchers
from amagama.updater.fetchers import FetchError, UbuntuLanguagePacks, deb_data
from amagama.updater.sources import Block


def deb(files, compression="xz"):
    """A .deb (an ar archive) whose data.tar.<compression> holds files."""
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo("./" + name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    data = tar_bytes.getvalue()
    if compression == "xz":
        data = lzma.compress(data)
    else:
        data = compressed._frame(data, 3)
    members = [("debian-binary", b"2.0\n"), ("control.tar.xz", lzma.compress(b"")),
               ("data.tar." + compression, data)]
    archive = b"!<arch>\n"
    for name, content in members:
        archive += ("%-16s%-12s%-6s%-6s%-8s%-10d`\n" % (name, 0, 0, 0, 100644, len(content))).encode()
        archive += content + (b"\n" if len(content) % 2 else b"")
    return archive


MO = "usr/share/locale-langpack/%s/LC_MESSAGES/%s.mo"


@pytest.fixture
def archive(tmp_path):
    """An Ubuntu archive with a series and its -updates pocket."""
    root = tmp_path / "ubuntu"

    def publish(suite, packs):
        index = []
        for package, content in packs.items():
            path = root / "pool" / suite / (package + ".deb")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            index.append("Package: %s\nArchitecture: all\nFilename: pool/%s/%s.deb\n"
                         "SHA512: %s\nDescription: a pack\n multi-line" % (
                             package, suite, package, hashlib.sha512(content).hexdigest()))
        directory = root / "dists" / suite / "main" / "binary-amd64"
        directory.mkdir(parents=True)
        (directory / "Packages.xz").write_bytes(lzma.compress("\n\n".join(index).encode()))

    publish("noble", {
        "language-pack-af-base": deb({MO % ("af", "apport"): b"old", "usr/share/doc/x": b"doc"}),
        "language-pack-pt-base": deb({MO % ("pt", "apport"): b"pt", MO % ("pt_BR", "apport"): b"br"},
                                     "zst"),
        "language-pack-gnome-af-base": deb({MO % ("af", "gnome-shell"): b"gnome"}),
        "language-support-af": deb({}),
    })
    # The pocket's newer pack replaces the series'.
    publish("noble-updates", {"language-pack-af-base": deb({MO % ("af", "apport"): b"new"})})
    return root.as_uri()


def fetcher(archive, **settings):
    return UbuntuLanguagePacks(dict({"series": "noble", "base_url": archive, "pause": 0}, **settings))


def test_languages(archive):
    assert fetcher(archive).languages() == ["af", "pt"]


def test_fetch(archive, tmp_path):
    dest = tmp_path / "dest"
    found = fetcher(archive).fetch(str(dest))
    # Only the translations, under the pack's language.
    assert sorted(found) == [("af/" + MO % ("af", "apport"), "af"),
                             ("pt/" + MO % ("pt", "apport"), "pt"),
                             ("pt/" + MO % ("pt_BR", "apport"), "pt")]
    assert (dest / "af" / (MO % ("af", "apport"))).read_bytes() == b"new"
    assert (dest / "pt" / (MO % ("pt_BR", "apport"))).read_bytes() == b"br"


def test_fetch_some_languages(archive, tmp_path):
    assert fetcher(archive).fetch(str(tmp_path), ["pt"]) == [
        ("pt/" + MO % ("pt", "apport"), "pt"), ("pt/" + MO % ("pt_BR", "apport"), "pt")]


def test_fingerprint(archive, tmp_path):
    first = fetcher(archive).fingerprint()
    assert first == fetcher(archive).fingerprint()
    pool = tmp_path / "ubuntu" / "pool" / "noble-updates" / "language-pack-af-base.deb"
    pool.write_bytes(deb({MO % ("af", "apport"): b"newer"}))
    # The index still gives the old SHA512: the download doesn't match it.
    with pytest.raises(FetchError, match="SHA512 doesn't match the index"):
        fetcher(archive).fetch(str(tmp_path / "dest"), ["af"])


def test_updates_pocket_missing(archive, monkeypatch):
    download = fetchers.download

    def missing_updates(url, path):
        if "-updates/" in url:
            raise FetchError("%s: HTTP Error 404: Not Found" % url, 404)
        return download(url, path)

    monkeypatch.setattr(fetchers, "download", missing_updates)
    assert fetcher(archive).languages() == ["af", "pt"]


def test_series_missing(archive):
    with pytest.raises(FetchError):
        fetcher(archive, series="nonesuch").languages()


def test_deb_data_errors(tmp_path):
    path = tmp_path / "x.deb"
    path.write_bytes(b"not an archive")
    with pytest.raises(FetchError, match="not a .deb"):
        deb_data(str(path))
    path.write_bytes(b"!<arch>\n")
    with pytest.raises(FetchError, match="no data.tar"):
        deb_data(str(path))


def test_block(archive, tmp_path):
    block = Block("ubuntu", "Ubuntu", "ubuntu-langpacks", series="noble", base_url=archive, pause=0,
                  files=["*/usr/share/locale-langpack/{lang}/LC_MESSAGES/apport.mo"])
    assert block.rotates
    found = block.fetcher.fetch(str(tmp_path))
    # The language is the locale in the path, not the pack's.
    assert sorted(block.selection.select(path, lang) for path, lang in found) == ["af", "pt", "pt_BR"]


def test_index_unreadable(archive, tmp_path):
    (tmp_path / "ubuntu/dists/noble/main/binary-amd64/Packages.xz").write_bytes(b"not xz")
    with pytest.raises(FetchError, match="Packages.xz"):
        fetcher(archive).languages()


def test_data_unreadable(archive, tmp_path):
    broken = b"!<arch>\n" + b"%-16s%-12s%-6s%-6s%-8s%-10d`\n" % (
        b"data.tar.xz", b"0", b"0", b"0", b"100644", 6) + b"not xz"
    pool = tmp_path / "ubuntu/pool/noble/language-pack-pt-base.deb"
    pool.write_bytes(broken)
    index = tmp_path / "ubuntu/dists/noble/main/binary-amd64/Packages.xz"
    index.write_bytes(lzma.compress(
        ("Package: language-pack-pt-base\nFilename: pool/noble/language-pack-pt-base.deb\n"
         "SHA512: %s\n" % hashlib.sha512(broken).hexdigest()).encode()))
    with pytest.raises(FetchError, match="language-pack-pt-base.deb"):
        fetcher(archive).fetch(str(tmp_path / "dest"), ["pt"])
