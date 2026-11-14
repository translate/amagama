#
# Copyright (C) amaGama contributors.
#
# This file is part of amaGama. It is distributed under the GPL3 or
# later license. See the LICENSE file for a copy of the license and
# the AUTHORS.md file for copyright and authorship information.


"""The geometry of the statistics page's charts, which its template draws as
inline SVG: a line over time and columns stacked by kind.

Coordinates are in the SVG's viewBox units, rounded to one decimal.
"""

import datetime
import math

# Days of use shown day by day; more are shown by week.
DAILY = 92
# viewBox units per character of an axis label, at the largest size the page
# draws them (see .stats-chart in amagama.css).
CHARACTER = {"line": 6.5, "columns": 11.5}
# Axis labels' height, and the gap between a label and the plot.
LABEL = {"line": 11, "columns": 20}
GAP = 4


def _round(value):
    return round(value, 1)


def nice_ticks(low, high, most=4):
    """Round values from at or below low to at or above high, at most most + 1
    of them, a whole step of 1, 2, 2.5 or 5 times a power of ten apart."""
    if high <= low:
        high = low + 1
    raw = (high - low) / most
    power = 10 ** math.floor(math.log10(raw))
    # 2.5 only where it gives whole numbers.
    multiples = (1, 2, 2.5, 5, 10) if power >= 10 else (1, 2, 5, 10)
    step = max(1, next(n * power for n in multiples if n * power >= raw))
    while True:
        first = math.floor(low / step) * step
        last = math.ceil(high / step) * step
        if (last - first) / step <= most:
            break
        step = next(n * power for n in (2, 5, 10, 20, 50) if n * power > step)
    count = round((last - first) / step)
    return [int(first + n * step) for n in range(count + 1)]


def _aligned(start, end, kind, step):
    """The dates from start to end that begin a step of kind (day, week, month)."""
    day = start
    while day <= end:
        if kind == "day" and day.toordinal() % step == 0 or \
                kind == "week" and day.weekday() == 0 and day.toordinal() // 7 % step == 0 or \
                kind == "month" and day.day == 1 and (day.month - 1) % step == 0:
            yield day
        day += datetime.timedelta(days=1)


def date_ticks(start, end, most):
    """[(date, label)] of at most most dates from start to end, a whole number of
    days, weeks or months apart: months are labelled YYYY-MM, others YYYY-MM-DD."""
    steps = [("day", 1), ("day", 2), ("week", 1), ("week", 2)] + \
        [("month", n) for n in (1, 2, 3, 6)] + [("month", 12 * n) for n in range(1, 100)]
    for kind, step in steps:
        ticks = list(_aligned(start, end, kind, step))
        if len(ticks) <= most:
            break
    form = "%Y-%m" if kind == "month" else "%Y-%m-%d"
    return [(day, day.strftime(form)) for day in ticks]


def _frame(kind, width, height, values):
    """The plot's left, top, right and bottom, leaving room for the labels of values."""
    longest = max(len("{:,}".format(value)) for value in values)
    return (_round(longest * CHARACTER[kind] + 2 * GAP), LABEL[kind] / 2 + GAP,
            width - GAP, height - LABEL[kind] - 2 * GAP)


def _y_axis(ticks, top, bottom):
    low, high = ticks[0], ticks[-1]

    def y(value):
        return _round(bottom - (value - low) / (high - low) * (bottom - top))
    return y, [(y(value), value) for value in ticks]


def _x_labels(ticks, x, left, right, kind):
    """[(x, label, anchor)] of date ticks, kept within left and right."""
    half = 5 * CHARACTER[kind]
    labels = []
    for day, label in ticks:
        at = x(day)
        anchor = "start" if at - half < left else "end" if at + half > right else "middle"
        labels.append((at, label, anchor))
    return labels


