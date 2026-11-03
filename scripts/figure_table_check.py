#!/usr/bin/env python3
"""Check that figures and tables print with fonts close to the body text.

LaTeX: python figure_table_check.py main.tex --venue cvpr [--body-pt 10] [--min-pt 6.5]
       python figure_table_check.py main.tex --columnwidth-in 3.5 --textwidth-in 7.16 --body-pt 10
Word:  python figure_table_check.py paper.docx

For every \\includegraphics it reports the inserted width, the native width,
the scale factor, the effective font sizes inside PDF figures (size x scale),
Type 3 fonts and the effective resolution of bitmaps. For every table it
reports font-size commands and whole-table scaling (\\resizebox, \\scalebox,
adjustbox), which make table fonts inconsistent. For .docx it compares the
font sizes used in tables with the body text.

Venues: cvpr iccv eccv ieee icml neurips iclr thesis (widths in inches).
"""
from __future__ import annotations

import argparse
import re
import shutil
import statistics
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

VENUES = {
    "cvpr": dict(single=3.28, double=6.875, body_pt=10, twocol=True),
    "iccv": dict(single=3.28, double=6.875, body_pt=10, twocol=True),
    "eccv": dict(single=4.80, double=4.80, body_pt=10, twocol=False),
    "ieee": dict(single=3.5, double=7.16, body_pt=10, twocol=True),
    "icml": dict(single=3.25, double=6.75, body_pt=10, twocol=True),
    "neurips": dict(single=5.50, double=5.50, body_pt=10, twocol=False),
    "iclr": dict(single=5.50, double=5.50, body_pt=10, twocol=False),
    "thesis": dict(single=5.90, double=5.90, body_pt=12, twocol=False),
}
SIZE_PT10 = {"tiny": 5, "scriptsize": 7, "footnotesize": 8, "small": 9, "normalsize": 10, "large": 12, "Large": 14.4}
UNITS = {"in": 1.0, "cm": 1 / 2.54, "mm": 1 / 25.4, "pt": 1 / 72.27, "bp": 1 / 72.0, "pc": 12 / 72.27}


def strip_comment(line):
    return re.sub(r"(?<!\\)%.*$", "", line)


def resolve_input(name, root, here):
    """LaTeX resolves \\input paths from the main file's directory; fall back to the including file's."""
    name = name.strip()
    for base in (root, here):
        p = base / name
        for c in ([p] if p.suffix == ".tex" else [p.with_name(p.name + ".tex"), p]):
            if c.is_file():
                return c
    return None


def expand(path, seen=None, root=None, missing=None):
    path = Path(path)
    root = Path(root) if root else path.parent
    seen = seen if seen is not None else set()
    missing = missing if missing is not None else []
    if not path.exists() or path.resolve() in seen:
        return
    seen.add(path.resolve())
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = strip_comment(raw)
        m = re.search(r"\\(input|include|subfile)\{([^{}]+)\}", line)
        if m:
            yield (path.name, i, line[:m.start()])
            target = resolve_input(m.group(2), root, path.parent)
            if target is None:
                missing.append((f"{path.name}:{i}", m.group(2)))
            else:
                yield from expand(target, seen, root, missing)
            yield (path.name, i, line[m.end():])
        else:
            yield (path.name, i, line)


def dim_in(spec, ctx, body_pt):
    spec = spec.strip().replace(" ", "")
    m = re.match(r"^([\d.]*)\\(linewidth|columnwidth|textwidth|hsize)$", spec)
    if m:
        return float(m.group(1) or 1) * ctx[m.group(2)]
    m = re.match(r"^([\d.]+)(in|cm|mm|pt|bp|pc|em)$", spec)
    if m:
        v, u = float(m.group(1)), m.group(2)
        return v * (body_pt / 72.27) if u == "em" else v * UNITS[u]
    return None


