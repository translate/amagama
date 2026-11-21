#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import contextlib
import io
import os
import socket
import tarfile
import threading
import time

import pytest

from amagama.updater import fetchers
from amagama.updater.fetchers import FetchError, HttpPerLanguage
from amagama.updater.sources import Block


@pytest.fixture
def fetcher(monkeypatch):
    fetcher = HttpPerLanguage({"base_url": "https://example.org/compendia",
                               "path": "{lang}/{lang}.po", "pause": 0})
    monkeypatch.setattr(fetcher, "languages", lambda: ["fr"])

    def server_error(*args):
        raise FetchError("https://example.org/compendia/fr/fr.po: HTTP Error 500", 500)
    monkeypatch.setattr(fetchers, "http_fingerprint", server_error)
    monkeypatch.setattr(fetchers, "download", server_error)
    return fetcher


def test_url(fetcher):
    assert fetcher.url("pt_BR") == "https://example.org/compendia/pt_BR/pt_BR.po"


def test_server_error_stops_fingerprint(fetcher):
    with pytest.raises(FetchError, match="HTTP Error 500"):
        fetcher.fingerprint()


def test_server_error_stops_fetch(fetcher, tmp_path):
    with pytest.raises(FetchError, match="HTTP Error 500"):
        fetcher.fetch(str(tmp_path))


def test_fetch_some_languages(fetcher, monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "languages", lambda: ["de", "fr"])
    monkeypatch.setattr(fetchers, "download", lambda url, path: open(path, "w").close())
    assert fetcher.fetch(str(tmp_path), ["fr"]) == [("fr/fr.po", "fr")]


def test_slow_download_times_out(monkeypatch, tmp_path):
    """A server sending a byte now and then is cut off at DEADLINE."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def trickle():
        connection, _ = listener.accept()
        with connection, contextlib.suppress(OSError):
            connection.recv(4096)
            connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 6\r\n\r\n")
            for byte in b"af.po\n":
                connection.sendall(bytes([byte]))
                time.sleep(0.5)
    threading.Thread(target=trickle, daemon=True).start()
    monkeypatch.setattr(fetchers, "DEADLINE", 1, raising=False)
    url = "http://127.0.0.1:%d/af.po" % listener.getsockname()[1]
    try:
        with pytest.raises(FetchError, match="took more than 1 seconds"):
            fetchers.download(url, str(tmp_path / "af.po"))
    finally:
        listener.close()


@pytest.mark.parametrize("parts", [("fr",), ("fr", "fr.po"), ("a/../fr",)])
def test_inside(parts, tmp_path):
    assert fetchers.inside(str(tmp_path), *parts) == os.path.normpath(os.path.join(tmp_path, *parts))


@pytest.mark.parametrize("parts", [("..",), (".",), ("../x",), ("fr", "../../x"), ("/etc",)])
def test_inside_refuses(parts, tmp_path):
    with pytest.raises(FetchError, match="not a path in the fetch"):
        fetchers.inside(str(tmp_path), *parts)


def test_listed_code_outside_fetch(monkeypatch, tmp_path):
    """A listing entry that decodes to ../ isn't fetched outside the fetch."""
    fetcher = HttpPerLanguage({"base_url": "https://example.org/compendia",
                               "path": "{lang}/{lang}.po", "pause": 0})
    monkeypatch.setattr(fetchers, "http_text", lambda url: '<a href="%2e%2e%2fescaped/">x</a>')
    monkeypatch.setattr(fetchers, "download", lambda url, path: open(path, "w").close())
    assert fetcher.languages() == ["../escaped"]
    with pytest.raises(FetchError, match="not a path in the fetch"):
        fetcher.fetch(str(tmp_path / "out"))
    assert not (tmp_path / "escaped").exists()


def test_block_refuses_path_outside_fetch(monkeypatch, tmp_path):
    """A fetcher's path outside the fetch isn't removed as an unknown language."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "notes.txt").write_text("keep")
    block = Block("x", "X", "http", base_url="https://example.org/", path="{lang}/{lang}.po")
    monkeypatch.setattr(block.fetcher, "fetch", lambda dest: [("../victim/notes.txt", "xx")])
    monkeypatch.setattr(block, "fingerprint", lambda cache_dir: None)
    monkeypatch.setattr(block.fetcher, "rotates", False)
    with pytest.raises(FetchError, match="not a path in the fetch"):
        block.fetch(str(tmp_path))
    assert (victim / "notes.txt").read_text() == "keep"


@pytest.fixture
def server():
    """A local HTTP server sending replies[path] as raw bytes, then closing."""
    replies = {}
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(5)

    def serve():
        while True:
            try:
                connection, _ = listener.accept()
            except OSError:
                return
            with connection:
                request = connection.recv(4096).decode("latin-1")
                connection.sendall(replies[request.split()[1]])
    threading.Thread(target=serve, daemon=True).start()
    yield "http://127.0.0.1:%d" % listener.getsockname()[1], replies
    with contextlib.suppress(OSError):
        listener.shutdown(socket.SHUT_RDWR)
    listener.close()


def test_download(server, tmp_path):
    url, replies = server
    replies["/fr.po"] = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nfr po"
    fetchers.download(url + "/fr.po", str(tmp_path / "fr.po"))
    assert (tmp_path / "fr.po").read_bytes() == b"fr po"


def test_short_download_fails(server, tmp_path):
    url, replies = server
    replies["/fr.po"] = b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"x" * 10
    with pytest.raises(FetchError, match="got 10 of 1000 bytes"):
        fetchers.download(url + "/fr.po", str(tmp_path / "fr.po"))


def test_cut_chunked_download_fails(server, tmp_path):
    url, replies = server
    replies["/fr.po"] = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n10\r\nfr po"
    with pytest.raises(FetchError, match="IncompleteRead"):
        fetchers.download(url + "/fr.po", str(tmp_path / "fr.po"))


def test_text_not_utf8_fails(server):
    url, replies = server
    replies["/"] = b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n\xff\xfe<a"
    with pytest.raises(FetchError, match="can't decode"):
        fetchers.http_text(url + "/")


def test_truncated_tarball_fails(tmp_path):
    path = tmp_path / "fr.tgz"
    with tarfile.open(path, "w:gz") as tar:
        data = os.urandom(100000)
        info = tarfile.TarInfo("fr/fr.po")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    path.write_bytes(path.read_bytes()[:20000])
    with pytest.raises(FetchError, match="fr.tgz"):
        fetchers.extract_tarball(str(path), str(tmp_path / "out"))
