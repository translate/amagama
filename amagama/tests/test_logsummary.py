#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import gzip

import pytest
from click.testing import CliRunner

from amagama.cli import manage
from amagama.logsummary import BULK, ORDINARY, PREFETCH, classify, summarise


@pytest.mark.parametrize("value, kind", [
    (None, ORDINARY),
    ("", ORDINARY),
    ("prerender", ORDINARY),
    ("prefetchx;bulk", ORDINARY),
    ("xprefetch;bulk", ORDINARY),
    ('"prefetch";bulk', ORDINARY),
    ("prefetch", PREFETCH),
    ("prefetch;bulky", PREFETCH),
    ("prefetch;bulk=?0", PREFETCH),
    ("prefetch;anonymous-client-ip", PREFETCH),
    ("prefetch;bulk", BULK),
    ("prefetch;bulk=?1", BULK),
    ("prefetch; bulk", BULK),
    ("Prefetch ; BULK", BULK),
    ("prefetch;a=1;bulk;b", BULK),
    ('prefetch;a="x,;y";bulk', BULK),
    ('prefetch;a="\\";bulk";c', PREFETCH),
    ("prerender, prefetch;bulk", BULK),
    ("prefetch;bulk, prerender", BULK),
    ("prefetch, prerender;bulk", PREFETCH),
])
def test_classify(value, kind):
    assert classify(value) == kind


LINE = ('203.0.113.1 - - [07/Nov/2026:10:00:00 +0000] "GET %s HTTP/1.1" %d 2 '
        '"-" "Virtaal/1.1.0"%s\n')


def line(path="/api/v1/en/af/unit/file", status=200, purpose=None):
    return LINE % (path, status, "" if purpose is None else ' "%s"' % purpose)


@pytest.fixture
def logs(tmp_path):
    plain = tmp_path / "live-nginx.access.log"
    plain.write_text("".join([
        line(purpose="-"),
        line("/tmserver/en/af/unit/file"),  # combined format, no header field
        line(purpose="prefetch"),
        line(purpose="prefetch;bulk"),
        line(purpose="prefetch;bulk", status=429),
        line("/static/css/amagama.css", purpose="prefetch;bulk"),
        line("/", purpose="-"),
        "not a log line\n",
    ]), encoding="utf-8")
    rotated = tmp_path / "live-nginx.access.log-20261031.gz"
    with gzip.open(rotated, "wt", encoding="utf-8") as f:
        f.write(line(purpose="prefetch;bulk", status=429))
    return plain, rotated


def test_summarise(logs):
    assert summarise([str(p) for p in logs]) == {
        ORDINARY: [2, 0], PREFETCH: [1, 0], BULK: [3, 2]}


def test_log_summary_command(logs):
    result = CliRunner().invoke(manage, ["log_summary", *map(str, logs)])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines() == [
        "kind\trequests\tlimited (429)",
        "ordinary\t2\t0",
        "prefetch\t1\t0",
        "bulk\t3\t2",
    ]
