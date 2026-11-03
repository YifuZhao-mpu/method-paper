#!/usr/bin/env python3
"""Build an academic algorithm-framework figure from a JSON spec.

    python framework_figure.py spec.json -o figs/framework [--formats pdf,svg,png] [--dpi 300]

Design rules baked in
- Stages are separated by colored regions, never by (a)(b)(c) letters.
- A legend strip is added automatically below the drawing: one entry per region
  (color swatch, region name, one-sentence mechanism) plus line-style keys.
  Put mechanism text there, never results or metric numbers.
- The figure is built at its final printed width, so font_pt is the size readers see.
- Solid arrows = forward data flow, dashed arrows = training-only supervision,
  a snowflake marks frozen modules.
- PDF embeds TrueType fonts, SVG keeps text editable, PNG is for preview.
- After drawing, the script checks for text overlapping text, nodes or arrows,
  module text overflowing its box, arrows crossing unrelated nodes, nodes
  leaving their region, and anything outside the canvas. Fix every warning.

Coordinates are inches from the bottom-left corner of the drawing area.
See references/figures.md and assets/framework_example.json for the schema.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import colors as mcolors  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle  # noqa: E402
from matplotlib.transforms import Bbox  # noqa: E402

# TrueType Times-like fonts first (embed as Type 42); Nimbus Roman / TeX Gyre Termes are usually OpenType-CFF.
TIMES_LIKE = ["Times New Roman", "Times", "Liberation Serif", "Nimbus Roman", "Nimbus Roman No9 L", "TeX Gyre Termes"]
LATIN = TIMES_LIKE + ["STIXGeneral", "DejaVu Serif"]
CJK = ["SimSun", "Songti SC", "STSong", "Noto Serif CJK SC", "Source Han Serif SC", "Noto Serif SC",
       "AR PL UMing CN"]
CJK_RE = re.compile(r"[　-〿㐀-鿿＀-￯]")
TOKEN_RE = re.compile(r"\$[^$]*\$|[　-〿㐀-鿿＀-￯]|[^\s　-〿㐀-鿿＀-￯$]+|\s+")
NO_LINE_START = set("，。、；：！？）》」』】,.;:!?)]}%")
MATH_SPLIT = re.compile(r"(\$[^$]*\$)")
TEXT_COLOR = "#1A1A1A"
EDGE_COLOR = "#333333"
LOSS_COLOR = "#B2182B"
FROZEN_FILL, FROZEN_EDGE = "#ECECEC", "#8C8C8C"
NAMED = {"蓝色": "#0072B2", "橙色": "#E69F00", "绿色": "#009E73", "红色": "#D55E00", "紫色": "#CC79A7",
         "青色": "#56B4E9", "黄色": "#F0E442", "灰色": "#7F7F7F", "棕色": "#8C564B"}
NAMED_EN = {"蓝色": "blue", "橙色": "orange", "绿色": "green", "红色": "red", "紫色": "purple",
            "青色": "cyan", "黄色": "yellow", "灰色": "gray", "棕色": "brown"}


def tint(c, t):
    r, g, b = mcolors.to_rgb(c)
    return (r + (1 - r) * t, g + (1 - g) * t, b + (1 - b) * t)


def shade(c, t):
    r, g, b = mcolors.to_rgb(c)
    return (r * (1 - t), g * (1 - t), b * (1 - t))


def color_name(c):
    rgb = np.array(mcolors.to_rgb(c))
    best = min(NAMED, key=lambda k: np.sum((np.array(mcolors.to_rgb(NAMED[k])) - rgb) ** 2))
    return best


def has_cjk(s):
    return bool(CJK_RE.search(str(s)))


# ----------------------------------------------------------------- data loaders
def load_points(path, bin_dims=None, notes=None):
    """Return an Nx3 float array from .npy/.npz, raw float32 .bin (KITTI 4 values per point, nuScenes 5),
    .txt/.csv/.xyz, ASCII or binary .ply, ASCII or binary .pcd."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".npy":
        a = np.load(p)
    elif ext == ".npz":
        z = np.load(p)
        a = z[z.files[0]]
    elif ext == ".bin":
        raw = np.fromfile(p, dtype=np.float32)
        if bin_dims:
            dims = int(bin_dims)
            if raw.size % dims:
                raise ValueError(f"{p.name}: {raw.size} floats is not a multiple of bin_dims={dims}")
        else:
            fits = [d for d in (4, 5) if raw.size % d == 0]
            if not fits:
                raise ValueError(f"{p.name}: cannot infer values per point; set bin_dims")
            dims = fits[0]
            if len(fits) > 1 and notes is not None:
                notes.append(f"{p.name}：按每点 4 个数（KITTI）读取；如果是 nuScenes 的 .pcd.bin，设 \"bin_dims\": 5")
            elif dims == 5 and notes is not None:
                notes.append(f"{p.name}：按每点 5 个数（nuScenes）读取")
        a = raw.reshape(-1, dims)
    elif ext in (".txt", ".csv", ".xyz"):
        a = np.loadtxt(p, delimiter="," if ext == ".csv" else None, usecols=(0, 1, 2))
    elif ext == ".ply":
        a = _read_ply(p)
    elif ext == ".pcd":
        a = _read_pcd(p)
    else:
        raise ValueError(f"unsupported point file: {p.name}")
    a = np.asarray(a, dtype=float)
    return a[:, :3][np.isfinite(a[:, :3]).all(axis=1)]


_PLY_TYPES = {"char": "i1", "uchar": "u1", "int8": "i1", "uint8": "u1", "short": "i2", "ushort": "u2",
              "int16": "i2", "uint16": "u2", "int": "i4", "uint": "u4", "int32": "i4", "uint32": "u4",
              "float": "f4", "float32": "f4", "double": "f8", "float64": "f8"}


