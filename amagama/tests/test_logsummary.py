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
from amagama.logsummary import (
    BULK, ORDINARY, PREFETCH, classify, lookups_per_second, over_limit, peaks, percentile,
    summarise)


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


def at(client, time, purpose="-"):
    if " " not in time:
        time += " +0000"
    return ('%s - - [%s] "GET /api/v1/en/af/unit/file HTTP/1.1" 200 2 '
            '"-" "Virtaal/1.1.0" "%s"\n' % (client, time, purpose))


@pytest.fixture
def rates_log(tmp_path):
    log = tmp_path / "live-nginx.access.log"
    log.write_text("".join(
        [at("203.0.113.1", "07/Nov/2026:10:00:00")] * 3
        + [at("203.0.113.1", "07/Nov/2026:10:00:59", "prefetch")]
        + [at("203.0.113.1", "07/Nov/2026:10:01:00")]
        + [at("203.0.113.1", "07/Nov/2026:10:00:00", "prefetch;bulk")] * 9
        + [at("2001:db8::1", "08/Nov/2026:01:59:59 +0200")] * 2
        # Not times: left out.
        + [at("203.0.113.2", "31/Nov/2026:10:00:00"), at("203.0.113.2", "07/Nov/2026")]
        + [line("/static/css/amagama.css")]
    ), encoding="utf-8")
    return log


def test_lookups_per_second(rates_log):
    clients = sorted(lookups_per_second([str(rates_log)]), key=len)
    start = 1794045600  # 07/Nov/2026:10:00:00 +0000
    assert clients == [
        {start + 50399: 2},  # 07/Nov/2026:23:59:59 +0000
        {start: 3, start + 59: 1, start + 60: 1},
    ]


def test_peaks():
    assert peaks({0: 3, 59: 1, 60: 1}) == (3, 4)
    assert peaks({0: 1, 30: 2, 89: 3, 200: 1}) == (3, 5)
    assert peaks({}) == (0, 0)


@pytest.mark.parametrize("seconds, refused", [
    # 200 at once against rate 5 burst 50: the burst test's 51 served.
    ({0: 200}, 149),
    ({0: 51}, 0),
    ({0: 52}, 1),
    # The bucket drains at the rate: 5 more a second later.
    ({0: 51, 1: 6}, 1),
    # A steady 5 a second never fills it.
    ({t: 5 for t in range(600)}, 0),
    # 6 a second fills it in 45 seconds, then one a second is refused.
    ({t: 6 for t in range(60)}, 14),
])
def test_over_limit(seconds, refused):
    assert over_limit(seconds, 5, 50) == refused


def test_percentile():
    assert [percentile([1, 2, 3, 4], share) for share in (50, 90, 100)] == [2, 4, 4]
    assert percentile([7], 50) == 7


def test_log_summary_rates(rates_log):
    result = CliRunner().invoke(
        manage, ["log_summary", "--rate", "1", "--burst", "1", str(rates_log)])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[4:] == [
        "",
        "clients\t2",
        "peak lookups\tper second\tper minute",
        "50%\t2\t2",
        "90%\t3\t4",
        "99%\t3\t4",
        "max\t3\t4",
        "refused at 1 a second, burst 1\t1 lookups\t1 clients",
    ]


def test_log_summary_rates_without_lookups(logs):
    result = CliRunner().invoke(manage, ["log_summary", "--rates", str(logs[1])])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[4:] == ["", "clients\t0"]


def test_log_summary_rate_needs_burst(logs):
    result = CliRunner().invoke(manage, ["log_summary", "--rate", "5", str(logs[0])])
    assert result.exit_code == 2
    assert "--rate and --burst go together" in result.output


@pytest.mark.parametrize('rate', ['nan', 'inf'])
def test_log_summary_rate_must_be_a_number(logs, rate):
    result = CliRunner().invoke(
        manage, ["log_summary", "--rate", rate, "--burst", "5", str(logs[0])])
    assert result.exit_code == 2
    assert "--rate must be a number" in result.output
