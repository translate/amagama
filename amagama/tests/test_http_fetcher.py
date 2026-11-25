#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import contextlib
import http.client
import io
import os
import socket
import ssl
import subprocess
import tarfile
import threading
import time
import urllib.error

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


def test_rate_limit_wait():
    assert fetchers.rate_limit_wait(
        {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1000000100"}, 60) <= fetchers.MAX_WAIT
    assert fetchers.rate_limit_wait({}, 60) == 60
    # GitHub rounds Retry-After down.
    assert fetchers.rate_limit_wait({"Retry-After": "1"}, 60) == 2
    assert fetchers.rate_limit_wait({"Retry-After": "5000"}, 60) == fetchers.MAX_WAIT


def test_http_error_keeps_headers(monkeypatch):
    headers = http.client.HTTPMessage()
    headers["Retry-After"] = "623"

    def refuse(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "Too Many Requests", headers, None)
    monkeypatch.setattr(fetchers.urllib.request, "urlopen", refuse)
    with pytest.raises(FetchError) as error:
        fetchers.http_text("https://example.org/api/")
    assert error.value.status == 429
    assert error.value.headers["Retry-After"] == "623"
    assert error.value.transient


def failing(errors, body=b"ok"):
    """An urlopen raising errors in turn, then answering body; its requests."""
    requests = []

    def urlopen(request, timeout):
        requests.append(request.full_url)
        if errors:
            raise errors.pop(0)
        response = io.BytesIO(body)
        response.headers = {"Content-Length": str(len(body))}
        return response
    return urlopen, requests


def http_error(code):
    return urllib.error.HTTPError("https://example.org/x", code, "error", http.client.HTTPMessage(), None)


@pytest.mark.parametrize("error", [
    http_error(503), http_error(429),
    urllib.error.URLError(ConnectionResetError("reset")), TimeoutError("timed out")])
def test_transient_errors_retried(monkeypatch, pauses, error):
    urlopen, requests = failing([error, error])
    monkeypatch.setattr(fetchers.urllib.request, "urlopen", urlopen)
    assert fetchers.http_text("https://example.org/x") == "ok"
    assert pauses == [fetchers.RETRY_WAIT, 2 * fetchers.RETRY_WAIT]
    assert len(requests) == 3


@pytest.mark.parametrize("error", [
    http_error(404), http_error(403), urllib.error.URLError("unknown url type: gopher"),
    urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))])
def test_lasting_errors_not_retried(monkeypatch, pauses, error):
    urlopen, requests = failing([error])
    monkeypatch.setattr(fetchers.urllib.request, "urlopen", urlopen)
    with pytest.raises(FetchError):
        fetchers.http_text("https://example.org/x")
    assert pauses == []
    assert len(requests) == 1


def test_failing_host_tried_once(monkeypatch, pauses):
    errors = [http_error(503)] * (fetchers.RETRIES + 2)
    urlopen, requests = failing(errors)
    monkeypatch.setattr(fetchers.urllib.request, "urlopen", urlopen)
    with pytest.raises(FetchError, match="503"):
        fetchers.http_text("https://example.org/a")
    assert len(requests) == 1 + fetchers.RETRIES
    # The host's next request isn't held up as long again.
    with pytest.raises(FetchError, match="503"):
        fetchers.http_text("https://example.org/b")
    assert len(requests) == 2 + fetchers.RETRIES
    assert len(pauses) == fetchers.RETRIES
    # Until one works.
    assert fetchers.http_text("https://example.org/c") == "ok"
    errors.append(http_error(503))
    assert fetchers.http_text("https://example.org/d") == "ok"
    assert len(pauses) == fetchers.RETRIES + 1


def git_failure(stderr):
    return subprocess.CalledProcessError(128, ["git"], stderr=stderr)