def parse_opts(s):
    out = {}
    for part in (s or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip()
        elif part.strip():
            out[part.strip()] = True
    return out


# ------------------------------------------------------------------ graphics info
def pdf_info(path, scale):
    """Native size (in), effective font sizes, font types. Uses PyMuPDF, else poppler."""
    info = {"w": None, "h": None, "sizes": [], "types": set(), "approx": False, "engine": None}
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24
    except ImportError:
        try:
            import fitz  # older PyMuPDF
        except ImportError:
            fitz = None
    if fitz is not None:
        doc = fitz.open(str(path))
        page = doc[0]
        info["w"], info["h"] = page.rect.width / 72.0, page.rect.height / 72.0
        for b in page.get_text("dict").get("blocks", []):
            for line in b.get("lines", []):
                for sp in line.get("spans", []):
                    if sp.get("text", "").strip():
                        info["sizes"] += [sp["size"]] * max(1, len(sp["text"].strip()))
        for f in page.get_fonts(full=True):
            info["types"].add(f[2])
        info["engine"] = "PyMuPDF"
        return info
    raw = Path(path).read_bytes()
    m = re.search(rb"/CropBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", raw) or \
        re.search(rb"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*\]", raw)
    if m:
        x0, y0, x1, y1 = [float(v) for v in m.groups()]
        info["w"], info["h"] = (x1 - x0) / 72.0, (y1 - y0) / 72.0
    if shutil.which("pdffonts"):
        out = subprocess.run(["pdffonts", str(path)], capture_output=True, text=True).stdout
        for line in out.splitlines()[2:]:
            if "Type 3" in line:
                info["types"].add("Type3")
            elif line.strip():
                info["types"].add("other")
    if shutil.which("pdftotext"):
        out = subprocess.run(["pdftotext", "-bbox", "-f", "1", "-l", "1", str(path), "-"],
                             capture_output=True, text=True).stdout
        for m2 in re.finditer(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">([^<]*)</word>', out):
            wid = float(m2.group(3)) - float(m2.group(1))
            hgt = float(m2.group(4)) - float(m2.group(2))
            if len(m2.group(5).strip()) >= 2 and hgt > 1.3 * wid:  # rotated word: its box height is its length
                continue
            info["sizes"].append(hgt / 1.15)
        info["approx"] = True
        info["engine"] = "poppler（字号为估计值）"
    return info


def raster_info(path):
    try:
        from PIL import Image
    except ImportError:
        return None
    with Image.open(path) as im:
        w, h = im.size
        dpi = im.info.get("dpi", (72, 72))[0] or 72
    return {"px_w": w, "px_h": h, "dpi": float(dpi), "w": w / float(dpi), "h": h / float(dpi)}


def eps_info(path):
    txt = Path(path).read_text(errors="ignore")
    m = re.search(r"%%BoundingBox:\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", txt)
    if not m:
        return None
    x0, y0, x1, y1 = [float(v) for v in m.groups()]
    return {"w": (x1 - x0) / 72.0, "h": (y1 - y0) / 72.0}


def resolve_graphic(name, base, gpaths):
    cands = []
    for d in [base] + [base / g for g in gpaths]:
        p = d / name
        cands.append(p)
        if p.suffix == "":
            cands += [p.with_suffix(e) for e in (".pdf", ".png", ".jpg", ".jpeg", ".eps")]
    for c in cands:
        if c.exists() and c.is_file():
            return c
    return None


# ------------------------------------------------------------------------ LaTeX
TOKEN = re.compile(
    r"\\begin\{(?P<benv>[^{}]+)\}(?:\[[^\]]*\])?(?:\{(?P<barg>[^{}]*)\})?"
    r"|\\end\{(?P<eenv>[^{}]+)\}"
    r"|\\includegraphics\*?(?:\[(?P<gopt>[^\]]*)\])?\{(?P<gfile>[^{}]+)\}"
    r"|\\(?P<scal>resizebox|scalebox)\*?\{"
    r"|\\(?P<size>tiny|scriptsize|footnotesize|small|normalsize|large|Large)\b"
    r"|\\caption(?:\[[^\]]*\])?\{(?P<cap>[^{}]{0,80})"
    r"|\\label\{(?P<label>[^{}]+)\}")


def check_tex(main, a):
    v = VENUES.get(a.venue, VENUES["cvpr"])
    C = a.columnwidth_in or v["single"]
    T = a.textwidth_in or v["double"]
    if not v["twocol"] and not a.columnwidth_in:
        C = T
    body = a.body_pt or v["body_pt"]
    base = Path(main).parent
    missing = []
    lines = list(expand(main, missing=missing))
    doc = "\n".join(l for _, _, l in lines)
    gpaths = re.findall(r"\{([^{}]+)\}", (re.search(r"\\graphicspath\{(.*)\}", doc) or [None, ""])[1] or "")
    # map character offset -> file:line
    offsets, pos = [], 0
    for f, i, l in lines:
        offsets.append((pos, f"{f}:{i}"))
        pos += len(l) + 1

    def loc_of(off):
        lo = offsets[0][1]
        for o, loc in offsets:
            if o <= off:
                lo = loc
            else:
                break
        return lo

    start = doc.find("\\begin{document}")
    stack = [{"env": "document", "linewidth": C, "columnwidth": C, "textwidth": T, "hsize": C}]
    floats, cur = [], None
    for m in TOKEN.finditer(doc, max(0, start)):
        ctx = stack[-1]
        if m.group("benv"):
            env = m.group("benv")
            new = dict(ctx, env=env)
            if env in ("figure*", "table*"):
                new.update(linewidth=T, hsize=T)
            if env in ("minipage", "subfigure", "subtable") and m.group("barg"):
                w = dim_in(m.group("barg"), ctx, body)
                if w:
                    new.update(linewidth=w, columnwidth=w, textwidth=w, hsize=w)
            if env == "adjustbox" and cur is not None:
                cur["scaled"] = True
            stack.append(new)
            if env in ("figure", "figure*", "table", "table*", "wrapfigure"):
                cur = {"env": env, "loc": loc_of(m.start()), "label": "", "caption": "", "graphics": [],
                       "sizes": [], "scaled": False}
            continue
        if m.group("eenv"):
            env = m.group("eenv")
            if len(stack) > 1 and stack[-1]["env"] == env:
                stack.pop()
            if env in ("figure", "figure*", "table", "table*", "wrapfigure") and cur is not None:
                floats.append(cur)
                cur = None
            continue
        if cur is None:
            continue
        if m.group("gfile"):
            cur["graphics"].append((m.group("gopt") or "", m.group("gfile").strip(), dict(ctx), loc_of(m.start())))
        elif m.group("scal"):
            cur["scaled"] = True
        elif m.group("size"):
            cur["sizes"].append(m.group("size"))
        elif m.group("cap") is not None:
            cur["caption"] = re.sub(r"\\[a-zA-Z]+|[{}$]", "", m.group("cap")).strip()[:30]
        elif m.group("label"):
            cur["label"] = m.group("label")

    out = [f"# 图表字号检查：{Path(main).name}",
           "",
           f"版心：单栏 {C:.3f} in，通栏 {T:.3f} in；正文 {body:g} pt。图中文字应接近正文：轴标签约 {body - 1:g} pt，"
           f"刻度和图例不小于 {body - 2:g} pt，任何文字不小于 {a.min_pt:g} pt；插入宽度应等于出图宽度。",
           "", "## 图", "",
           "| 图 | 文件 | 插入宽度 | 原图宽度 | 缩放 | 图中实际字号（最小/中位/最大） | 结论 |",
           "|---|---|---|---|---|---|---|"]
    problems = []
    for fl in floats:
        if not fl["env"].startswith(("figure", "wrapfigure")):
            continue
        name = fl["label"] or fl["caption"] or fl["loc"]
        for opt, gfile, ctx, gloc in fl["graphics"]:
            o = parse_opts(opt)
            p = resolve_graphic(gfile, base, gpaths)
            if p is None:
                out.append(f"| {name} | {gfile} | | | | | 找不到文件 |")
                continue
            ext = p.suffix.lower()
            if ext == ".pdf":
                nat = pdf_info(p, 1.0)
            elif ext == ".eps":
                nat = eps_info(p) or {}
            else:
                nat = raster_info(p) or {}
            nw, nh = nat.get("w"), nat.get("h")
            ins = None
            if "width" in o:
                ins = dim_in(str(o["width"]), ctx, body)
            elif "height" in o and nw and nh:
                hh = dim_in(str(o["height"]), ctx, body)
                ins = nw * hh / nh if hh else None
            elif "scale" in o and nw:
                ins = nw * float(o["scale"])
            elif nw:
                ins = nw
            if ins and "height" in o and "width" in o and o.get("keepaspectratio") and nw and nh:
                hh = dim_in(str(o["height"]), ctx, body)
                if hh:
                    ins = min(ins, nw * hh / nh)
            verdict, fonts = [], ""
            scale = ins / nw if ins and nw else None
            if ext in (".pdf", ".eps"):
                if scale and abs(scale - 1) > 0.10:
                    verdict.append(f"插入时缩放到 {scale * 100:.0f}%，字号随之变化：按 {ins:.2f} in 宽重新出图")
                if ext == ".pdf":
                    sizes = [s * (scale or 1) for s in nat["sizes"]]
                    if sizes:
                        ss = sorted(sizes)
                        mn, md, mx = ss[0], statistics.median(ss), ss[-1]
                        p5 = ss[int(0.05 * (len(ss) - 1))]  # ignore a few sub/superscript glyphs
                        fonts = f"{mn:.1f} / {md:.1f} / {mx:.1f} pt" + ("（约）" if nat.get("approx") else "")
                        if p5 < a.min_pt:
                            verdict.append(f"有不少文字小于 {a.min_pt:g} pt（最小 {mn:.1f} pt）")
                        if md < body - 3:
                            verdict.append("整体字号明显小于正文")
                        elif md > body + 2:
                            verdict.append("整体字号明显大于正文")
                    else:
                        if nat.get("engine") is None:
                            fonts = "无法读取"
                            verdict.append("缺少 PyMuPDF 和 poppler，读不到字号：pip install pymupdf 后重跑")
                        else:
                            fonts = "无可提取文字"
                            verdict.append("文字已转曲或为位图，无法自动检查，需目测")
                    if "Type3" in nat["types"] or "Type 3" in nat["types"]:
                        verdict.append("含 Type 3 字体：matplotlib 设 pdf.fonttype=42 后重新出图")
            else:
                r = raster_info(p)
                if r and ins:
                    dpi = r["px_w"] / ins
                    fonts = f"位图 {dpi:.0f} dpi"
                    if dpi < 300:
                        verdict.append(f"有效分辨率 {dpi:.0f} dpi 偏低（应 ≥ 300）")
                    verdict.append("位图里的文字无法检查字号；数据图和框架图尽量用矢量 PDF")
            row = (f"| {name} | {p.name} | {ins:.2f} in |" if ins else f"| {name} | {p.name} | ? |")
            if ext in (".pdf", ".eps"):
                row += f" {nw:.2f} in |" if nw else " ? |"
                row += f" {scale * 100:.0f}% |" if scale else " ? |"
            else:
                rr = raster_info(p) or {}
                row += f" {rr.get('px_w', '?')} px | — |"
            row += f" {fonts} | {'；'.join(verdict) if verdict else '通过'} |"
            out.append(row)
            if verdict:
                problems.append(f"{name}（{gloc}）：{'；'.join(verdict)}")

    out += ["", "## 表", "", "| 表 | 位置 | 字号命令 | 整表缩放 | 结论 |", "|---|---|---|---|---|"]
    classes = []
    for fl in floats:
        if not fl["env"].startswith("table"):
            continue
        name = fl["label"] or fl["caption"] or fl["loc"]
        size = fl["sizes"][0] if fl["sizes"] else "normalsize"
        classes.append(size)
        verdict = []
        if fl["scaled"]:
            verdict.append("整表被缩放，字号不可控：删列、缩写或改通栏，去掉 \\resizebox")
        pt = SIZE_PT10.get(size, 10) * body / 10
        if pt < body - 2.5:
            verdict.append(f"\\{size} 约 {pt:.0f} pt，明显小于正文")
        out.append(f"| {name} | {fl['loc']} | \\{size} | {'是' if fl['scaled'] else '否'} | "
                   f"{'；'.join(verdict) if verdict else '通过'} |")
        if verdict:
            problems.append(f"{name}（{fl['loc']}）：{'；'.join(verdict)}")
    if len(set(classes)) > 1:
        cnt = {c: classes.count(c) for c in set(classes)}
        problems.append("各表字号不统一：" + "、".join(f"\\{k} × {v}" for k, v in cnt.items()) + "；全文统一为同一字号")
    for loc, name in missing:
        problems.append(f"找不到被 \\input/\\include 的文件 {name}（{loc}），其中的图表没有检查")
    out += ["", "## 需要修改", ""]
    out += [f"- {p}" for p in problems] if problems else ["- 无"]
    return "\n".join(out)


# ------------------------------------------------------------------------- Word
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def check_docx(path, a):
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    style_sz, based, default_sz = {}, {}, 10.0
    try:
        st = ET.fromstring(z.read("word/styles.xml"))
        d = st.find(f"{W}docDefaults/{W}rPrDefault/{W}rPr/{W}sz")
        if d is not None:
            default_sz = int(d.get(W + "val")) / 2
        for s in st.iter(W + "style"):
            sid = s.get(W + "styleId")
            sz = s.find(f"{W}rPr/{W}sz")
            if sz is not None:
                style_sz[sid] = int(sz.get(W + "val")) / 2
            b = s.find(W + "basedOn")
            if b is not None:
                based[sid] = b.get(W + "val")
    except KeyError:
        pass

    def sty_size(sid):
        seen = set()
        while sid and sid not in seen:
            seen.add(sid)
            if sid in style_sz:
                return style_sz[sid]
            sid = based.get(sid)
        return None

    def para_sizes(p):
        ps = p.find(f"{W}pPr/{W}pStyle")
        psz = sty_size(ps.get(W + "val")) if ps is not None else sty_size("Normal") or sty_size("a")
        out = []
        for r in p.iter(W + "r"):
            t = "".join(x.text or "" for x in r.iter(W + "t")).strip()
            if not t:
                continue
            sz = r.find(f"{W}rPr/{W}sz")
            if sz is not None:
                s = int(sz.get(W + "val")) / 2
            else:
                rs = r.find(f"{W}rPr/{W}rStyle")
                s = (sty_size(rs.get(W + "val")) if rs is not None else None) or psz or default_sz
            out += [s] * len(t)
        return out

    body = root.find(W + "body")
    body_sizes, tables = [], []
    for el in body:
        if el.tag == W + "p":
            ps = el.find(f"{W}pPr/{W}pStyle")
            sid = (ps.get(W + "val") if ps is not None else "") or ""
            if re.match(r"(heading|title|\d$)", sid, re.I):
                continue
            body_sizes += para_sizes(el)
        elif el.tag == W + "tbl":
            ts = []
            for p in el.iter(W + "p"):
                ts += para_sizes(p)
            tables.append(ts)
    n_img = len(root.findall(f".//{W}drawing"))
    mode = lambda xs: statistics.mode(xs) if xs else None  # noqa: E731
    bm = mode(body_sizes)
    out = [f"# 图表字号检查：{Path(path).name}", "",
           f"正文主字号：{bm} pt；文档中有 {n_img} 张图片（图片里的文字字号无法自动读取：按插入宽度出图并目测确认）。",
           "", "| 表 | 主字号 | 用到的字号 | 结论 |", "|---|---|---|---|"]
    problems, modes = [], []
    for i, ts in enumerate(tables, 1):
        if not ts:
            continue
        tm = mode(ts)
        modes.append(tm)
        used = sorted(set(ts))
        verdict = []
        if bm and tm < bm - 2:
            verdict.append(f"比正文小 {bm - tm:g} pt")
        if len(used) > 2:
            verdict.append("表内字号不统一")
        out.append(f"| 表 {i} | {tm} pt | {', '.join(map(str, used))} | {'；'.join(verdict) if verdict else '通过'} |")
        if verdict:
            problems.append(f"表 {i}：{'；'.join(verdict)}")
    if len(set(modes)) > 1:
        problems.append("各表主字号不统一：" + "、".join(f"{m} pt" for m in modes) + "；全文统一")
    out += ["", "## 需要修改", ""] + ([f"- {p}" for p in problems] if problems else ["- 无"])
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--venue", default="cvpr", choices=sorted(VENUES))
    ap.add_argument("--columnwidth-in", type=float)
    ap.add_argument("--textwidth-in", type=float)
    ap.add_argument("--body-pt", type=float)
    ap.add_argument("--min-pt", type=float, default=6.5)
    a = ap.parse_args(argv)
    p = Path(a.file)
    if p.suffix.lower() == ".docx":
        print(check_docx(p, a))
    elif p.suffix.lower() == ".tex":
        print(check_tex(p, a))
    else:
        sys.exit("支持 .tex 和 .docx")
    return 0


if __name__ == "__main__":
    sys.exit(main())