def line(points, width=320, height=170):
    """A line through points, [(datetime, value)] in time order, or None for fewer
    than two: {"width", "height", "left", "right", "bottom", "path", "dots",
    "yTicks": [(y, value)], "xTicks": [(x, label, anchor)], "first", "last"}."""
    if len(points) < 2:
        return None
    values = [value for when, value in points]
    low, high = min(values), max(values)
    # The axis starts at zero unless the values change, by less than they are.
    ticks = nice_ticks(low if 0 < high - low <= low else 0, high)
    left, top, right, bottom = _frame("line", width, height, ticks)
    y, y_ticks = _y_axis(ticks, top, bottom)
    start, end = points[0][0], points[-1][0]
    span = (end - start).total_seconds()

    def x(when):
        if not isinstance(when, datetime.datetime):
            when = datetime.datetime.combine(when, datetime.time(), start.tzinfo)
        if not span:
            return _round((left + right) / 2)
        return _round(left + (when - start).total_seconds() / span * (right - left))
    dots = [(x(when), y(value)) for when, value in points]
    path = "M" + " L".join("%s,%s" % dot for dot in dots)
    # A tick's midnight can fall before the first rebuild of its day.
    in_range = [(day, label) for day, label in date_ticks(start.date(), end.date(), 3)
                if start <= datetime.datetime.combine(day, datetime.time(), start.tzinfo)] or \
        [(start, start.date().isoformat())]
    return {
        "width": width, "height": height, "left": left, "right": right, "bottom": bottom,
        "path": path, "dots": dots, "yTicks": y_ticks,
        "xTicks": _x_labels(in_range, x, left, right, "line"),
        "first": points[0], "last": points[-1],
    }


def _monday(day):
    return day - datetime.timedelta(days=day.weekday())


def bands(days, kinds, daily=DAILY):
    """days, [{"date": "YYYY-MM-DD", kind: lookups}] in date order, as
    (weekly, [{"start": date, "days": n, kind: lookups per day}]): one band a
    day for up to daily days, else one a week, a day with no counts being 0."""
    counts = {datetime.date.fromisoformat(day["date"]): day for day in days}
    first, last = min(counts), max(counts)
    weekly = (last - first).days + 1 > daily
    found = []
    start = _monday(first) if weekly else first
    while start <= last:
        length = 7 if weekly else 1
        covered = [start + datetime.timedelta(days=n) for n in range(length)
                   if first <= start + datetime.timedelta(days=n) <= last]
        band = {"start": start, "days": len(covered)}
        for kind in kinds:
            band[kind] = sum(counts.get(day, {}).get(kind, 0) for day in covered) / len(covered)
        found.append(band)
        start += datetime.timedelta(days=length)
    return weekly, found


def columns(days, kinds, line_kind=None, width=640, height=280, daily=DAILY):
    """Columns of lookups per day, stacked by kinds in order, with line_kind as a
    line over them, or None for fewer than two days: {"width", "height",
    "left", "right", "bottom", "weekly", "columns": [[(kind, x, y, w, h)]],
    "line": path or None, "dots", "yTicks", "xTicks", "start", "end",
    "total", "average"}."""
    if len(days) < 2:
        return None
    weekly, found = bands(days, kinds + ((line_kind,) if line_kind else ()), daily)
    most = max(sum(band[kind] for kind in kinds) for band in found)
    ticks = nice_ticks(0, most)
    left, top, right, bottom = _frame("columns", width, height, ticks)
    y, y_ticks = _y_axis(ticks, top, bottom)
    origin = found[0]["start"]
    length = 7 if weekly else 1
    total_days = len(found) * length
    band_width = (right - left) / len(found)

    def x(day):
        return _round(left + ((day - origin).days + 0.5) / total_days * (right - left))
    drawn = []
    for n, band in enumerate(found):
        at = left + n * band_width + band_width * 0.1
        stacked = []
        base = 0
        for kind in kinds:
            if band[kind]:
                top_of = y(base + band[kind])
                stacked.append((kind, _round(at), top_of, _round(band_width * 0.8),
                                _round(y(base) - top_of)))
            base += band[kind]
        drawn.append(stacked)
    path = dots = None
    if line_kind and any(band[line_kind] for band in found):
        dots = [(_round(left + (n + 0.5) * band_width), y(band[line_kind]))
                for n, band in enumerate(found)]
        path = "M" + " L".join("%s,%s" % dot for dot in dots)
    dates = sorted(datetime.date.fromisoformat(day["date"]) for day in days)
    first, last = dates[0], dates[-1]
    total = sum(day.get(kind, 0) for day in days for kind in kinds)
    return {
        "width": width, "height": height, "left": left, "right": right, "bottom": bottom,
        "weekly": weekly, "columns": drawn, "line": path, "dots": dots or [],
        "yTicks": y_ticks,
        "xTicks": _x_labels(date_ticks(first, last, 5), x, left, right, "columns"),
        "start": first, "end": last, "total": total,
        "average": round(total / ((last - first).days + 1)),
    }