def test_git_network_failure_retried(monkeypatch, pauses):
    answers = [git_failure("fatal: unable to access 'https://git.example/r/': "
                           "Could not resolve host: git.example"),
               subprocess.CompletedProcess(["git"], 0, stdout="abc\tHEAD\n")]

    def run(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(fetchers.subprocess, "run", run)
    assert fetchers.run_tool("git", "ls-remote", "https://git.example/r", "HEAD") == "abc\tHEAD\n"
    assert pauses == [fetchers.RETRY_WAIT]


def test_git_host_from_url(monkeypatch, pauses):
    # A failing mirror doesn't stop the upstream's retries.
    fetchers._failing_hosts.add("mirror.example")
    answers = [git_failure("fatal: early EOF"), subprocess.CompletedProcess(["git"], 0, stdout="")]

    def run(*args, **kwargs):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer
    monkeypatch.setattr(fetchers.subprocess, "run", run)
    fetchers.run_tool("git", "ls-remote", "https://up.example/r", "HEAD", host="mirror.example")
    assert pauses == [fetchers.RETRY_WAIT]


def test_git_lasting_failure_not_retried(monkeypatch, pauses):
    calls = []

    def run(*args, **kwargs):
        calls.append(args)
        raise git_failure("remote: Repository not found.\nfatal: repository not found")
    monkeypatch.setattr(fetchers.subprocess, "run", run)
    with pytest.raises(FetchError, match="not found"):
        fetchers.run_tool("git", "ls-remote", "https://git.example/r", "HEAD")
    assert len(calls) == 1
    assert pauses == []


def test_rate_limited_403_is_not_missing(fetcher, monkeypatch):
    def limited(url):
        raise FetchError(url + ": HTTP Error 403", 403, {"Retry-After": "5"}, transient=True)
    monkeypatch.setattr(fetchers, "http_fingerprint", limited)
    with pytest.raises(FetchError, match="403"):
        fetcher.fingerprint()

    def forbidden(url):
        raise FetchError(url + ": HTTP Error 403", 403)
    monkeypatch.setattr(fetchers, "http_fingerprint", lambda url: None)
    missing = fetcher.fingerprint()
    monkeypatch.setattr(fetchers, "http_fingerprint", forbidden)
    assert fetcher.fingerprint() == missing


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


@pytest.mark.parametrize("length, body, pause, close_after", [
    # A byte now and then.
    (b"Content-Length: 6\r\n", [bytes([byte]) for byte in b"af.po\n"], 0.5, 0),
    # All of a reply of no length, but the connection held open.
    (b"", [b"af.po\n"], 0, 1.5),
])
def test_slow_download_times_out(monkeypatch, tmp_path, length, body, pause, close_after):
    """A reply still coming at DEADLINE is cut off."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def trickle():
        connection, _ = listener.accept()
        with connection, contextlib.suppress(OSError):
            connection.recv(4096)
            connection.sendall(b"HTTP/1.1 200 OK\r\n" + length + b"\r\n")
            for piece in body:
                connection.sendall(piece)
                time.sleep(pause)
            time.sleep(close_after)
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


def test_read_error_retried(monkeypatch, pauses):
    class Reset(io.BytesIO):
        headers = {}

        def read1(self, size):
            raise ConnectionResetError("reset")
    monkeypatch.setattr(fetchers.urllib.request, "urlopen", lambda request, timeout: Reset())
    with pytest.raises(FetchError, match="reset") as error:
        fetchers.http_text("https://example.org/x")
    assert error.value.transient
    assert len(pauses) == fetchers.RETRIES


def test_short_text_retried(server, pauses):
    url, replies = server
    replies["/api/"] = b"HTTP/1.1 200 OK\r\nContent-Length: 1000\r\n\r\n" + b"x" * 10
    with pytest.raises(FetchError, match="got 10 of 1000 bytes"):
        fetchers.http_text(url + "/api/")
    assert len(pauses) == fetchers.RETRIES


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


class TestListingFails(object):
    """A listing that fails, served as a success, keeps the last fetch."""

    @pytest.fixture
    def listing(self, monkeypatch):
        pages = {}
        monkeypatch.setattr(fetchers, "http_text", lambda url: pages["listing"])
        monkeypatch.setattr(fetchers, "download", lambda url, path: open(path, "w").write(
            'msgid ""\nmsgstr ""\n\nmsgid "a"\nmsgstr "b"\n'))
        monkeypatch.setattr(fetchers.time, "sleep", lambda seconds: None)
        return pages

    def block(self):
        return Block("debian", "Debian", "http", base_url="https://example.org/",
                     path="{lang}/{lang}.po", max_age_days=1)

    def fetch_all(self, listing, cache, *langs):
        listing["listing"] = "".join('<a href="%s/">%s</a>' % (lang, lang) for lang in langs)
        state = self.block().fetch(str(cache))
        assert sorted(lang for path, lang in state["files"]) == sorted(langs)

    def test_no_languages(self, listing, tmp_path):
        self.fetch_all(listing, tmp_path, "af", "fr")
        listing["listing"] = "<html>Down for maintenance</html>"
        with pytest.raises(FetchError, match="0 languages listed, 2 last time"):
            self.block().fetch(str(tmp_path))
        assert sorted(os.listdir(tmp_path / "debian")) == ["af", "fetched.json", "fr"]

    def test_few_languages(self, listing, tmp_path):
        self.fetch_all(listing, tmp_path, "af", "de", "fr", "zu")
        listing["listing"] = '<a href="af/">af</a>'
        with pytest.raises(FetchError, match="1 languages listed, 4 last time"):
            self.block().fetch(str(tmp_path))

    def test_nothing_fetched(self, monkeypatch, tmp_path):
        block = Block("x", "X", "http", base_url="https://example.org/", path="{lang}/{lang}.po")
        monkeypatch.setattr(block.fetcher, "rotates", False)
        monkeypatch.setattr(block, "fingerprint", lambda cache_dir: None)
        monkeypatch.setattr(block.fetcher, "fetch", lambda dest: [])
        with pytest.raises(FetchError, match="no files fetched"):
            block.fetch(str(tmp_path))
