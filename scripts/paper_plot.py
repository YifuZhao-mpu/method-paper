#!/usr/bin/env python3
"""Paper-quality matplotlib helpers for method papers.

What it does
- Figure sizes come from the venue's column / text width, so the figure is
  inserted at 100% and its fonts print at their true size.
- Fonts match the body text: a Times-like serif with STIX math, plus a CJK
  serif fallback for Chinese labels. PDF fonts are embedded as TrueType
  (Type 42); SVG text stays editable.
- place_legend_safely() tries legend positions that do not cover any data and
  falls back to a legend above the axes.
- check_overlaps() reports text (legend, annotations, labels) that covers
  lines, markers, bars, bands or other text, crowded tick labels, and text
  cut off by the canvas.

Typical use
    import sys; sys.path.insert(0, "<skill>/scripts")
    import paper_plot as pp
    pp.setup(venue="cvpr")                      # ieee, icml, neurips, iclr, eccv, thesis
    fig, ax = pp.figure("single", aspect=0.62)  # "double" for full text width
    colors = pp.method_colors(["Ours", "A", "B"], ours="Ours")
    ax.plot(x, y, label="Ours", color=colors["Ours"])
    pp.place_legend_safely(ax)
    pp.save(fig, "figs/curve")                  # curve.pdf + curve.png, re-checked

Run `python paper_plot.py --demo OUTDIR` to render two example figures.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.collections import LineCollection, PathCollection, PolyCollection  # noqa: E402
from matplotlib.text import Annotation, Text  # noqa: E402
from matplotlib.transforms import Bbox  # noqa: E402

# Widths in inches. body_pt is the body-text size of the template; fig_pt is the
# default size for axis labels (ticks and legend use fig_pt - 1, never below 7).
VENUES = {
    "cvpr": dict(single=3.28, double=6.875, body_pt=10, fig_pt=9),
    "iccv": dict(single=3.28, double=6.875, body_pt=10, fig_pt=9),
    "eccv": dict(single=4.80, double=4.80, body_pt=10, fig_pt=9),
    "ieee": dict(single=3.5, double=7.16, body_pt=10, fig_pt=9),  # ICRA, IROS, RA-L, T-ITS ...
    "icml": dict(single=3.25, double=6.75, body_pt=10, fig_pt=9),
    "neurips": dict(single=5.50, double=5.50, body_pt=10, fig_pt=9),
    "iclr": dict(single=5.50, double=5.50, body_pt=10, fig_pt=9),
    # Chinese thesis on A4: body 小四 (12 pt), figure text 五号 (10.5 pt). Check your template.
    "thesis": dict(single=5.90, double=5.90, body_pt=12, fig_pt=10.5),
}

# TrueType Times-like fonts first: they embed as Type 42. Nimbus Roman / TeX Gyre Termes are usually
# OpenType-CFF files, which embed as CFF and make some PDF checkers complain.
TIMES_LIKE = ["Times New Roman", "Times", "Liberation Serif", "Nimbus Roman", "Nimbus Roman No9 L", "TeX Gyre Termes"]
LATIN_SERIF = TIMES_LIKE + ["STIXGeneral", "DejaVu Serif"]
CJK_SERIF = ["SimSun", "Songti SC", "STSong", "Noto Serif CJK SC", "Source Han Serif SC",
             "Noto Serif SC", "AR PL UMing CN"]

OURS_COLOR = "#D55E00"  # vermillion: reserved for the proposed method
PALETTE = ["#0072B2", "#009E73", "#E69F00", "#56B4E9", "#CC79A7", "#000000", "#999999", "#F0E442"]
MARKERS = ["o", "s", "^", "D", "v", "P", "X", "*"]

_STATE = {"venue": None, "single": 3.28, "double": 6.875, "fig_pt": 9.0, "body_pt": 10.0}


# --------------------------------------------------------------------------- setup
def available_fonts(candidates):
    installed = {f.name for f in font_manager.fontManager.ttflist}
    return [c for c in candidates if c in installed]


def font_file(name):
    try:
        return font_manager.findfont(font_manager.FontProperties(family=name), fallback_to_default=False)
    except Exception:  # pragma: no cover
        return ""


def _has_cjk(s):
    return any("\u3000" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef" for ch in str(s))


def setup(venue="cvpr", body_pt=None, fig_pt=None, single_in=None, double_in=None,
          font_family=None, cjk_family=None, verbose=True):
    """Apply venue sizes and paper fonts to matplotlib.rcParams. Returns the state dict."""
    if venue not in VENUES:
        raise ValueError(f"unknown venue {venue!r}; choose from {sorted(VENUES)}")
    v = VENUES[venue]
    body = float(body_pt or v["body_pt"])
    fpt = float(fig_pt or v.get("fig_pt", body - 1))
    small = max(7.0, fpt - 1.0)
    latin = available_fonts(font_family or LATIN_SERIF)
    cjk = available_fonts(cjk_family or CJK_SERIF)
    if not font_family and not available_fonts(TIMES_LIKE):
        print("[paper_plot] 警告：没有找到 Times 系字体（Times New Roman、Liberation Serif 等），退回 STIX，"
              "与正文字体会有差别；安装 Times New Roman 或用 font_family 指定。")
    if latin and font_file(latin[0]).lower().endswith(".otf"):
        print(f"[paper_plot] 提示：{latin[0]} 是 OpenType-CFF 字体，PDF 里会以 CFF 形式嵌入，"
              "部分 PDF 检查（如 IEEE PDF eXpress）会报字体类型警告；有 Times New Roman 时优先用它。")
    families = latin + [c for c in cjk if c not in latin] or ["serif"]
    _STATE["cjk"] = bool(cjk)
    matplotlib.rcParams.update({
        "font.family": families,
        "font.size": fpt,
        "axes.labelsize": fpt,
        "axes.titlesize": fpt,
        "xtick.labelsize": small,
        "ytick.labelsize": small,
        "legend.fontsize": small,
        "legend.title_fontsize": small,
        "legend.frameon": False,
        "legend.handlelength": 1.6,
        "legend.borderaxespad": 0.4,
        "axes.linewidth": 0.6,
        "axes.unicode_minus": False,
        "lines.linewidth": 1.2,
        "lines.markersize": 3.5,
        "patch.linewidth": 0.5,
        "xtick.major.width": 0.6,
        "ytick.major.width": 0.6,
        "xtick.major.size": 2.5,
        "ytick.major.size": 2.5,
        "xtick.direction": "in",
        "ytick.direction": "in",
        "grid.linewidth": 0.4,
        "grid.alpha": 0.5,
        "mathtext.fontset": "stix",
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "svg.fonttype": "none",
        "savefig.dpi": 300,
        "figure.dpi": 150,
        "savefig.bbox": None,
        "savefig.pad_inches": 0.0,
    })
    _STATE.update(venue=venue, single=float(single_in or v["single"]),
                  double=float(double_in or v["double"]), fig_pt=fpt, body_pt=body)
    if verbose:
        print(f"[paper_plot] venue={venue} 单栏 {_STATE['single']} in / 通栏 {_STATE['double']} in；"
              f"正文 {body:g} pt，图中标签 {fpt:g} pt，刻度与图例 {small:g} pt；字体 {families[:3]}")
    return dict(_STATE)


def width_in(width="single"):
    if width == "single":
        return _STATE["single"]
    if width == "double":
        return _STATE["double"]
    return float(width)


def figure(width="single", aspect=0.62, height=None, nrows=1, ncols=1, **kwargs):
    """plt.subplots at the exact print size with constrained layout."""
    w = width_in(width)
    h = float(height) if height else w * aspect
    fig, ax = plt.subplots(nrows, ncols, figsize=(w, h), layout="constrained", **kwargs)
    fig.get_layout_engine().set(w_pad=2 / 72, h_pad=2 / 72, wspace=0.02, hspace=0.02)
    return fig, ax


def method_colors(methods, ours=None):
    """Stable colors per method. The proposed method always gets the vermillion highlight."""
    out, k = {}, 0
    for m in methods:
        if ours is not None and m == ours:
            out[m] = OURS_COLOR
        else:
            out[m] = PALETTE[k % len(PALETTE)]
            k += 1
    return out


def method_markers(methods):
    return {m: MARKERS[i % len(MARKERS)] for i, m in enumerate(methods)}


# ---------------------------------------------------------------- overlap checking
def _renderer(fig):
    fig.canvas.draw()
    return fig.canvas.get_renderer()


def _short(s, n=24):
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _text_bbox(t, r):
    # Annotation.get_window_extent includes the arrow; only the text matters here.
    return Text.get_window_extent(t, r) if isinstance(t, Annotation) else t.get_window_extent(r)


def _densify(verts, max_samples=20000):
    verts = verts[np.isfinite(verts).all(axis=1)]
    if len(verts) < 2:
        return verts
    seg = np.diff(verts, axis=0)
    lens = np.hypot(seg[:, 0], seg[:, 1])
    step = max(1.5, lens.sum() / max_samples)
    n = np.maximum(1, np.ceil(lens / step).astype(int))
    idx = np.repeat(np.arange(len(seg)), n)
    t = np.concatenate([np.arange(k) / k for k in n])
    return np.vstack([verts[idx] + seg[idx] * t[:, None], verts[-1:]])


def _inside_axes(P, ax):
    bb = ax.bbox
    m = (P[:, 0] >= bb.x0) & (P[:, 0] <= bb.x1) & (P[:, 1] >= bb.y0) & (P[:, 1] <= bb.y1)
    return P[m]


def _data_items(ax, r, fig):
    """Display-space geometry of everything in the axes that text must not cover."""
    items = []
    px_per_pt = fig.dpi / 72.0
    for i, line in enumerate(ax.lines):
        if not line.get_visible():
            continue
        verts = line.get_transform().transform(line.get_path().vertices)
        ls = line.get_linestyle()
        P = _densify(verts) if ls not in ("None", "none", "", " ") else verts[np.isfinite(verts).all(axis=1)]
        P = _inside_axes(P, ax)
        marker = line.get_marker()
        rad = 0.0 if marker in (None, "None", "none", "", " ") else line.get_markersize() * px_per_pt / 2
        rad = max(rad, line.get_linewidth() * px_per_pt / 2)
        label = line.get_label()
        name = f"曲线“{_short(label)}”" if label and not label.startswith("_") else f"第 {i + 1} 条线"
        items.append((name, "points", (P, rad)))
    for i, coll in enumerate(ax.collections):
        if not coll.get_visible():
            continue
        label = coll.get_label()
        name = f"“{_short(label)}”" if label and not label.startswith("_") else f"图元 {i + 1}"
        if isinstance(coll, PathCollection) and len(coll.get_offsets()):
            P = coll.get_offset_transform().transform(np.asarray(coll.get_offsets(), float))
            sizes = coll.get_sizes()
            rad = (np.sqrt(sizes.max()) / 2 if len(sizes) else 3.0) * px_per_pt
            items.append((f"散点{name}", "points", (_inside_axes(P[np.isfinite(P).all(axis=1)], ax), rad)))
        elif isinstance(coll, LineCollection):
            tr = coll.get_transform()
            segs = [_densify(tr.transform(np.asarray(s, float))) for s in coll.get_segments() if len(s)]
            if segs:
                lw = np.max(coll.get_linewidths()) if len(coll.get_linewidths()) else 1.0
                items.append((f"线段{name}", "points", (_inside_axes(np.vstack(segs), ax), lw * px_per_pt / 2)))
        elif isinstance(coll, PolyCollection):
            tr = coll.get_transform()
            paths = [tr.transform_path(p) for p in coll.get_paths()]
            if paths:
                items.append((f"填充区域{name}", "paths", paths))
    for i, p in enumerate(ax.patches):
        if p is ax.patch or not p.get_visible():
            continue
        bb = p.get_window_extent(r)
        if bb.width > 0 and bb.height > 0:
            items.append((f"柱或色块 {i + 1}", "bbox", bb))
    return items


def _hits(bb, items, pad=1.0):
    found = []
    for name, kind, payload in items:
        if kind == "points":
            P, rad = payload
            if not len(P):
                continue
            m = ((P[:, 0] > bb.x0 - rad - pad) & (P[:, 0] < bb.x1 + rad + pad)
                 & (P[:, 1] > bb.y0 - rad - pad) & (P[:, 1] < bb.y1 + rad + pad))
            if m.any():
                found.append(name)
        elif kind == "bbox":
            ib = Bbox.intersection(bb, payload)
            if ib is not None and ib.width * ib.height > 2.0:
                found.append(name)
        elif kind == "paths":
            gx, gy = np.meshgrid(np.linspace(bb.x0, bb.x1, 7), np.linspace(bb.y0, bb.y1, 5))
            grid = np.column_stack([gx.ravel(), gy.ravel()])
            if any(p.contains_points(grid).any() for p in payload):
                found.append(name)
    return found


def _text_items(ax, r):
    items = []
    leg = ax.get_legend()
    if leg is not None and leg.get_visible():
        items.append(("图例", leg.get_window_extent(r)))
    for t in ax.texts:
        if t.get_visible() and str(t.get_text()).strip():
            items.append((f"文字“{_short(t.get_text())}”", _text_bbox(t, r)))
    return items


def _drawn_ticklabels(axis):
    """Tick labels that are actually drawn (matplotlib keeps labels for ticks outside the view)."""
    lo, hi = sorted(axis.get_view_interval())
    tol = (hi - lo) * 1e-6 if hi > lo else 1e-12
    out = []
    for ticks in (axis.get_major_ticks(), axis.get_minor_ticks()):
        for tick in ticks:
            loc = tick.get_loc()
            if loc is None or not (lo - tol <= loc <= hi + tol):
                continue
            for lab in (tick.label1, tick.label2):
                if lab.get_visible() and str(lab.get_text()).strip():
                    out.append(lab)
    return out


def _tick_crowding(ax, r, ai):
    issues = []
    for axis_name, axis in (("x", ax.xaxis), ("y", ax.yaxis)):
        boxes = [t.get_window_extent(r) for t in _drawn_ticklabels(axis)]
        key = (lambda b: b.x0) if axis_name == "x" else (lambda b: b.y0)
        boxes.sort(key=key)
        for a, b in zip(boxes, boxes[1:]):
            ib = Bbox.intersection(a, b)
            if ib is not None and ib.width * ib.height > 1.0:
                issues.append(f"[子图 {ai}] {axis_name} 轴刻度文字互相重叠：减少刻度或旋转标签")
                break
    return issues


def check_overlaps(fig, pad_px=1.0):
    """Return a list of human-readable problems; an empty list means the figure is clean."""
    r = _renderer(fig)
    issues, texts = [], []
    axes = [ax for ax in fig.axes if ax.get_visible()]
    all_data = []
    for ai, ax in enumerate(axes):
        data = _data_items(ax, r, fig)
        all_data.extend(data)
        for name, bb in _text_items(ax, r):
            for h in _hits(bb, data, pad_px):
                issues.append(f"[子图 {ai}] {name}遮挡了{h}")
            texts.append((f"[子图 {ai}] {name}", bb))
        issues += _tick_crowding(ax, r, ai)
    for leg in fig.legends:
        bb = leg.get_window_extent(r)
        for h in _hits(bb, all_data, pad_px):
            issues.append(f"[整图] 图例遮挡了{h}")
        texts.append(("[整图] 图例", bb))
    for t in fig.texts:
        if t.get_visible() and str(t.get_text()).strip():
            bb = t.get_window_extent(r)
            for h in _hits(bb, all_data, pad_px):
                issues.append(f"[整图] 文字“{_short(t.get_text())}”遮挡了{h}")
            texts.append((f"[整图] 文字“{_short(t.get_text())}”", bb))
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            ib = Bbox.intersection(texts[i][1], texts[j][1])
            if ib is not None and ib.width * ib.height > 1.0:
                issues.append(f"{texts[i][0]} 与 {texts[j][0]} 互相重叠")
    if not _STATE.get("cjk", True) and any(_has_cjk(t.get_text()) for t in fig.findobj(Text)):
        issues.append("图中有中文，但没有可用的中文字体：中文会显示成方框；安装 SimSun 或 Noto Serif CJK SC 后重新 setup")
    fb = fig.bbox
    all_ticks, drawn_ticks = set(), []
    for ax in axes:
        for axis in (ax.xaxis, ax.yaxis):
            for tick in list(axis.get_major_ticks()) + list(axis.get_minor_ticks()):
                all_ticks.update({id(tick.label1), id(tick.label2)})
            drawn_ticks.extend(_drawn_ticklabels(axis))
    candidates = [t for t in fig.findobj(Text) if id(t) not in all_ticks] + drawn_ticks
    for t in candidates:
        if not t.get_visible() or not str(t.get_text()).strip() or t.figure is None:
            continue
        try:
            bb = _text_bbox(t, r)
        except Exception:  # pragma: no cover - exotic artists
            continue
        if bb.x0 < fb.x0 - 0.5 or bb.y0 < fb.y0 - 0.5 or bb.x1 > fb.x1 + 0.5 or bb.y1 > fb.y1 + 0.5:
            issues.append(f"文字“{_short(t.get_text())}”超出画布，会被裁掉")
    return list(dict.fromkeys(issues))


def place_legend_safely(ax, locs=None, outside=True, **kwargs):
    """Put the legend where it covers no data; otherwise above the axes. Returns the Legend."""
    handles, labels = ax.get_legend_handles_labels()
    if not handles:
        return None
    fig = ax.figure
    r = _renderer(fig)
    data = _data_items(ax, r, fig)
    others = [bb for name, bb in _text_items(ax, r) if name != "图例"]
    locs = locs or ["upper left", "upper right", "lower right", "lower left", "upper center",
                    "lower center", "center right", "center left", "center"]
    for loc in locs:
        leg = ax.legend(handles, labels, loc=loc, **kwargs)
        r = _renderer(fig)
        bb = leg.get_window_extent(r)
        clash = any((ib := Bbox.intersection(bb, o)) is not None and ib.width * ib.height > 1 for o in others)
        if not _hits(bb, data, 1.5) and not clash:
            return leg
        leg.remove()
    if not outside:
        return ax.legend(handles, labels, loc="best", **kwargs)
    kwargs.pop("ncol", None)
    for ncol in sorted({min(len(labels), 4), min(len(labels), 3), min(len(labels), 2), 1}, reverse=True):
        leg = ax.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=ncol,
                        borderaxespad=0.0, columnspacing=1.0, **kwargs)
        r = _renderer(fig)
        bb, fb = leg.get_window_extent(r), fig.bbox
        if bb.x0 >= fb.x0 - 0.5 and bb.x1 <= fb.x1 + 0.5:
            return leg
        leg.remove()
    return ax.legend(handles, labels, loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=1, **kwargs)


def font_sizes(fig):
    fig.canvas.draw()
    texts = [t for t in fig.findobj(Text) if t.get_visible() and str(t.get_text()).strip()]
    tick_ids = set()
    drawn = []
    for ax in fig.axes:
        for axis in (ax.xaxis, ax.yaxis):
            for tick in list(axis.get_major_ticks()) + list(axis.get_minor_ticks()):
                tick_ids.update({id(tick.label1), id(tick.label2)})
            drawn.extend(_drawn_ticklabels(axis))
    texts = [t for t in texts if id(t) not in tick_ids] + drawn
    return sorted({round(float(t.get_fontsize()), 2) for t in texts})


def save(fig, stem, formats=("pdf", "png"), dpi=300, check=True, strict=False):
    """Save at the exact figure size (no tight bbox, so fonts stay at print size)."""
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    issues = check_overlaps(fig) if check else []
    for fmt in formats:
        out = Path(f"{stem}.{fmt}")
        fig.savefig(out, dpi=dpi if fmt.lower() in ("png", "jpg", "jpeg", "tif", "tiff") else None)
    w, h = fig.get_size_inches()
    print(f"[paper_plot] 已保存 {stem}.{{{','.join(formats)}}}：{w:.2f}×{h:.2f} in，"
          f"字号 {font_sizes(fig)} pt")
    if issues:
        print("[paper_plot] 遮挡检查未通过：")
        for s in issues:
            print("  - " + s)
    else:
        print("[paper_plot] 遮挡检查通过")
    if strict and issues:
        raise RuntimeError("overlap check failed: " + "; ".join(issues))
    return issues


# ------------------------------------------------------------------------- demo
def _demo(outdir, venue):
    setup(venue=venue)
    out = Path(outdir)
    rng = np.random.default_rng(0)
    methods = ["Ours", "Baseline-A", "Baseline-B", "Baseline-C"]
    colors, marks = method_colors(methods, ours="Ours"), method_markers(methods)
    x = np.arange(0, 101, 10)
    fig, ax = figure("single", aspect=0.68)
    for k, m in enumerate(methods):
        y = 30 + 0.45 * x * (1.0 - 0.15 * k) + rng.normal(0, 0.6, x.size)
        ax.plot(x, y, marker=marks[m], color=colors[m], label=m, lw=1.6 if m == "Ours" else 1.0, zorder=3 if m == "Ours" else 2)
        if m == "Ours":
            ax.fill_between(x, y - 1.2, y + 1.2, color=colors[m], alpha=0.15, lw=0)
    ax.set_xlabel("Training data (%)")
    ax.set_ylabel("mAP (%)")
    ax.grid(True, axis="y")
    place_legend_safely(ax)
    save(fig, out / "demo_curve")

    fig, ax = figure("single", aspect=0.6)
    vals = [62.1, 58.4, 55.0, 51.7]
    bars = ax.bar(range(4), vals, color=[colors[m] for m in methods], width=0.62)
    ax.bar_label(bars, fmt="%.1f", padding=1.5, fontsize=matplotlib.rcParams["legend.fontsize"])
    ax.set_xticks(range(4), methods)
    ax.set_ylabel("Success rate (%)")
    ax.set_ylim(0, 72)
    save(fig, out / "demo_bars")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--demo", metavar="OUTDIR", help="render example figures into OUTDIR")
    ap.add_argument("--venue", default="cvpr", choices=sorted(VENUES))
    a = ap.parse_args()
    if a.demo:
        _demo(a.demo, a.venue)
    else:
        ap.print_help()
