"""Tiny inline-SVG chart helpers for the self-contained HTML report (no matplotlib needed).

Colors are CSS variables defined by the report page (light and dark), so every chart follows
the page theme. Marks carry <title> elements: hovering any line point, bar or dot shows its
values (the minimal hover layer; the report also has the full tables).
"""
from __future__ import annotations

import html
import math

W, H = 460, 260
M = {"l": 52, "r": 14, "t": 14, "b": 40}
LOG_TICKS = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000]


def esc(s):
    return html.escape(str(s), quote=True)


def _nice_ticks(lo, hi, n=5):
    if hi <= lo:
        hi = lo + 1
    span = hi - lo
    step = 10 ** math.floor(math.log10(span / n))
    for m in (1, 2, 5, 10):
        if span / (step * m) <= n:
            step *= m
            break
    t = math.ceil(lo / step) * step
    out = []
    while t <= hi + 1e-9:
        out.append(round(t, 10))
        t += step
    return out


class Axes:
    def __init__(self, xlo, xhi, ylo, yhi, xlog=False, w=W, h=H):
        self.xlog, self.w, self.h = xlog, w, h
        if xlog:
            xlo, xhi = max(xlo, 0.05), max(xhi, xlo * 1.5)
        self.xlo, self.xhi, self.ylo, self.yhi = xlo, xhi, ylo, yhi
        self.pw = w - M["l"] - M["r"]
        self.ph = h - M["t"] - M["b"]

    def x(self, v):
        if self.xlog:
            v = max(v, self.xlo)
            f = (math.log10(v) - math.log10(self.xlo)) / (math.log10(self.xhi) - math.log10(self.xlo))
        else:
            f = (v - self.xlo) / ((self.xhi - self.xlo) or 1)
        return M["l"] + f * self.pw

    def y(self, v):
        return M["t"] + (1 - (v - self.ylo) / ((self.yhi - self.ylo) or 1)) * self.ph

    def frame(self, xlabel, ylabel, yfmt="{:g}", xticks=None, yticks=None):
        out = []
        xt = xticks or ([t for t in LOG_TICKS if self.xlo <= t <= self.xhi] if self.xlog
                        else _nice_ticks(self.xlo, self.xhi))
        yt = yticks or _nice_ticks(self.ylo, self.yhi, 4)
        for t in yt:
            yy = self.y(t)
            out.append('<line class="grid" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (M["l"], self.w - M["r"], yy, yy))
            out.append('<text class="tick" x="%.1f" y="%.1f" text-anchor="end" dy="0.32em">%s</text>' % (M["l"] - 6, yy, yfmt.format(t)))
        for t in xt:
            xx = self.x(t)
            out.append('<line class="tickmark" x1="%.1f" x2="%.1f" y1="%.1f" y2="%.1f"/>' % (xx, xx, self.h - M["b"], self.h - M["b"] + 4))
            out.append('<text class="tick" x="%.1f" y="%.1f" text-anchor="middle">%s</text>' % (xx, self.h - M["b"] + 16, "{:g}".format(t)))
        out.append('<line class="axis" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/>' % (M["l"], self.w - M["r"], self.h - M["b"], self.h - M["b"]))
        out.append('<text class="label" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (M["l"] + self.pw / 2, self.h - 6, esc(xlabel)))
        out.append('<text class="label" transform="translate(12,%.1f) rotate(-90)" text-anchor="middle">%s</text>' % (M["t"] + self.ph / 2, esc(ylabel)))
        return "".join(out)

    def svg(self, body, title):
        return ('<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg>' % (self.w, self.h, esc(title), body))


def polyline(ax, pts, color, dash=None, name=""):
    if not pts:
        return ""
    d = " ".join("%.1f,%.1f" % (ax.x(x), ax.y(y)) for x, y in pts)
    return '<polyline fill="none" stroke="%s" stroke-width="2" %s points="%s"><title>%s</title></polyline>' % (
        color, 'stroke-dasharray="%s"' % dash if dash else "", d, esc(name))


def step_ecdf(ax, pts, color, name):
    """ECDF as a step line (x = latency, y = fraction)."""
    if not pts:
        return ""
    seq = []
    prev_y = 0.0
    for x, y in pts:
        seq.append((x, prev_y))
        seq.append((x, y))
        prev_y = y
    return polyline(ax, seq, color, name=name)


def vline(ax, x, cls="frameline", label=None):
    xx = ax.x(x)
    s = '<line class="%s" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/>' % (cls, xx, xx, M["t"], ax.h - M["b"])
    if label:
        s += '<text class="tick" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (xx, M["t"] - 3, esc(label))
    return s


def dots(ax, pts, color, name, r=2.2):
    return "".join('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" fill-opacity="0.55"><title>%s: %.3g, %.3g</title></circle>' % (
        ax.x(x), ax.y(y), r, color, esc(name), x, y) for x, y in pts)


def legend(items):
    return '<div class="legend">%s</div>' % "".join(
        '<span><i style="background:%s"></i>%s</span>' % (c, esc(n)) for n, c in items)


def hbar_stack(rows, segments, colors, xlabel, w=W):
    """rows: [(label, [v1, v2, ...])] stacked horizontally; 2px surface gap between segments."""
    h = 30 + 26 * len(rows) + 30
    xmax = max([sum(v for v in vals if v) for _, vals in rows] + [1])
    ax = Axes(0, xmax * 1.05, 0, 1, w=w, h=h)
    ax_l = 150
    out = []
    pw = w - ax_l - M["r"]
    sx = lambda v: ax_l + v / (xmax * 1.05) * pw
    for t in _nice_ticks(0, xmax * 1.05):
        out.append('<line class="grid" x1="%.1f" x2="%.1f" y1="10" y2="%d"/>' % (sx(t), sx(t), h - 30))
        out.append('<text class="tick" x="%.1f" y="%d" text-anchor="middle">%g</text>' % (sx(t), h - 16, t))
    for k, (label, vals) in enumerate(rows):
        y = 14 + 26 * k
        out.append('<text class="tick" x="%d" y="%d" text-anchor="end" dy="0.32em">%s</text>' % (ax_l - 6, y + 9, esc(label)))
        acc = 0.0
        for j, v in enumerate(vals):
            if not v or v <= 0:
                continue
            x0, x1 = sx(acc), sx(acc + v)
            out.append('<rect x="%.1f" y="%d" width="%.1f" height="18" rx="2" fill="%s" stroke="var(--surface)" stroke-width="2">'
                       '<title>%s / %s: %.2f ms</title></rect>' % (x0, y, max(0.5, x1 - x0), colors[j], esc(label), esc(segments[j]), v))
            acc += v
    out.append('<text class="label" x="%.1f" y="%d" text-anchor="middle">%s</text>' % (ax_l + pw / 2, h - 2, esc(xlabel)))
    return '<svg class="chart" viewBox="0 0 %d %d" role="img" aria-label="%s">%s</svg>' % (w, h, esc(xlabel), "".join(out))