def _read_ply(p):
    with open(p, "rb") as f:
        header, props, n, fmt, in_vertex = [], [], 0, "ascii", False
        while True:
            line = f.readline().decode("ascii", "ignore").strip()
            header.append(line)
            if line.startswith("format"):
                fmt = line.split()[1]
            elif line.startswith("element"):
                in_vertex = line.split()[1] == "vertex"
                if in_vertex:
                    n = int(line.split()[2])
            elif line.startswith("property") and in_vertex:
                parts = line.split()
                if parts[1] == "list":
                    raise ValueError("list properties in the vertex element are not supported")
                props.append((parts[2], _PLY_TYPES[parts[1]]))
            elif line == "end_header":
                break
        names = [q[0] for q in props]
        if fmt == "ascii":
            a = np.loadtxt(f, max_rows=n)
            return a[:, [names.index("x"), names.index("y"), names.index("z")]]
        endian = "<" if "little" in fmt else ">"
        dt = np.dtype([(nm, endian + t) for nm, t in props])
        a = np.frombuffer(f.read(dt.itemsize * n), dtype=dt, count=n)
        return np.column_stack([a["x"], a["y"], a["z"]])


def _read_pcd(p):
    with open(p, "rb") as f:
        meta = {}
        while True:
            line = f.readline().decode("ascii", "ignore").strip()
            if not line or line.startswith("#"):
                continue
            k, *v = line.split()
            meta[k.upper()] = v
            if k.upper() == "DATA":
                break
        fields = meta["FIELDS"]
        sizes = [int(s) for s in meta["SIZE"]]
        types = meta["TYPE"]
        counts = [int(c) for c in meta.get("COUNT", ["1"] * len(fields))]
        n = int(meta.get("POINTS", meta.get("WIDTH"))[0])
        mode = meta["DATA"][0].lower()
        if mode == "ascii":
            a = np.loadtxt(f, max_rows=n)
            idx, col = [], 0
            for fld, c in zip(fields, counts):
                if fld in ("x", "y", "z"):
                    idx.append(col)
                col += c
            return a[:, idx]
        if mode != "binary":
            raise ValueError("binary_compressed PCD is not supported; convert to .npy first")
        dt = np.dtype([(fld, {"F": "f", "I": "i", "U": "u"}[t] + str(s), (c,) if c > 1 else ())
                       for fld, s, t, c in zip(fields, sizes, types, counts)])
        a = np.frombuffer(f.read(dt.itemsize * n), dtype=dt, count=n)
        return np.column_stack([a["x"], a["y"], a["z"]]).astype(float)


def synthetic_points(seed=0, n_rings=24):
    rng = np.random.default_rng(seed)
    pts = []
    for i in range(n_rings):
        r = 2.5 + i * 1.15
        th = np.linspace(0, 2 * np.pi, int(160 + 9 * r), endpoint=False) + rng.normal(0, 0.002, 1)
        pts.append(np.column_stack([r * np.cos(th), r * np.sin(th), -1.7 + rng.normal(0, 0.03, th.size)]))
    for _ in range(7):
        cx, cy = rng.uniform(-18, 18, 2)
        if abs(cx) < 4 and abs(cy) < 4:
            continue
        l, w, h = rng.uniform(3.6, 4.6), rng.uniform(1.6, 1.9), rng.uniform(1.4, 1.7)
        k = 260
        face = rng.integers(0, 3, k)
        u, v = rng.uniform(-0.5, 0.5, k), rng.uniform(0, 1, k)
        x = np.where(face == 0, u * l, np.where(face == 1, l / 2, u * l))
        y = np.where(face == 0, -w / 2, np.where(face == 1, u * w, w / 2))
        pts.append(np.column_stack([cx + x, cy + y, -1.7 + v * h]))
    for _ in range(5):
        cx, cy = rng.uniform(-20, 20, 2)
        z = np.linspace(-1.7, 3.5, 40)
        pts.append(np.column_stack([cx + rng.normal(0, 0.05, z.size), cy + rng.normal(0, 0.05, z.size), z]))
    return np.vstack(pts)


def synthetic_heatmap(rows, cols, seed=0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:rows, 0:cols]
    a = np.zeros((rows, cols))
    for _ in range(3):
        cy, cx = rng.uniform(0, rows), rng.uniform(0, cols)
        s = rng.uniform(1.0, 2.2)
        a += np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s)) * rng.uniform(0.6, 1.0)
    a += rng.uniform(0, 0.15, a.shape)
    return a / a.max()


