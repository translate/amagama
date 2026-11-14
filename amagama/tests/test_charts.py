#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


import datetime

from amagama import charts

UTC = datetime.timezone.utc
KINDS = ("ordinary", "prefetch", "bulk")


def at(day, hour=0):
    return datetime.datetime(2026, 11, day, hour, tzinfo=UTC)


def use(*counts, start=1):
    """Days from 2026-11-<start>, one (ordinary, prefetch, bulk, limited) each."""
    return [dict(zip(KINDS + ("limited",), n), date="2026-11-%02d" % (start + day))
            for day, n in enumerate(counts)]


class TestNiceTicks(object):

    def test_steps(self):
        assert charts.nice_ticks(0, 108) == [0, 50, 100, 150]
        assert charts.nice_ticks(0, 90) == [0, 25, 50, 75, 100]
        assert charts.nice_ticks(9, 15) == [8, 10, 12, 14, 16]
        assert charts.nice_ticks(28_000_000, 29_200_000) == [
            28_000_000, 28_500_000, 29_000_000, 29_500_000]

    def test_whole_numbers_only(self):
        assert charts.nice_ticks(0, 1) == [0, 1]
        assert charts.nice_ticks(0, 3) == [0, 1, 2, 3]
        assert charts.nice_ticks(0, 0) == [0, 1]


class TestDateTicks(object):

    def test_days(self):
        assert charts.date_ticks(datetime.date(2026, 11, 1), datetime.date(2026, 11, 3), 3) == [
            (datetime.date(2026, 11, n), "2026-11-%02d" % n) for n in (1, 2, 3)]

    def test_mondays(self):
        assert charts.date_ticks(datetime.date(2026, 11, 1), datetime.date(2026, 11, 20), 3) == [
            (datetime.date(2026, 11, n), "2026-11-%02d" % n) for n in (2, 9, 16)]

    def test_months(self):
        assert [label for day, label in charts.date_ticks(
            datetime.date(2026, 7, 17), datetime.date(2026, 11, 13), 5)] == [
            "2026-08", "2026-09", "2026-10", "2026-11"]
        assert [label for day, label in charts.date_ticks(
            datetime.date(2025, 11, 14), datetime.date(2026, 11, 13), 3)] == [
            "2026-01", "2026-07"]


class TestLine(object):

    def test_path(self):
        chart = charts.line([(at(1), 10), (at(4, 12), 15), (at(8), 20)])
        assert (chart["left"], chart["right"], chart["bottom"]) == (21.0, 316, 151)
        assert chart["path"] == "M21.0,151.0 L168.5,80.2 L316.0,9.5"
        assert chart["dots"] == [(21.0, 151.0), (168.5, 80.2), (316.0, 9.5)]
        assert chart["yTicks"] == [(151.0, 10), (80.2, 15), (9.5, 20)]
        assert chart["xTicks"] == [(63.1, "2026-11-02", "middle")]
        assert chart["first"] == (at(1), 10)
        assert chart["last"] == (at(8), 20)

    def test_axis_starts_at_zero_for_big_changes(self):
        chart = charts.line([(at(1), 2), (at(2), 9)])
        assert [value for y, value in chart["yTicks"]][0] == 0

    def test_flat_line(self):
        chart = charts.line([(at(1), 7), (at(8), 7)])
        assert [value for y, value in chart["yTicks"]] == [0, 2, 4, 6, 8]
        assert chart["dots"][0][1] == chart["dots"][1][1] < chart["bottom"]

    def test_one_day(self):
        chart = charts.line([(at(1, 5), 1), (at(1, 6), 2)])
        assert chart["xTicks"] == [(14.5, "2026-11-01", "start")]

    def test_fewer_than_two_points(self):
        assert charts.line([]) is None
        assert charts.line([(at(1), 10)]) is None


class TestBands(object):

    def test_daily(self):
        weekly, found = charts.bands(use((1, 2, 3, 0), (4, 0, 0, 0), start=2), KINDS)
        assert not weekly
        assert [(band["start"].day, band["ordinary"], band["bulk"]) for band in found] == [
            (2, 1, 3), (3, 4, 0)]

    def test_missing_day_is_zero(self):
        weekly, found = charts.bands([use((5, 0, 0, 0))[0], use((5, 0, 0, 0), start=3)[0]],
                                     KINDS)
        assert [band["ordinary"] for band in found] == [5, 0, 5]

    def test_weekly_averages_the_days_in_range(self):
        days = use(*[(n, 0, 0, 0) for n in range(1, 11)])
        weekly, found = charts.bands(days, KINDS, daily=7)
        assert weekly
        # 2026-11-01 is a Sunday: its week has only that day.
        assert [(band["start"], band["days"], band["ordinary"]) for band in found] == [
            (datetime.date(2026, 10, 26), 1, 1), (datetime.date(2026, 11, 2), 7, 5),
            (datetime.date(2026, 11, 9), 2, 9.5)]


class TestColumns(object):

    def test_stacks(self):
        chart = charts.columns(use((10, 5, 0, 0), (20, 0, 5, 2)), KINDS, "limited")
        assert not chart["weekly"]
        assert chart["columns"] == [
            [("ordinary", 61.2, 172.7, 242.0, 79.3), ("prefetch", 61.2, 133.0, 242.0, 39.7)],
            [("ordinary", 363.8, 93.3, 242.0, 158.7), ("bulk", 363.8, 53.7, 242.0, 39.6)]]
        assert chart["line"] == "M182.2,252.0 L484.8,236.1"
        assert chart["yTicks"] == [(252.0, 0), (172.7, 10), (93.3, 20), (14.0, 30)]
        assert (chart["start"], chart["end"], chart["total"], chart["average"]) == (
            datetime.date(2026, 11, 1), datetime.date(2026, 11, 2), 40, 20)

    def test_no_line_without_refusals(self):
        chart = charts.columns(use((10, 5, 0, 0), (20, 0, 5, 0)), KINDS, "limited")
        assert chart["line"] is None
        assert chart["dots"] == []

    def test_weekly_after_daily_days(self):
        chart = charts.columns(use(*[(n, 0, 0, 0) for n in range(1, 11)]), KINDS, daily=7)
        assert chart["weekly"]
        assert len(chart["columns"]) == 3
        assert chart["total"] == 55

    def test_fewer_than_two_days(self):
        assert charts.columns(use((1, 0, 0, 0)), KINDS) is None
