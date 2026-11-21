#!/usr/bin/env python3
"""Qualitative result figures for 3D detection papers.

Draws camera views with projected 3D boxes and bird's-eye views (BEV) with points, occupied pillars and
boxes. The figure is laid out at the venue's print width with fonts matched to the body text (paper_plot.py);
box labels go where they cover no points, boxes or other text, and the overlap check runs on save.

    python qualfig.py spec.json --out figs/qual        # figs/qual.pdf and figs/qual.png
    python qualfig.py --demo OUTDIR                     # synthetic data and three example figures

Spec (JSON)
    {
      "venue": "cvpr",                 # a paper_plot venue, or {"width_in": 5.39, "body_pt": 12, "fig_pt": 10}
      "width": "double",               # "single", "double" or a number in inches
      "groups": {"in": {"label": "SACR only (in)"}},   # optional overrides of the box styles below
      "point_label": "LiDAR points",   # legend entry for points ("" hides it)
      "rows": [[panel, panel, ...], ...]   # panels in a row share one height; widths follow their aspect
    }
    BEV panel:   {"type": "bev", "points": "pts.npy", "range": [xmin, xmax, ymin, ymax],
                  "axes": "xy" | "forward_up", "boxes": [...], "grid_m": 10, "title": "...",
                  "pillars": {"origin": [x0, y0], "size": [0.16, 0.16]}, "point_size": 0.6,
                  "label_mode": "auto" | "inside" | "key"}   # key: box labels listed under the panel
                                                               # (auto picks key for dense LiDAR sweeps)
    Image panel: {"type": "image", "image": "img.jpg", "K": 3x3 or "P": 3x4, "T": 4x4 (camera <- box frame),
                  "boxes": [...], "dets2d": [[u1, v1, u2, v2], ...], "crop": [u0, v0, u1, v1] | "auto" | null,
                  "points": "pts.npy" (optional, coloured by depth), "title": "..."}
    Box:         {"box": [x, y, z_center, l, w, h, yaw], "group": "gt", "text": "optional BEV label"}
                 l runs along yaw and yaw turns about +z (the OpenPCDet boxes_lidar convention).
Paths in a spec are relative to the spec file.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paper_plot as pp  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.collections import PolyCollection  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.transforms import Bbox  # noqa: E402

# Okabe-Ito colours; the order of this dict is the legend order.
GROUPS = {
    "gt": dict(color="#000000", ls=(0, (3, 2)), lw=1.0, label="GT", z=4),
    "both": dict(color="#8C8C8C", ls="-", lw=1.4, label="kept by both", z=5),
    "in": dict(color="#0072B2", ls="-", lw=1.6, label="SACR only (in)", z=6),
    "out": dict(color="#D55E00", ls="-", lw=1.6, label="score gate only (out)", z=6),
    "tp": dict(color="#0072B2", ls="-", lw=1.4, label="matched (y = 1)", z=5),
    "fp": dict(color="#D55E00", ls="-", lw=1.4, label="unmatched (y = 0)", z=6),
    "native": dict(color="#56B4E9", ls="-", lw=2.6, label="Native", z=5),
    "sdfp": dict(color="#D55E00", ls="-", lw=1.0, label="SDFP", z=6),
    "pred": dict(color="#0072B2", ls="-", lw=1.4, label="prediction", z=5),
}
DET2D = dict(color="#009E73", ls=(0, (2, 1.5)), lw=0.9, label="2D detection")
PILLAR = dict(color="#CC79A7", alpha=0.45, label="occupied pillar (enlarged)")
EDGES = [(0, 1), (1, 2), (2, 3), (3, 0), (4, 5), (5, 6), (6, 7), (7, 4), (0, 4), (1, 5), (2, 6), (3, 7)]
GAP_W, GAP_H, MARGIN = 0.06, 0.05, 0.02   # inches


# ------------------------------------------------------------------ geometry
def box_corners(b):
    """8 x 3 corners: bottom face (front-left, front-right, rear-right, rear-left), then the top face."""
    x, y, z, l, w, h, yaw = [float(v) for v in b[:7]]
    c, s = math.cos(yaw), math.sin(yaw)
    dx = np.array([1, 1, -1, -1]) * l / 2
    dy = np.array([1, -1, -1, 1]) * w / 2
    xs, ys = x + c * dx - s * dy, y + s * dx + c * dy
    bottom = np.c_[xs, ys, np.full(4, z - h / 2)]
    top = np.c_[xs, ys, np.full(4, z + h / 2)]
    return np.vstack([bottom, top])


def project(pts, T, K=None, P=None):
    """Return pixel coordinates (N x 2) and depth (N) of points given in the box/point frame."""
    X = np.asarray(T, float) @ np.c_[pts, np.ones(len(pts))].T
    if P is not None:
        uvw = np.asarray(P, float) @ X
    else:
        uvw = np.asarray(K, float) @ X[:3]
    depth = uvw[2]
    with np.errstate(divide="ignore", invalid="ignore"):
        uv = (uvw[:2] / depth).T
    return uv, depth


def bev_map(xy, mode):
    xy = np.asarray(xy, float)
    return np.c_[-xy[:, 1], xy[:, 0]] if mode == "forward_up" else xy


def bev_limits(rng, mode):
    xmin, xmax, ymin, ymax = rng
    return ((-ymax, -ymin), (xmin, xmax)) if mode == "forward_up" else ((xmin, xmax), (ymin, ymax))


def load_points(path, base):
    if not path:
        return None
    p = (base / path) if not Path(path).is_absolute() else Path(path)
    arr = np.load(p) if p.suffix == ".npy" else np.fromfile(p, dtype=np.float32)
    return arr.reshape(len(arr), -1) if arr.ndim == 2 else arr


# ------------------------------------------------------------------ panel preparation
def image_panel_crop(panel, img_wh):
    W, H = img_wh
    crop = panel.get("crop")
    if crop and crop != "auto":
        return [float(v) for v in crop]
    if crop != "auto":
        return [0.0, 0.0, float(W), float(H)]
    boxes2d = []
    for b in panel.get("boxes", []):
        uv, d = project(box_corners(b["box"]), panel["T"], panel.get("K"), panel.get("P"))
        ok = d > 0.1
        if ok.sum() >= 4:
            boxes2d.append([uv[ok, 0].min(), uv[ok, 1].min(), uv[ok, 0].max(), uv[ok, 1].max()])
    boxes2d += [list(map(float, r)) for r in panel.get("dets2d", [])]
    if not boxes2d:
        return [0.0, 0.0, float(W), float(H)]
    a = np.array(boxes2d)
    u0, v0, u1, v1 = a[:, 0].min(), a[:, 1].min(), a[:, 2].max(), a[:, 3].max()
    m = 0.6 * max(u1 - u0, v1 - v0) + 40
    u0, v0, u1, v1 = u0 - m, v0 - m, u1 + m, v1 + m
    w, h = u1 - u0, v1 - v0
    if w < 1.2 * h:            # keep the crop between 1.2:1 and 2:1
        u0, u1 = (u0 + u1) / 2 - 0.6 * h, (u0 + u1) / 2 + 0.6 * h
    elif w > 2.0 * h:
        v0, v1 = (v0 + v1) / 2 - w / 4, (v0 + v1) / 2 + w / 4
    du = max(0.0, -u0) - max(0.0, u1 - W)
    dv = max(0.0, -v0) - max(0.0, v1 - H)
    u0, u1, v0, v1 = u0 + du, u1 + du, v0 + dv, v1 + dv
    return [max(0.0, u0), max(0.0, v0), min(float(W), u1), min(float(H), v1)]


def prepare(panel, base):
    """Load what the panel needs and return its natural aspect (height / width)."""
    if panel["type"] == "bev":
        mode = panel.get("axes", "xy")
        (x0, x1), (y0, y1) = bev_limits(panel["range"], mode)
        panel["_pts"] = pts = load_points(panel.get("points"), base)
        n = 0
        if pts is not None and len(pts):
            m = bev_map(pts[:, :2], mode)
            n = int(((m[:, 0] >= x0) & (m[:, 0] <= x1) & (m[:, 1] >= y0) & (m[:, 1] <= y1)).sum())
        lm = panel.get("label_mode", "auto")
        panel["_key"] = lm == "key" or (lm == "auto" and n > 800)  # LiDAR sweeps leave no free space inside
        return (y1 - y0) / (x1 - x0)
    from PIL import Image
    p = Path(panel["image"])
    img = Image.open(base / p if not p.is_absolute() else p).convert("RGB")
    panel["_img"] = img
    panel["_crop"] = image_panel_crop(panel, img.size)
    panel["_pts"] = load_points(panel.get("points"), base)
    u0, v0, u1, v1 = panel["_crop"]
    return (v1 - v0) / (u1 - u0)


# ------------------------------------------------------------------ drawing
def style(groups, name):
    s = dict(GROUPS.get(name, GROUPS["pred"]))
    s.update(groups.get(name, {}))
    return s


def draw_bev(ax, panel, groups, used):
    mode = panel.get("axes", "xy")
    (x0, x1), (y0, y1) = bev_limits(panel["range"], mode)
    pts = panel.get("_pts")
    if panel.get("pillars") and pts is not None:
        pil = panel["pillars"]
        ox, oy = pil["origin"]
        dx, dy = pil["size"]
        cells = np.unique(np.c_[np.floor((pts[:, 0] - ox) / dx), np.floor((pts[:, 1] - oy) / dy)].astype(int), axis=0)
        centers = bev_map(np.c_[ox + (cells[:, 0] + 0.5) * dx, oy + (cells[:, 1] + 0.5) * dy], mode)
        ax.scatter(centers[:, 0], centers[:, 1], s=panel.get("pillar_size", 9), marker="s", c=PILLAR["color"],
                   alpha=PILLAR["alpha"], linewidths=0, zorder=1, rasterized=True)
        used.add("_pillar")
        if "grid_shape" in pil:
            total = int(np.prod(pil["grid_shape"]))
            panel.setdefault("_stats", {})["pillars"] = f"{len(cells)} / {total:,} pillars ({100 * len(cells) / total:.2f}%)"
    if pts is not None and len(pts):
        m = bev_map(pts[:, :2], mode)
        keep = (m[:, 0] >= x0) & (m[:, 0] <= x1) & (m[:, 1] >= y0) & (m[:, 1] <= y1)
        m = m[keep]
        cap = panel.get("max_points", 12000)
        if len(m) > cap:  # thin dense LiDAR sweeps so boxes and labels stay readable
            m = m[np.random.default_rng(0).choice(len(m), cap, replace=False)]
        ax.scatter(m[:, 0], m[:, 1], s=panel.get("point_size", 0.6), c=panel.get("point_color", "#6E6E6E"),
                   linewidths=0, zorder=2, rasterized=True)
        used.add("_points")
    for b in panel.get("boxes", []):
        s = style(groups, b.get("group", "pred"))
        c = box_corners(b["box"])
        poly = bev_map(np.vstack([c[:4], c[:1]])[:, :2], mode)
        ax.plot(poly[:, 0], poly[:, 1], color=s["color"], ls=s["ls"], lw=s["lw"], zorder=s["z"], solid_capstyle="round")
        head = bev_map(np.array([[b["box"][0], b["box"][1]], (c[0, :2] + c[1, :2]) / 2]), mode)
        ax.plot(head[:, 0], head[:, 1], color=s["color"], ls="-", lw=min(s["lw"], 1.2), zorder=s["z"])
        used.add(b.get("group", "pred"))
    g = panel.get("grid_m", 10)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    if g:
        ax.set_xticks(np.arange(math.ceil(x0 / g) * g, x1 + 1e-6, g))
        ax.set_yticks(np.arange(math.ceil(y0 / g) * g, y1 + 1e-6, g))
        ax.grid(True, color="#E3E3E3", lw=0.4, zorder=0)
    ax.tick_params(length=0, labelbottom=False, labelleft=False)
    for sp in ax.spines.values():
        sp.set_color("#9A9A9A")
        sp.set_linewidth(0.5)


def draw_image(ax, panel, groups, used, width_in, img_dpi):
    from PIL import Image
    u0, v0, u1, v1 = panel["_crop"]
    img = panel["_img"].crop((int(u0), int(v0), int(math.ceil(u1)), int(math.ceil(v1))))
    tw = int(math.ceil(width_in * img_dpi))
    if img.size[0] > tw:  # store no more pixels than the print needs
        img = img.resize((tw, max(1, round(img.size[1] * tw / img.size[0]))), Image.LANCZOS)
    ax.imshow(np.asarray(img), extent=[u0, u1, v1, v0], interpolation="antialiased", zorder=0)
    pts = panel.get("_pts")
    if pts is not None and len(pts):
        uv, d = project(pts[:, :3], panel["T"], panel.get("K"), panel.get("P"))
        m = (d > 0.5) & (uv[:, 0] >= u0) & (uv[:, 0] <= u1) & (uv[:, 1] >= v0) & (uv[:, 1] <= v1)
        ax.scatter(uv[m, 0], uv[m, 1], s=panel.get("point_size", 0.5), c=d[m], cmap="viridis_r",
                   vmin=0, vmax=panel.get("depth_max", 60), linewidths=0, zorder=1, rasterized=True)
    for r in panel.get("dets2d", []):
        a, b_, c, d_ = map(float, r)
        ax.plot([a, c, c, a, a], [b_, b_, d_, d_, b_], color=DET2D["color"], ls=DET2D["ls"], lw=DET2D["lw"], zorder=3)
        used.add("_det2d")
    for b in panel.get("boxes", []):
        s = style(groups, b.get("group", "pred"))
        uv, d = project(box_corners(b["box"]), panel["T"], panel.get("K"), panel.get("P"))
        if (d > 0.1).all():
            for i, j in EDGES:
                ax.plot(uv[[i, j], 0], uv[[i, j], 1], color=s["color"], ls=s["ls"], lw=s["lw"] * 0.85, zorder=s["z"])
        elif (d > 0.1).sum() >= 4:  # partly behind the camera: the clipped rectangle, as used for camera support
            ok = d > 0.1
            a, b_ = uv[ok].min(0)
            c, d_ = uv[ok].max(0)
            a, c = np.clip([a, c], u0, u1)
            b_, d_ = np.clip([b_, d_], v0, v1)
            ax.plot([a, c, c, a, a], [b_, b_, d_, d_, b_], color=s["color"], ls=s["ls"], lw=s["lw"] * 0.85, zorder=s["z"])
        else:
            continue
        used.add(b.get("group", "pred"))
    ax.set_xlim(u0, u1)
    ax.set_ylim(v1, v0)
    ax.set_xticks([])
    ax.set_yticks([])
    for sp in ax.spines.values():
        sp.set_color("#9A9A9A")
        sp.set_linewidth(0.5)


def place_labels(fig, ax, panel, groups, fontsize):
    """Put each BEV box label where it covers no data or other text; draw a leader line when it sits away."""
    todo = [b for b in panel.get("boxes", []) if b.get("text")]
    if not todo or panel.get("_key"):
        return []
    mode = panel.get("axes", "xy")
    missed, placed = [], []
    r = pp._renderer(fig)
    for b in todo:
        data = pp._data_items(ax, r, fig)
        c = box_corners(b["box"])
        corners = ax.transData.transform(bev_map(c[:4, :2], mode))
        center = corners.mean(0)
        t = ax.text(0, 0, b["text"], fontsize=fontsize, color="#1A1A1A", zorder=10, linespacing=1.05)
        found = None
        for rad in (4, 10, 18, 28, 40, 55, 75, 100):
            for k in range(12):
                ang = math.radians(45 + 30 * k)
                ux, uy = math.cos(ang), math.sin(ang)
                ext = np.abs(corners - center).max(0)
                pos = center + np.array([ux * (ext[0] + rad), uy * (ext[1] + rad)])
                t.set_ha("left" if ux > 0.2 else "right" if ux < -0.2 else "center")
                t.set_va("bottom" if uy > 0.2 else "top" if uy < -0.2 else "center")
                t.set_position(ax.transData.inverted().transform(pos))
                bb = t.get_window_extent(r)
                inside = ax.bbox
                if bb.x0 < inside.x0 + 1 or bb.x1 > inside.x1 - 1 or bb.y0 < inside.y0 + 1 or bb.y1 > inside.y1 - 1:
                    continue
                if pp._hits(bb, data, 1.5):
                    continue
                if any((ib := Bbox.intersection(bb, o)) is not None and ib.width * ib.height > 0.5 for o in placed):
                    continue
                found = (bb, rad)
                break
            if found:
                break
        if not found:
            t.remove()
            missed.append(b["text"])
            continue
        bb, rad = found
        placed.append(bb)
        if rad > 10:  # leader line from the nearest corner to the nearest point of the label box
            tgt = np.array([min(max(center[0], bb.x0), bb.x1), min(max(center[1], bb.y0), bb.y1)])
            src = corners[np.argmin(((corners - tgt) ** 2).sum(1))]
            d = tgt - src
            tgt = tgt - d / max(np.hypot(*d), 1e-6) * 4.0  # stop short so the line does not touch the text
            seg = ax.transData.inverted().transform(np.vstack([src, tgt]))
            ax.plot(seg[:, 0], seg[:, 1], color=style(groups, b.get("group", "pred"))["color"], lw=0.5, zorder=9)
        r = pp._renderer(fig)
    return missed


def key_lines(panel, width_in, fontsize):
    """Labels of a dense BEV panel, written under the panel in the colour of their box."""
    if panel.get("type") != "bev" or not panel.get("_key"):
        return []
    per_line = max(12, int(width_in * 72 / (0.45 * fontsize)))
    out = []
    for b in panel.get("boxes", []):
        if not b.get("text"):
            continue
        words, line = ", ".join(s.strip() for s in str(b["text"]).split("\n")).split(", "), ""
        for w in words:
            cand = f"{line}, {w}" if line else w
            if len(cand) > per_line and line:
                out.append((line + ",", b.get("group", "pred")))
                line = w
            else:
                line = cand
        out.append((line, b.get("group", "pred")))
    return out


def draw_key(ax, lines, groups, fontsize):
    step = fontsize * 1.25 / 72 / ax.figure.get_size_inches()[1] / ax.get_position().height
    for i, (s, g) in enumerate(lines):
        ax.text(0.0, -0.035 - i * step, s, transform=ax.transAxes, ha="left", va="top", fontsize=fontsize,
                color=style(groups, g)["color"], clip_on=False)


# ------------------------------------------------------------------ layout
def legend_entries(used, groups, spec):
    h, lab = [], []
    for name in GROUPS:
        if name in used:
            s = style(groups, name)
            h.append(Line2D([], [], color=s["color"], ls=s["ls"], lw=s["lw"]))
            lab.append(s["label"])
    for name in sorted(n for n in used if n not in GROUPS and not n.startswith("_")):
        s = style(groups, name)
        h.append(Line2D([], [], color=s["color"], ls=s["ls"], lw=s["lw"]))
        lab.append(s.get("label", name))
    if "_det2d" in used:
        h.append(Line2D([], [], color=DET2D["color"], ls=DET2D["ls"], lw=DET2D["lw"]))
        lab.append(spec.get("det2d_label", DET2D["label"]))
    if "_points" in used and spec.get("point_label", "points"):
        h.append(Line2D([], [], color="#6E6E6E", marker="o", ls="none", markersize=2.5))
        lab.append(spec.get("point_label", "points"))
    if "_pillar" in used:
        h.append(Patch(facecolor=PILLAR["color"], alpha=PILLAR["alpha"], edgecolor="none"))
        lab.append(spec.get("pillar_label", PILLAR["label"]))
    return h, lab


def render(spec, base, out, img_dpi=300):
    v = spec.get("venue", "cvpr")
    if isinstance(v, dict):
        pp.setup("cvpr", body_pt=v.get("body_pt"), fig_pt=v.get("fig_pt"), single_in=v["width_in"],
                 double_in=v.get("double_in", v["width_in"]))
    else:
        pp.setup(v)
    W = pp.width_in(spec.get("width", "double"))
    fpt = plt.rcParams["font.size"]
    small = plt.rcParams["legend.fontsize"]
    groups = spec.get("groups", {})
    rows = spec["rows"]
    aspects = [[prepare(p, base) for p in row] for row in rows]
    title_h = fpt * 1.35 / 72

    keys = []
    for row, asp in zip(rows, aspects):
        H = (W - 2 * MARGIN - GAP_W * (len(row) - 1)) / sum(1 / a for a in asp)
        keys.append([key_lines(p, H / a, small) for p, a in zip(row, asp)])

    def layout(legend_h):
        boxes, y = [], MARGIN + legend_h
        for row, asp, kl in zip(rows, aspects, keys):
            H = (W - 2 * MARGIN - GAP_W * (len(row) - 1)) / sum(1 / a for a in asp)
            th = title_h if any(p.get("title") for p in row) else 0.0
            fh = max(len(k) for k in kl) * small * 1.25 / 72 + (0.04 if any(kl) else 0.0)
            x = MARGIN
            for p, a in zip(row, asp):
                boxes.append((x, y + th, H / a, H))
                x += H / a + GAP_W
            y += th + H + fh + GAP_H
        return boxes, y - GAP_H + MARGIN

    legend_h = small * 1.9 / 72
    boxes, total = layout(legend_h)
    fig = plt.figure(figsize=(W, total))
    axes, used, missed = [], set(), []
    flat = [p for row in rows for p in row]
    for p, (x, y, w, h) in zip(flat, boxes):
        ax = fig.add_axes([x / W, 1 - (y + h) / total, w / W, h / total])
        if p["type"] == "bev":
            draw_bev(ax, p, groups, used)
        else:
            draw_image(ax, p, groups, used, w, img_dpi)
        if p.get("title"):
            ax.set_title(p["title"], fontsize=fpt, pad=2)
        axes.append(ax)
    for ax, p, kl in zip(axes, flat, [k for row in keys for k in row]):
        if p["type"] == "bev":
            missed += place_labels(fig, ax, p, groups, small)
        if kl:
            draw_key(ax, kl, groups, small)
    handles, labels = legend_entries(used, groups, spec)
    leg = None
    if spec.get("legend", True) and handles:
        n = len(handles)
        for ncol in sorted({math.ceil(n / r) for r in range(1, n + 1)}, reverse=True):
            leg = fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 1 - MARGIN / total), ncol=ncol,
                             frameon=False, fontsize=small, handlelength=1.8, columnspacing=1.0, borderaxespad=0.0)
            bb = leg.get_window_extent(pp._renderer(fig))
            if bb.width <= fig.bbox.width - 4:
                break
            leg.remove()
        need = leg.get_window_extent(pp._renderer(fig)).height / fig.dpi + 0.03
        if need > legend_h + 1e-3:  # the legend wraps: grow the band and move the panels down
            boxes, total = layout(need)
            fig.set_size_inches(W, total)
            for ax, (x, y, w, h) in zip(axes, boxes):
                ax.set_position([x / W, 1 - (y + h) / total, w / W, h / total])
            leg.set_bbox_to_anchor((0.5, 1 - MARGIN / total), transform=fig.transFigure)
            for ax, kl in zip(axes, [k for row in keys for k in row]):
                if kl:
                    for txt in [x for x in ax.texts if x.get_transform() == ax.transAxes]:
                        txt.remove()
                    draw_key(ax, kl, groups, small)
    stats = [f"{p.get('title', '')}: {p['_stats']['pillars']}" for p in flat if p.get("_stats")]
    issues = pp.save(fig, out)
    side = Path(f"{out}_notes.txt")
    lines = []
    if stats:
        lines += ["Occupied pillars per panel (for the caption):"] + stats
    if missed:
        lines += ["Labels that found no free space (put these values in the caption):"] + missed
        print(f"[qualfig] {len(missed)} 个标签找不到不遮挡内容的位置，已写进 {side}，请放进图注")
    if lines:
        side.write_text("\n".join(lines) + "\n", encoding="utf-8")
    plt.close(fig)
    return issues, missed


# ------------------------------------------------------------------ demo
def _demo(outdir):
    from PIL import Image, ImageDraw, ImageFilter
    out = Path(outdir)
    data = out / "demo_data"
    data.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    K = [[1266.4, 0.0, 816.3], [0.0, 1266.4, 491.5], [0.0, 0.0, 1.0]]
    R = np.array([[0, -1, 0], [0, 0, -1], [1, 0, 0]], float)      # camera axes from LiDAR axes (x fwd, y left, z up)
    cam = np.array([0.6, 0.0, -0.25])
    T = np.eye(4)
    T[:3, :3], T[:3, 3] = R, -R @ cam

    def scene(seed, objs, name):
        r = np.random.default_rng(seed)
        W_, H_ = 1600, 900
        sky = np.linspace([150, 182, 214], [214, 224, 232], 491).astype(np.uint8)
        img = np.zeros((H_, W_, 3), np.uint8)
        img[:491] = sky[:, None, :]
        img[491:] = np.array([96, 98, 100], np.uint8)
        im = Image.fromarray(img)
        dr = ImageDraw.Draw(im)
        dr.polygon([(816, 491), (-600, 900), (2200, 900)], fill=(72, 74, 78))
        for x_, y_ in ((12, 5.5), (25, -6.0), (40, 7.0)):
            uv, d = project(box_corners([x_, y_, 3.0, 1.0, 1.0, 8.0, 0.0])[[0, 3, 4, 7]], T, K)
            if (d > 0).all():
                dr.rectangle([uv[:, 0].min(), uv[:, 1].min(), uv[:, 0].max(), uv[:, 1].max()], fill=(120, 132, 118))
        for o in sorted(objs, key=lambda o: -o[0]):
            uv, d = project(box_corners(o), T, K)
            if (d > 0.1).all():
                faces = [[0, 1, 5, 4], [1, 2, 6, 5], [3, 0, 4, 7], [4, 5, 6, 7]]
                for f, sh in zip(faces, (1.0, 0.8, 0.8, 1.15)):
                    col = tuple(int(min(255, c * sh)) for c in o[7])
                    dr.polygon([tuple(uv[i]) for i in f], fill=col)
        arr = np.asarray(im.filter(ImageFilter.GaussianBlur(1.2))).astype(np.int16)
        arr = np.clip(arr + r.normal(0, 5, arr.shape), 0, 255).astype(np.uint8)
        Image.fromarray(arr).save(data / f"{name}.jpg", quality=90)
        rings = []
        for k, el in enumerate(np.linspace(-0.43, -0.03, 24)):
            rr = 1.75 / max(1e-3, -math.tan(el))
            if rr > 70:
                continue
            th = np.linspace(-math.pi, math.pi, int(900 * min(1.0, 18 / rr)) + 200)
            rings.append(np.c_[rr * np.cos(th), rr * np.sin(th), np.full(len(th), -1.75)])
        ground = np.vstack(rings) + r.normal(0, 0.03, (sum(len(x) for x in rings), 3))
        obj_pts = []
        for o in objs:
            c = box_corners(o)
            for _ in range(int(400 / max(1.0, o[0] / 10))):
                f = r.integers(0, 4)
                a, b = c[[0, 1, 2, 3][f]], c[[1, 2, 3, 0][f]]
                t = r.random()
                p = a + (b - a) * t
                p[2] = o[2] - o[5] / 2 + r.random() * o[5]
                obj_pts.append(p)
        pts = np.vstack([ground, np.array(obj_pts) + r.normal(0, 0.02, (len(obj_pts), 3))])
        np.save(data / f"{name}_lidar.npy", pts.astype(np.float32))
        radar = []
        for o in objs:
            n = r.integers(3, 9)
            radar.append(np.c_[o[0] + r.normal(0, 0.6, n), o[1] + r.normal(0, 0.4, n), o[2] + r.normal(0, 0.3, n)])
        clutter = np.c_[r.uniform(2, 78, 60), r.uniform(-35, 35, 60), r.uniform(-1.5, 1.5, 60)]
        np.save(data / f"{name}_radar.npy", np.vstack(radar + [clutter]).astype(np.float32))
        return [o[:7] for o in objs]

    car, ped = (52, 92, 160), (176, 64, 52)
    frames = []
    for k, objs in enumerate([
        [(9.0, 2.2, -0.9, 4.4, 1.9, 1.6, 0.05, car), (16, -3.2, -0.95, 4.6, 1.9, 1.5, 3.10, car), (13, 6.5, -0.9, 0.7, 0.7, 1.75, 1.6, ped)],
        [(24, -1.0, -0.9, 4.5, 1.9, 1.6, 0.0, car), (41, 3.3, -0.85, 4.8, 2.0, 1.7, 3.14, car), (52, -2.9, -0.8, 11.0, 2.6, 3.2, 0.0, (110, 112, 90))],
        [(18, 0.5, -0.9, 4.3, 1.8, 1.5, 0.02, car), (63, 2.0, -0.8, 4.6, 1.9, 1.6, 3.1, car), (71, -4.0, -0.8, 4.7, 1.9, 1.6, 0.0, car)],
    ]):
        frames.append(scene(10 + k, objs, f"frame{k}"))

    def jitter(b, s):
        return [b[0] + rng.normal(0, s), b[1] + rng.normal(0, s), b[2], b[3], b[4], b[5], b[6] + rng.normal(0, s / 4)]

    # 1) deployment comparison, CVPR double column: native and accelerated detections coincide
    cols_img, cols_bev = [], []
    for k, gt in enumerate(frames):
        det = [jitter(b, 0.15) for b in gt]
        boxes = [{"box": b, "group": "gt"} for b in gt] + [{"box": b, "group": "native"} for b in det] + \
                [{"box": [*b[:6], b[6] + 0.003], "group": "sdfp"} for b in det]
        cols_img.append({"type": "image", "image": f"demo_data/frame{k}.jpg", "K": K, "T": T.tolist(),
                         "boxes": boxes, "points": f"demo_data/frame{k}_radar.npy", "point_size": 5,
                         "title": ["Near traffic", "Mid range", "Far range"][k]})
        cols_bev.append({"type": "bev", "points": f"demo_data/frame{k}_radar.npy", "range": [0, 80, -40, 40],
                         "axes": "forward_up", "boxes": boxes, "point_size": 2.5, "point_color": "#222222",
                         "pillars": {"origin": [0, -39.68], "size": [0.16, 0.16], "grid_shape": [496, 496]},
                         "title": "13.9 ms → 9.8 ms"})
    spec1 = {"venue": "cvpr", "width": "double", "point_label": "radar points", "rows": [cols_img, cols_bev]}
    # 2) selection cases, single-column journal page: BEV crop with labels next to the camera crop
    gt = frames[0]
    case_rows = []
    for k, (gi, go, gi_txt, go_txt) in enumerate([
        (2, 1, "ped., y = 1\ns = 0.20, q = 0.88", "car, y = 0\ns = 0.47, q = 0.04"),
        (0, 1, "car, y = 1\ns = 0.31, q = 0.72", "car, y = 0\ns = 0.52, q = 0.11"),
    ]):
        bin_ = jitter(gt[gi], 0.2)
        bout = jitter(gt[go], 0.9)
        bout[0] += 1.6
        boxes = [{"box": gt[gi], "group": "gt"}, {"box": bin_, "group": "in", "text": gi_txt},
                 {"box": bout, "group": "out", "text": go_txt}]
        cx, cy = (bin_[0] + bout[0]) / 2, (bin_[1] + bout[1]) / 2
        case_rows.append([
            {"type": "bev", "points": "demo_data/frame0_lidar.npy", "range": [cx - 13, cx + 13, cy - 11, cy + 11],
             "axes": "forward_up", "boxes": boxes, "grid_m": 5, "point_size": 0.5, "title": f"Case {k + 1}, BEV"},
            {"type": "image", "image": "demo_data/frame0.jpg", "K": K, "T": T.tolist(), "boxes": boxes,
             "dets2d": [[1000, 455, 1135, 610]] if k == 0 else [[560, 470, 760, 600]], "crop": "auto",
             "title": "front camera, support 0.63" if k == 0 else "front camera, support 0.48"}])
    spec2 = {"venue": {"width_in": 5.39, "body_pt": 12, "fig_pt": 10}, "width": "single",
             "point_label": "LiDAR points", "rows": case_rows}
    # 3) one keyframe: score-gate set versus re-ranked set with the same count
    g1 = frames[1]
    sg = [{"box": jitter(g1[0], 0.2), "group": "tp"}, {"box": jitter(g1[1], 0.2), "group": "tp"},
          {"box": [30, -9, -0.9, 4.4, 1.9, 1.6, 0.4], "group": "fp"}, {"box": [47, 12, -0.9, 4.4, 1.9, 1.6, 2.0], "group": "fp"}]
    sr = sg[:2] + [{"box": jitter(g1[2], 0.3), "group": "tp"}, {"box": [47, 12, -0.9, 4.4, 1.9, 1.6, 2.0], "group": "fp"}]
    gtb = [{"box": b, "group": "gt"} for b in g1]
    spec3 = {"venue": {"width_in": 5.39, "body_pt": 12, "fig_pt": 10}, "width": "single", "point_label": "LiDAR points",
             "rows": [[{"type": "image", "image": "demo_data/frame1.jpg", "K": K, "T": T.tolist(), "boxes": gtb + sg,
                        "crop": [300, 300, 1400, 700], "title": "Score gate: 4 kept, 2 matched"},
                       {"type": "image", "image": "demo_data/frame1.jpg", "K": K, "T": T.tolist(), "boxes": gtb + sr,
                        "crop": [300, 300, 1400, 700], "title": "SACR: 4 kept, 3 matched"}],
                      [{"type": "bev", "points": "demo_data/frame1_lidar.npy", "range": [0, 60, -25, 25], "axes": "forward_up",
                        "boxes": gtb + sg, "point_size": 0.4},
                       {"type": "bev", "points": "demo_data/frame1_lidar.npy", "range": [0, 60, -25, 25], "axes": "forward_up",
                        "boxes": gtb + sr, "point_size": 0.4}]]}
    for name, spec in (("demo_deployment", spec1), ("demo_cases", spec2), ("demo_frame", spec3)):
        (out / f"{name}.json").write_text(json.dumps(spec, ensure_ascii=False, indent=1), encoding="utf-8")
        render(spec, out, out / name)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", nargs="?", help="figure spec (JSON)")
    ap.add_argument("--out", help="output stem, e.g. figs/qual")
    ap.add_argument("--img-dpi", type=int, default=300, help="pixels per inch kept for camera images (default 300)")
    ap.add_argument("--demo", metavar="OUTDIR", help="render three example figures from synthetic data")
    a = ap.parse_args()
    if a.demo:
        _demo(a.demo)
        return
    if not a.spec or not a.out:
        ap.error("give a spec and --out, or --demo OUTDIR")
    spec_path = Path(a.spec)
    issues, missed = render(json.loads(spec_path.read_text(encoding="utf-8")), spec_path.parent, a.out, a.img_dpi)
    sys.exit(1 if issues else 0)


if __name__ == "__main__":
    main()