# ------------------------------------------------------------------------ builder
class Builder:
    def __init__(self, spec, base_dir, dpi=300):
        self.spec = spec
        self.base = Path(base_dir)
        self.dpi = dpi
        c = spec.get("canvas", {})
        self.W = float(c.get("width_in", 6.875))
        self.H = float(c.get("height_in", 2.3))
        self.fp = float(c.get("font_pt", 9))
        self.small = max(6.5, self.fp - 1)
        self.show_region_names = bool(c.get("show_region_names", False))
        self.regions = {r["id"]: r for r in spec.get("regions", [])}
        self.nodes = {n["id"]: n for n in spec.get("nodes", [])}
        self.edges = spec.get("edges", [])
        self.legend_cfg = spec.get("legend", {})
        # descriptions in the legend strip (default) or in the caption; never both
        self.show_desc = bool(self.legend_cfg.get("descriptions", True))
        self.warnings = []
        self.notes = []      # informational, do not fail the check
        self.texts = []      # (owner, kind, artist)
        self.boxes = {}      # node id -> (x0, y0, x1, y1) occupied area
        self.icons = []      # (node id, box) small badges such as the frozen snowflake
        self.edge_pts = []   # (edge index, from id, to id, Nx2 samples)
        all_text = " ".join(str(r.get("name", "")) + str(r.get("desc", "")) for r in self.regions.values())
        self.zh = has_cjk(all_text)
        self._setup_fonts(c)
        self._mfig = plt.figure(figsize=(self.W, 2), dpi=100)
        self._mr = self._mfig.canvas.get_renderer()

    # ---- fonts and text measurement
    def _setup_fonts(self, c):
        installed = {f.name for f in font_manager.fontManager.ttflist}
        latin = [f for f in (c.get("font_family") or LATIN) if f in installed]
        cjk = [f for f in (c.get("cjk_family") or CJK) if f in installed]
        if not c.get("font_family") and not any(f in installed for f in TIMES_LIKE):
            self.warn("没有找到 Times 系字体（Times New Roman、Liberation Serif 等），已退回 STIX；"
                      "安装 Times New Roman 或在 canvas.font_family 指定。")
        if latin:
            try:
                ff = font_manager.findfont(font_manager.FontProperties(family=latin[0]), fallback_to_default=False)
            except Exception:
                ff = ""
            if ff.lower().endswith(".otf"):
                self.notes.append(f"{latin[0]} 是 OpenType-CFF 字体，PDF 中以 CFF 嵌入，部分 PDF 检查会报字体类型警告；"
                                  "有 Times New Roman 时优先用它")
        everything = json.dumps(self.spec, ensure_ascii=False)
        if has_cjk(everything) and not cjk:
            self.warn("图中有中文但没有找到中文字体（SimSun、Noto Serif CJK SC 等），中文会显示成方框。")
        matplotlib.rcParams.update({
            "font.family": (latin + [f for f in cjk if f not in latin]) or ["serif"],
            "mathtext.fontset": "stix",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "axes.unicode_minus": False,
        })

    def _measure_one(self, s, size, weight):
        t = self._mfig.text(0, 0, s, fontsize=size, fontweight=weight)
        bb = t.get_window_extent(self._mr)
        t.remove()
        return bb.width / self._mfig.dpi, bb.height / self._mfig.dpi

    def measure(self, s, size, weight="normal"):
        """Width and height in inches. Prose and $math$ are measured separately, matching how they are drawn."""
        s = str(s)
        if "$" not in s:
            return self._measure_one(s, size, weight)
        w = h = 0.0
        for seg in (g for g in MATH_SPLIT.split(s) if g):
            ww, hh = self._measure_one(seg, size, weight)
            w, h = w + ww, max(h, hh)
        return w, h

    def wrap(self, s, maxw, size, weight="normal"):
        lines = []
        for para in str(s).split("\n"):
            cur = ""
            for tok in TOKEN_RE.findall(para):
                if tok.isspace():
                    cur = cur + " " if cur else cur
                    continue
                cand = cur + tok
                if not cur.strip() or tok in NO_LINE_START or self.measure(cand.rstrip(), size, weight)[0] <= maxw:
                    cur = cand
                else:
                    lines.append(cur.rstrip())
                    cur = tok
            lines.append(cur.rstrip())
        return lines or [""]

    def warn(self, msg):
        self.warnings.append(msg)

    # ---- helpers
    def node_color(self, n):
        if n.get("color"):
            return n["color"]
        r = self.regions.get(n.get("region"))
        return r["color"] if r else "#7F7F7F"

    def box(self, n):
        x, y, w, h = [float(v) for v in n["box"]]
        return x, y, w, h

    def occupied(self, nid):
        return self.boxes[nid]

    def add_text(self, owner, kind, x, y, s, **kw):
        kw.setdefault("fontsize", self.fp)
        kw.setdefault("color", TEXT_COLOR)
        kw.setdefault("zorder", 8)
        if "$" in str(s):
            return self._rich(owner, kind, x, y, str(s), **kw)
        t = self.ax.text(x, y, s, **kw)
        self.texts.append((owner, kind, t))
        return t

    def _rich(self, owner, kind, x, y, s, ha="center", va="center", fontsize=None, color=TEXT_COLOR,
              fontweight="normal", linespacing=1.12, zorder=8, style="normal"):
        """Draw prose in the body font and $math$ in STIX, segment by segment.

        matplotlib renders a whole string through mathtext as soon as it contains $...$, which
        turns Chinese characters into boxes and switches the prose font. Splitting the line
        keeps prose in Times/SimSun and only the formula in STIX.
        """
        size = float(fontsize or self.fp)
        for m in MATH_SPLIT.findall(s):
            if has_cjk(m):
                self.warn(f"公式 {m} 里有中文：中文放到 $ 外面")
        lines = s.split("\n")
        asc, desc = 0.78 * size / 72, 0.24 * size / 72
        lh = size * 1.18 * linespacing / 72
        block = asc + desc + (len(lines) - 1) * lh
        top = {"top": y, "bottom": y + block, "baseline": y + asc}.get(va, y + block / 2)
        first = None
        for i, line in enumerate(lines):
            base = top - asc - i * lh
            segs = [g for g in MATH_SPLIT.split(line) if g]
            widths = [self._measure_one(g, size, fontweight)[0] for g in segs]
            total = sum(widths)
            x0 = x - total / 2 if ha == "center" else (x - total if ha == "right" else x)
            for g, w in zip(segs, widths):
                t = self.ax.text(x0, base, g, ha="left", va="baseline", fontsize=size, color=color,
                                 fontweight=fontweight, zorder=zorder, style=style)
                self.texts.append((owner, kind, t))
                first = first or t
                x0 += w
        return first

    def add_label(self, n, x0, y0, x1, y1):
        label = n.get("label")
        pos = n.get("label_pos", "below")
        if not label or pos == "none":
            return
        size = float(n.get("label_pt", self.fp))
        maxw = float(n.get("label_width", max(x1 - x0 + 0.4, 1.2)))
        s = "\n".join(self.wrap(label, maxw, size))
        g = 0.04
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if pos == "below":
            self.add_text(n["id"], "label", cx, y0 - g, s, ha="center", va="top", fontsize=size, linespacing=1.1)
        elif pos == "above":
            self.add_text(n["id"], "label", cx, y1 + g, s, ha="center", va="bottom", fontsize=size, linespacing=1.1)
        elif pos == "left":
            self.add_text(n["id"], "label", x0 - g, cy, s, ha="right", va="center", fontsize=size, linespacing=1.1)
        elif pos == "right":
            self.add_text(n["id"], "label", x1 + g, cy, s, ha="left", va="center", fontsize=size, linespacing=1.1)

    def resolve(self, src):
        if not src:
            return None
        p = Path(src)
        if not p.is_absolute():
            p = self.base / p
        return p if p.exists() else None

    # ---- drawing primitives
    def draw_region(self, r):
        x, y, w, h = [float(v) for v in r["box"]]
        c = r["color"]
        self.ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0,rounding_size=0.06",
                                         fc=tint(c, 0.90), ec=tint(c, 0.25), lw=0.8, zorder=1))
        if self.show_region_names:
            self.add_text("region:" + r["id"], "region", x + 0.06, y + h - 0.05, r.get("name", ""),
                          ha="left", va="top", fontsize=self.small, color=shade(c, 0.3))

    def draw_module(self, n):
        x, y, w, h = self.box(n)
        c = self.node_color(n)
        frozen = bool(n.get("frozen", False))
        self.ax.add_patch(FancyBboxPatch(
            (x, y), w, h, boxstyle=f"round,pad=0,rounding_size={min(0.05, h * 0.25):.3f}",
            fc=FROZEN_FILL if frozen else tint(c, 0.68), ec=FROZEN_EDGE if frozen else c, lw=0.9, zorder=4))
        size = float(n.get("text_pt", self.fp))
        raw = str(n.get("text", ""))
        lines = self.wrap(raw, w - 0.08, size)
        if has_cjk(raw) and len(lines) > raw.count("\n") + 1:
            self.warn(f"节点 {n['id']} 的中文被自动折行（{'/'.join(lines)}）：加宽方框，或在 text 里用 \\n 在词语之间断行")
        s = "\n".join(lines)
        self.add_text(n["id"], "inner", x + w / 2, y + h / 2, s, ha="center", va="center", fontsize=size,
                      linespacing=1.12)
        if frozen:  # badge sits on the top-right corner so it never covers the module text
            cx, cy, rb = x + w, y + h, 0.052
            self.ax.add_patch(Circle((cx, cy), rb, fc="white", ec=FROZEN_EDGE, lw=0.5, zorder=6.8))
            self.snowflake(cx, cy, 0.036, zorder=7)
            self.icons.append((n["id"], (cx - rb, cy - rb, cx + rb, cy + rb)))
        self.boxes[n["id"]] = (x, y, x + w, y + h)

    def snowflake(self, cx, cy, r, color="#4F7FA8", zorder=7):
        for k in range(3):
            a = math.pi / 3 * k
            dx, dy = r * math.cos(a), r * math.sin(a)
            self.ax.add_line(Line2D([cx - dx, cx + dx], [cy - dy, cy + dy], lw=0.7, color=color, zorder=zorder,
                                    solid_capstyle="round"))

    def draw_tensor(self, n):
        x, y, w, h = self.box(n)
        c = self.node_color(n)
        k = max(1, int(n.get("layers", 4)))
        fw, fh = w * 0.70, h * 0.74
        dx = (w - fw) / (k - 1) if k > 1 else 0
        dy = (h - fh) / (k - 1) if k > 1 else 0
        for i in range(k):
            j = k - 1 - i  # j = distance from the front layer
            self.ax.add_patch(Rectangle((x + j * dx, y + j * dy), fw, fh, fc=tint(c, 0.30 + 0.45 * j / max(1, k - 1)),
                                        ec=shade(c, 0.35), lw=0.5, zorder=4 + i * 0.01))
        self.boxes[n["id"]] = (x, y, x + w, y + h)
        self.add_label(n, x, y, x + w, y + h)

    def draw_image(self, n):
        x, y, w, h = self.box(n)
        stack = max(1, int(n.get("stack", 1)))
        off = float(n.get("stack_offset", 0.04))
        for i in range(stack - 1, 0, -1):
            self.ax.add_patch(Rectangle((x + i * off, y + i * off), w, h, fc="white", ec="#808080", lw=0.6, zorder=4))
        p = self.resolve(n.get("src"))
        if p is None:
            if n.get("src"):
                self.warn(f"节点 {n['id']}：找不到图片，已画占位框（请填真实的数据集样例）")
            else:
                self.warn(f"节点 {n['id']}：没有给出 src，已画占位框（请填真实的数据集样例）")
            self.ax.add_patch(Rectangle((x, y), w, h, fc="#E3E3E3", ec="none", zorder=5))
            for a, b in (((x, y), (x + w, y + h)), ((x, y + h), (x + w, y))):
                self.ax.add_line(Line2D([a[0], b[0]], [a[1], b[1]], lw=0.4, color="#B0B0B0", zorder=5.2))
        else:
            img = self._load_image(p, w, h, n.get("crop"))
            self.ax.imshow(img, extent=[x, x + w, y, y + h], zorder=5, interpolation="antialiased")
        self.ax.add_patch(Rectangle((x, y), w, h, fc="none", ec="#4D4D4D", lw=0.6, zorder=5.5))
        x1, y1 = x + w + (stack - 1) * off, y + h + (stack - 1) * off
        self.boxes[n["id"]] = (x, y, x1, y1)
        self.add_label(n, x, y, x1, y1)

    def _load_image(self, p, w, h, crop=None):
        from PIL import Image
        im = Image.open(p).convert("RGB")
        W0, H0 = im.size
        if crop:
            l, t, r, b = crop
            im = im.crop((int(l * W0), int(t * H0), int(r * W0), int(b * H0)))
            W0, H0 = im.size
        target = w / h
        if W0 / H0 > target:
            nw = int(H0 * target)
            im = im.crop(((W0 - nw) // 2, 0, (W0 - nw) // 2 + nw, H0))
        else:
            nh = int(W0 / target)
            im = im.crop((0, (H0 - nh) // 2, W0, (H0 - nh) // 2 + nh))
        px_w = max(16, int(round(w * self.dpi)))
        px_h = max(16, int(round(h * self.dpi)))
        if im.size[0] > px_w:
            im = im.resize((px_w, px_h), Image.LANCZOS)
        return np.asarray(im)

    def draw_points(self, n):
        x, y, w, h = self.box(n)
        p = self.resolve(n.get("src"))
        if p is None:
            if n.get("src"):
                self.warn(f"节点 {n['id']}：找不到点云文件，已用示意点云代替（请换成真实数据）")
            else:
                self.warn(f"节点 {n['id']}：没有给出 src，已用示意点云代替（请换成真实数据）")
            P = synthetic_points(int(n.get("seed", 0)))
        else:
            P = load_points(p, n.get("bin_dims"), self.notes)
        if n.get("range"):
            rr = float(n["range"])
            P = P[(np.abs(P[:, 0]) < rr) & (np.abs(P[:, 1]) < rr)]
        maxn = int(n.get("max_points", 5000))
        if len(P) > maxn:
            P = P[np.random.default_rng(0).choice(len(P), maxn, replace=False)]
        z = P[:, 2]
        if n.get("view", "persp") == "bev":
            u, v, depth = P[:, 0], P[:, 1], -z
        else:
            yaw, pitch = math.radians(float(n.get("yaw", -30))), math.radians(float(n.get("pitch", 30)))
            xr = P[:, 0] * math.cos(yaw) - P[:, 1] * math.sin(yaw)
            yr = P[:, 0] * math.sin(yaw) + P[:, 1] * math.cos(yaw)
            u = xr
            v = z * math.cos(pitch) + yr * math.sin(pitch)
            depth = yr * math.cos(pitch) - z * math.sin(pitch)
        order = np.argsort(-depth)
        u, v, z = u[order], v[order], z[order]
        m = 0.06
        su = (w - 2 * m) / max(np.ptp(u), 1e-9)
        sv = (h - 2 * m) / max(np.ptp(v), 1e-9)
        s = min(su, sv)
        uu = x + w / 2 + (u - (u.min() + u.max()) / 2) * s
        vv = y + h / 2 + (v - (v.min() + v.max()) / 2) * s
        lo, hi = np.percentile(z, [2, 98])
        self.ax.add_patch(Rectangle((x, y), w, h, fc="white", ec="#9A9A9A" if n.get("frame", True) else "none",
                                    lw=0.5, zorder=4))
        self.ax.scatter(uu, vv, c=np.clip(z, lo, hi), cmap=n.get("cmap", "viridis"), s=float(n.get("point_size", 0.45)),
                        linewidths=0, zorder=5, rasterized=len(uu) > 6000)
        self.boxes[n["id"]] = (x, y, x + w, y + h)
        self.add_label(n, x, y, x + w, y + h)

    def draw_heatmap(self, n):
        x, y, w, h = self.box(n)
        c = self.node_color(n)
        p = self.resolve(n.get("src"))
        if p is not None:
            A = np.load(p)
        else:
            if n.get("src"):
                self.warn(f"节点 {n['id']}：找不到热力图文件 {n['src']}，已画示意热力图")
            else:
                self.notes.append(f"节点 {n['id']}：未给 src，画的是示意热力图")
            rows, cols = n.get("grid", [8, 8])
            A = synthetic_heatmap(int(rows), int(cols), int(n.get("seed", 0)))
        cm = n.get("cmap", "region")
        cmap = LinearSegmentedColormap.from_list("r", [tint(c, 0.95), c, shade(c, 0.35)]) if cm == "region" else cm
        self.ax.imshow(A, extent=[x, x + w, y, y + h], cmap=cmap, interpolation="nearest", zorder=5)
        rows, cols = A.shape[:2]
        if n.get("grid_lines", True) and rows <= 24 and cols <= 24:
            for i in range(1, cols):
                self.ax.add_line(Line2D([x + w * i / cols] * 2, [y, y + h], lw=0.3, color="white", zorder=5.1))
            for j in range(1, rows):
                self.ax.add_line(Line2D([x, x + w], [y + h * j / rows] * 2, lw=0.3, color="white", zorder=5.1))
        self.ax.add_patch(Rectangle((x, y), w, h, fc="none", ec="#4D4D4D", lw=0.6, zorder=5.5))
        self.boxes[n["id"]] = (x, y, x + w, y + h)
        self.add_label(n, x, y, x + w, y + h)

    def draw_op(self, n):
        cx, cy = [float(v) for v in n["center"]]
        r = float(n.get("r", 0.075))
        self.ax.add_patch(Circle((cx, cy), r, fc="white", ec=EDGE_COLOR, lw=0.8, zorder=6))
        op = n.get("op", "add")
        k = 0.55 * r
        if op == "add":
            segs = [((cx - k, cy), (cx + k, cy)), ((cx, cy - k), (cx, cy + k))]
        elif op == "mul":
            d = k / math.sqrt(2) * 1.2
            segs = [((cx - d, cy - d), (cx + d, cy + d)), ((cx - d, cy + d), (cx + d, cy - d))]
        else:
            segs = []
        for a, b in segs:
            self.ax.add_line(Line2D([a[0], b[0]], [a[1], b[1]], lw=0.8, color=EDGE_COLOR, zorder=6.5))
        if op == "dot":
            self.ax.add_patch(Circle((cx, cy), r * 0.28, fc=EDGE_COLOR, ec="none", zorder=6.5))
        elif op == "concat":
            self.ax.text(cx, cy, "C", ha="center", va="center", fontsize=r * 72 * 1.15, color=EDGE_COLOR, zorder=6.5)
        self.boxes[n["id"]] = (cx - r, cy - r, cx + r, cy + r)
        self.add_label(n, cx - r, cy - r, cx + r, cy + r)

    def draw_loss(self, n):
        x, y, w, h = self.box(n)
        self.ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={min(0.04, h * 0.25):.3f}",
                                         fc="white", ec=LOSS_COLOR, lw=0.9, ls=(0, (3, 1.6)), zorder=4))
        size = float(n.get("text_pt", self.fp))
        s = "\n".join(self.wrap(n.get("text", ""), w - 0.06, size))
        self.add_text(n["id"], "inner", x + w / 2, y + h / 2, s, ha="center", va="center", fontsize=size)
        self.boxes[n["id"]] = (x, y, x + w, y + h)

    def draw_free_text(self, n):
        x, y = [float(v) for v in n["xy"]]
        self.add_text(n["id"], "free", x, y, n.get("text", ""), ha=n.get("ha", "center"), va=n.get("va", "center"),
                      fontsize=float(n.get("size", self.fp)), color=n.get("color", TEXT_COLOR),
                      style=n.get("style", "normal"))

    # ---- edges
    def _anchor(self, nid, side, toward):
        n = self.nodes[nid]
        if n["type"] == "op":
            cx, cy = [float(v) for v in n["center"]]
            r = float(n.get("r", 0.075))
            d = {"right": (1, 0), "left": (-1, 0), "top": (0, 1), "bottom": (0, -1)}.get(side)
            if d is None:
                vx, vy = toward[0] - cx, toward[1] - cy
                L = math.hypot(vx, vy) or 1.0
                d = (vx / L, vy / L)
            return (cx + r * d[0], cy + r * d[1])
        x0, y0, x1, y1 = self.boxes[nid]
        if n["type"] == "image":  # center on the front image, edge on the stacked extent
            x, y, w, h = self.box(n)
            cxm, cym = x + w / 2, y + h / 2
        else:
            cxm, cym = (x0 + x1) / 2, (y0 + y1) / 2
        return {"right": (x1, cym), "left": (x0, cym), "top": (cxm, y1), "bottom": (cxm, y0)}[side]

    def _auto_sides(self, a, b):
        ax0, ay0, ax1, ay1 = self.boxes[a]
        bx0, by0, bx1, by1 = self.boxes[b]
        if bx0 >= ax1 - 1e-6:
            return "right", "left"
        if bx1 <= ax0 + 1e-6:
            return "left", "right"
        if by0 >= ay1 - 1e-6:
            return "top", "bottom"
        if by1 <= ay0 + 1e-6:
            return "bottom", "top"
        dx = (bx0 + bx1 - ax0 - ax1) / 2
        dy = (by0 + by1 - ay0 - ay1) / 2
        if abs(dx) >= abs(dy):
            return ("right", "left") if dx > 0 else ("left", "right")
        return ("top", "bottom") if dy > 0 else ("bottom", "top")

    def draw_edge(self, i, e):
        a, b = e["from"], e["to"]
        for nid in (a, b):
            if nid not in self.boxes:
                raise KeyError(f"edge {i}: unknown node {nid!r}")
        sa, sb = self._auto_sides(a, b)
        sa, sb = e.get("from_side", sa), e.get("to_side", sb)
        via = [tuple(map(float, v)) for v in e.get("via", [])]
        p0 = self._anchor(a, sa, via[0] if via else self._center(b))
        p1 = self._anchor(b, sb, via[-1] if via else p0)
        if self.nodes[b]["type"] == "op" and not e.get("to_side") and not via:
            p1 = self._anchor(b, None, p0)
        if via and e.get("route", "elbow") == "elbow":
            # keep the first and last legs orthogonal to the side they leave or enter
            (vx, vy), (wx, wy) = via[0], via[-1]
            if abs(vx - p0[0]) > 0.02 and abs(vy - p0[1]) > 0.02:
                via.insert(0, (vx, p0[1]) if sa in ("left", "right") else (p0[0], vy))
            if abs(wx - p1[0]) > 0.02 and abs(wy - p1[1]) > 0.02:
                via.append((p1[0], wy) if sb in ("top", "bottom") else (wx, p1[1]))
        pts = [p0] + via + [p1]
        if not via and e.get("route", "elbow") == "elbow":
            (x0, y0), (x1, y1) = p0, p1
            if abs(x1 - x0) > 0.02 and abs(y1 - y0) > 0.02:
                if sa in ("left", "right"):
                    xm = (x0 + x1) / 2
                    pts = [p0, (xm, y0), (xm, y1), p1]
                else:
                    ym = (y0 + y1) / 2
                    pts = [p0, (x0, ym), (x1, ym), p1]
        dashed = e.get("style", "solid") == "dashed"
        color = e.get("color", LOSS_COLOR if dashed and e.get("loss_color", True) else EDGE_COLOR)
        hl, hw = 0.055, 0.046
        (xa, ya), (xb, yb) = pts[-2], pts[-1]
        L = math.hypot(xb - xa, yb - ya) or 1.0
        ux, uy = (xb - xa) / L, (yb - ya) / L
        base = (xb - ux * hl, yb - uy * hl)
        line_pts = pts[:-1] + [base]
        xs, ys = zip(*line_pts)
        self.ax.add_line(Line2D(xs, ys, lw=0.85, color=color, zorder=3.5, ls=(0, (3.2, 1.8)) if dashed else "-",
                                solid_joinstyle="miter", solid_capstyle="butt", dash_capstyle="butt"))
        px, py = -uy, ux
        head = [(xb, yb), (base[0] + px * hw / 2, base[1] + py * hw / 2), (base[0] - px * hw / 2, base[1] - py * hw / 2)]
        self.ax.add_patch(Polygon(head, closed=True, fc=color, ec=color, lw=0.3, zorder=3.6))
        samples = []
        for (u0, v0), (u1, v1) in zip(pts[:-1], pts[1:]):
            m = max(2, int(math.hypot(u1 - u0, v1 - v0) / 0.01))
            t = np.linspace(0, 1, m)
            samples.append(np.column_stack([u0 + (u1 - u0) * t, v0 + (v1 - v0) * t]))
        self.edge_pts.append((i, a, b, np.vstack(samples)))
        if e.get("label"):
            segs = list(zip(pts[:-1], pts[1:]))
            (u0, v0), (u1, v1) = max(segs, key=lambda s: math.hypot(s[1][0] - s[0][0], s[1][1] - s[0][1]))
            mx, my = (u0 + u1) / 2, (v0 + v1) / 2
            horizontal = abs(v1 - v0) < abs(u1 - u0)
            side = e.get("label_side", "above" if horizontal else "right")
            g = 0.035
            kw = dict(fontsize=float(e.get("label_pt", self.small)))
            if side == "above":
                self.add_text(f"edge:{i}", "edge", mx, my + g, e["label"], ha="center", va="bottom", **kw)
            elif side == "below":
                self.add_text(f"edge:{i}", "edge", mx, my - g, e["label"], ha="center", va="top", **kw)
            elif side == "left":
                self.add_text(f"edge:{i}", "edge", mx - g, my, e["label"], ha="right", va="center", **kw)
            else:
                self.add_text(f"edge:{i}", "edge", mx + g, my, e["label"], ha="left", va="center", **kw)

    def _center(self, nid):
        x0, y0, x1, y1 = self.boxes[nid]
        return ((x0 + x1) / 2, (y0 + y1) / 2)

    # ---- legend strip
    def legend_layout(self):
        L = self.legend_cfg
        regs = list(self.regions.values())
        if self.show_desc:
            cols = int(L.get("columns") or (min(len(regs), 3) if self.W >= 5 else min(len(regs), 2)) or 1)
        else:
            cols = int(L.get("columns") or min(len(regs), 5) or 1)
        mx, gap = 0.06, 0.16
        col_w = (self.W - 2 * mx - (cols - 1) * gap) / cols
        sw, tx = 0.12, 0.18
        lh = self.fp * 1.24 / 72
        weight = L.get("name_weight", "bold")
        entries = []
        tw = col_w - tx - 0.03  # safety margin: wrapped lines never touch the next column or the edge
        for r in regs:
            nl = self.wrap(r.get("name", r["id"]), tw, self.fp, weight)
            dl = self.wrap(r["desc"], tw, self.fp) if (r.get("desc") and self.show_desc) else []
            if len(dl) > 3:
                self.warn(f"区域 {r['id']} 的说明有 {len(dl)} 行：底部说明尽量一句话讲清机理")
            if re.search(r"\d+(\.\d+)?\s*(%|dB|ms|FPS|mAP|PSNR|SSIM|LPIPS|NDS|mIoU)", str(r.get("desc", ""))):
                self.warn(f"区域 {r['id']} 的说明里像是有结果数字：底部只写机理，不放结果")
            entries.append((r, nl, dl))
        rows = [entries[k:k + cols] for k in range(0, len(entries), cols)]
        row_h = [max(len(nl) + len(dl) for _, nl, dl in row) * lh for row in rows]
        style = self.style_items()
        style_h = lh * 1.15 if style else 0.0
        top, bottom, rgap = 0.12, 0.03, 0.07
        LH = top + sum(row_h) + rgap * max(0, len(rows) - 1) + ((rgap + style_h) if style else 0.0) + bottom
        return dict(cols=cols, mx=mx, gap=gap, col_w=col_w, sw=sw, tx=tx, lh=lh, rows=rows, row_h=row_h,
                    style=style, style_h=style_h, top=top, rgap=rgap, LH=LH, weight=weight)

    def style_items(self):
        L = self.legend_cfg
        if not L.get("line_styles", True):
            return []
        zh = self.zh
        lab = {"forward": "前向数据流" if zh else "Forward data flow",
               "supervision": "训练阶段监督" if zh else "Training-only supervision",
               "frozen": "冻结参数" if zh else "Frozen parameters"}
        lab.update(L.get("labels", {}))
        items = [("forward", lab["forward"])]
        if any(e.get("style") == "dashed" for e in self.edges):
            items.append(("supervision", lab["supervision"]))
        if any(n.get("frozen") for n in self.nodes.values()):
            items.append(("frozen", lab["frozen"]))
        return items

    def draw_legend(self, lay):
        y = -lay["top"]
        asc = self.fp * 0.80 / 72
        for row, rh in zip(lay["rows"], lay["row_h"]):
            for ci, (r, nl, dl) in enumerate(row):
                x = lay["mx"] + ci * (lay["col_w"] + lay["gap"])
                c = r["color"]
                self.ax.add_patch(FancyBboxPatch((x, y - asc - 0.005), lay["sw"], lay["sw"],
                                                 boxstyle="round,pad=0,rounding_size=0.02",
                                                 fc=tint(c, 0.75), ec=c, lw=0.8, zorder=4))
                yy = y - asc
                for s in nl:
                    self.add_text("legend", "legend", x + lay["tx"], yy, s, ha="left", va="baseline",
                                  color=shade(c, 0.30), fontweight=lay["weight"])
                    yy -= lay["lh"]
                for s in dl:
                    self.add_text("legend", "legend", x + lay["tx"], yy, s, ha="left", va="baseline")
                    yy -= lay["lh"]
            y -= rh + lay["rgap"]
        if lay["style"]:
            n = len(lay["style"])
            slot = (self.W - 2 * lay["mx"]) / n
            yc = y - lay["style_h"] / 2 + 0.01
            for k, (kind, label) in enumerate(lay["style"]):
                x = lay["mx"] + k * slot
                if kind in ("forward", "supervision"):
                    dashed = kind == "supervision"
                    col = LOSS_COLOR if dashed else EDGE_COLOR
                    self.ax.add_line(Line2D([x, x + 0.25], [yc, yc], lw=0.85, color=col,
                                            ls=(0, (3.2, 1.8)) if dashed else "-", zorder=4))
                    self.ax.add_patch(Polygon([(x + 0.30, yc), (x + 0.245, yc + 0.023), (x + 0.245, yc - 0.023)],
                                              closed=True, fc=col, ec=col, lw=0.3, zorder=4))
                else:
                    self.ax.add_patch(Circle((x + 0.25, yc), 0.052, fc="white", ec=FROZEN_EDGE, lw=0.5, zorder=4))
                    self.snowflake(x + 0.25, yc, 0.036, zorder=4.5)
                self.add_text("legend", "legend", x + 0.36, yc, label, ha="left", va="center", fontsize=self.fp)

    # ---- checks
    def _text_boxes(self):
        r = self.fig.canvas.get_renderer()
        inv = self.ax.transData.inverted()
        out = []
        for owner, kind, t in self.texts:
            if not str(t.get_text()).strip():
                continue
            bb = t.get_window_extent(r)
            (x0, y0), (x1, y1) = inv.transform([(bb.x0, bb.y0), (bb.x1, bb.y1)])
            out.append((owner, kind, t, (x0, y0, x1, y1)))
        return out

    @staticmethod
    def _inter(a, b, eps=0.0):
        return min(a[2], b[2]) - max(a[0], b[0]) > eps and min(a[3], b[3]) - max(a[1], b[1]) > eps

    def check(self):
        self.fig.canvas.draw()
        tb = self._text_boxes()
        name = lambda t: "“" + " ".join(str(t.get_text()).split())[:20] + "”"  # noqa: E731
        for i in range(len(tb)):
            for j in range(i + 1, len(tb)):
                oi, ki, ti, bi = tb[i]
                oj, kj, tj, bj = tb[j]
                if oi == oj:
                    continue
                if self._inter(bi, bj, 0.003):
                    self.warn(f"文字 {name(ti)} 与 {name(tj)} 重叠")
        for owner, kind, t, b in tb:
            for nid, box in self.boxes.items():
                if nid == owner:
                    if kind == "inner" and (b[0] < box[0] + 0.01 or b[2] > box[2] - 0.01
                                            or b[1] < box[1] + 0.005 or b[3] > box[3] - 0.005):
                        self.warn(f"节点 {nid} 的文字 {name(t)} 超出方框：加宽方框或缩短文字")
                    continue
                if self._inter(b, box, 0.004):
                    self.warn(f"文字 {name(t)} 压在节点 {nid} 上")
            for nid, ib in self.icons:
                if self._inter(b, ib, 0.002):
                    self.warn(f"文字 {name(t)} 与节点 {nid} 的冻结标记重叠：加宽方框或缩短文字")
            for i, a, c, P in self.edge_pts:
                m = (P[:, 0] > b[0] - 0.01) & (P[:, 0] < b[2] + 0.01) & (P[:, 1] > b[1] - 0.01) & (P[:, 1] < b[3] + 0.01)
                if m.any():
                    self.warn(f"箭头 {a}→{c} 穿过文字 {name(t)}")
            if b[0] < -0.005 or b[2] > self.W + 0.005 or b[1] < -self.LH - 0.005 or b[3] > self.H + 0.005:
                self.warn(f"文字 {name(t)} 超出画布")
        ids = list(self.boxes)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                if self._inter(self.boxes[ids[i]], self.boxes[ids[j]], 0.004):
                    self.warn(f"节点 {ids[i]} 与 {ids[j]} 重叠")
        for i, a, c, P in self.edge_pts:
            for nid, (x0, y0, x1, y1) in self.boxes.items():
                if nid in (a, c):
                    continue
                m = (P[:, 0] > x0 + 0.01) & (P[:, 0] < x1 - 0.01) & (P[:, 1] > y0 + 0.01) & (P[:, 1] < y1 - 0.01)
                if m.any():
                    self.warn(f"箭头 {a}→{c} 穿过节点 {nid}")
        for nid, n in self.nodes.items():
            if nid not in self.boxes:
                continue
            x0, y0, x1, y1 = self.boxes[nid]
            if x0 < -0.005 or x1 > self.W + 0.005 or y0 < -0.005 or y1 > self.H + 0.005:
                self.warn(f"节点 {nid} 超出画布")
            reg = self.regions.get(n.get("region"))
            if reg:
                rx, ry, rw, rh = [float(v) for v in reg["box"]]
                if x0 < rx - 0.005 or x1 > rx + rw + 0.005 or y0 < ry - 0.005 or y1 > ry + rh + 0.005:
                    self.warn(f"节点 {nid} 超出所属区域 {reg['id']}")
        sizes = sorted({round(float(t.get_fontsize()), 2) for _, _, t, _ in tb})
        if sizes and (sizes[0] < 6.5):
            self.warn(f"最小字号 {sizes[0]} pt 偏小：图中文字应接近正文字号")
        return sizes

    # ---- main
    def build(self):
        lay = self.legend_layout() if self.regions else None
        self.LH = lay["LH"] if lay else 0.0
        self.fig = plt.figure(figsize=(self.W, self.H + self.LH), dpi=self.dpi)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, self.W)
        self.ax.set_ylim(-self.LH, self.H)
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_autoscale_on(False)
        self.ax.axis("off")
        for r in self.regions.values():
            self.draw_region(r)
        draw = {"module": self.draw_module, "tensor": self.draw_tensor, "image": self.draw_image,
                "points": self.draw_points, "heatmap": self.draw_heatmap, "op": self.draw_op,
                "loss": self.draw_loss, "text": self.draw_free_text}
        for n in self.nodes.values():
            if n.get("type") not in draw:
                raise ValueError(f"node {n.get('id')}: unknown type {n.get('type')!r}")
            draw[n["type"]](n)
        for i, e in enumerate(self.edges):
            self.draw_edge(i, e)
        if lay:
            self.draw_legend(lay)
        self.ax.set_xlim(0, self.W)
        self.ax.set_ylim(-self.LH, self.H)
        return self.check()

    def caption_draft(self):
        regs = list(self.regions.values())
        dashed = any(e.get("style") == "dashed" for e in self.edges)
        if self.show_desc:  # mechanisms are already under the figure: keep the caption to one overview sentence
            if self.zh:
                return "本文方法的整体框架。【用一句话概括：输入经过哪些机制得到输出】（各区域机理已在图下方说明，图题不再重复）"
            return ("Overview of the proposed method. [One sentence: how the input passes through the key mechanism "
                    "to the output.] (Region mechanisms are already explained under the figure; do not repeat them.)")
        if self.zh:
            parts = [f"{color_name(r['color'])}区域" + (f"（{r['name']}）" if r.get("name") else "")
                     + "：" + str(r.get("desc", "")).strip().rstrip("。；;. ") + "。" for r in regs]
            s = "本文方法的整体框架。" + "".join(parts)
            if dashed:
                s += "实线表示前向数据流，虚线表示仅在训练阶段使用的监督。"
            return s

        def first_upper(d):
            d = str(d).strip().rstrip(".")
            return d[:1].upper() + d[1:] if d else d
        parts = [f"The {NAMED_EN[color_name(r['color'])]} region ({r.get('name', r['id'])}): {first_upper(r.get('desc', ''))}."
                 for r in regs]
        s = "Overview of the proposed method. " + " ".join(parts)
        if dashed:
            s += " Solid arrows denote the forward data flow and dashed arrows denote training-only supervision."
        return s


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("spec", help="JSON spec file")
    ap.add_argument("-o", "--out", required=True, help="output path without extension")
    ap.add_argument("--formats", default="pdf,svg,png")
    ap.add_argument("--dpi", type=int, default=300, help="PNG resolution and image downsampling target")
    a = ap.parse_args(argv)
    spec_path = Path(a.spec)
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    b = Builder(spec, spec_path.parent, dpi=a.dpi)
    sizes = b.build()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    for fmt in [f.strip() for f in a.formats.split(",") if f.strip()]:
        b.fig.savefig(f"{out}.{fmt}", dpi=a.dpi if fmt in ("png", "jpg") else None)
    w, h = b.fig.get_size_inches()
    print(f"[framework] 已输出 {out}.{{{a.formats}}}：{w:.3f}×{h:.3f} in（宽度即插入宽度，不要缩放），字号 {sizes} pt")
    if b.warnings:
        print(f"[framework] {len(b.warnings)} 条警告（逐条修好再交付）：")
        for s in dict.fromkeys(b.warnings):
            print("  - " + s)
    else:
        print("[framework] 检查通过：无遮挡、无越界")
    for s in dict.fromkeys(b.notes):
        print("[framework] 提示：" + s)
    print("[framework] 图题草稿：" + b.caption_draft())
    return 0


if __name__ == "__main__":
    sys.exit(main())
