#!/usr/bin/env python3
"""First-pass linter for method papers, Chinese and English.

    python paper_lint.py paper.tex | paper.md | paper.txt | paper.docx  [--json] [--max 300]
                         [--venue-type journal|conference] [--target-venue REGEX] [--norms venue_norms.json]
                         [--structure-severity 高|中|低] [--stats] [--year 2026]

It only finds suspects; every finding must be confirmed sentence by sentence
before anything is changed, and it never edits the file.

Rules (codes match references/writing.md):
  Q   question sentences in the body
  A   self-weakening wording about the proposed method
  B   hedging
  C   volunteering weaknesses for the reviewer
  E   process narration (we first tried / 经过多次尝试)
  I   before/after-modification wording, old/new versions
  J1  file names, paths, experiment IDs, code identifiers in prose
  J2  bullet or numbered lists in the body (one contributions list in the introduction is allowed)
  J3  citation order: numbers first appear in ascending order; ascending inside one bracket
  K   abstract states the method, the problem and the result
  S   structure and size: introduction, related work, references (count, recent, target venue), discussion

Structure checks follow the venue type (journal or conference), read from the document class unless
--venue-type is given; --norms points at research/writing_profile/venue_norms.json (written by lit_pool.py) to
replace the defaults; --target-venue is a regex for the journal the paper is going to; --stats prints the
measured structure as JSON (also works on text extracted from a published PDF with pdftotext, to measure exemplars).
"""
from __future__ import annotations

import argparse
import datetime
import os
import json
import re
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

SEV_ORDER = {"高": 0, "中": 1, "低": 2, "提示": 3}


@dataclass
class Unit:
    text: str
    loc: str
    section: str = ""
    kind: str = "prose"      # prose, list, table, caption, abstract, heading, refs
    line_starts: list = field(default_factory=list)  # [(offset, loc)] for multi-line paragraphs
    level: int = 0           # headings: 1 = top-level section, 2 = subsection, ...
    top: str = ""            # title of the top-level section the unit belongs to
    raw: str = ""            # LaTeX only: the text with the numbers inside math kept, for counting
    env: str = ""            # captions: "table" or "figure"

    def loc_at(self, off):
        loc = self.loc
        for o, l in self.line_starts:
            if o <= off:
                loc = l
            else:
                break
        return loc


@dataclass
class Finding:
    code: str
    sev: str
    loc: str
    section: str
    snippet: str
    msg: str


# ------------------------------------------------------------------------- readers
SKIP_ENVS = {"equation", "equation*", "align", "align*", "gather", "gather*", "multline", "multline*",
             "eqnarray", "eqnarray*", "displaymath", "math", "flalign", "flalign*", "alignat", "alignat*",
             "verbatim", "lstlisting", "minted", "algorithmic", "algorithm2e", "tikzpicture", "comment"}
TABLE_ENVS = {"tabular", "tabular*", "tabularx", "longtable", "array", "tabulary"}
LIST_ENVS = {"itemize", "enumerate", "description"}
TEX_LEVEL = {"chapter": 0, "section": 1, "subsection": 2, "subsubsection": 3, "paragraph": 4}
SECTION_RE = re.compile(r"\\(chapter|section|subsection|subsubsection|paragraph)\*?\s*(?:\[[^\]]*\])?\s*\{([^{}]*)\}")
INPUT_RE = re.compile(r"\\(input|include|subfile)\{([^{}]+)\}")


def strip_tex_comment(line):
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


def expand_tex(path, seen=None, root=None, missing=None):
    """Yield (file, lineno, text) for the document with \\input/\\include expanded.

    Unresolved inputs are appended to `missing` as (location, name)."""
    path = Path(path)
    if path.suffix == "" and not path.exists():
        path = path.with_suffix(".tex")
    root = Path(root) if root else path.parent
    seen = seen if seen is not None else set()
    missing = missing if missing is not None else []
    if path.resolve() in seen or not path.exists():
        return
    seen.add(path.resolve())
    for i, raw in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
        line = strip_tex_comment(raw)
        m = INPUT_RE.search(line)
        if m:
            before = line[:m.start()]
            if before.strip():
                yield (path.name, i, before)
            target = resolve_input(m.group(2), root, path.parent)
            if target is None:
                missing.append((f"{path.name}:{i}", m.group(2)))
            else:
                yield from expand_tex(target, seen, root, missing)
            after = line[m.end():]
            if after.strip():
                yield (path.name, i, after)
        else:
            yield (path.name, i, line)


def clean_tex(s):
    s = re.sub(r"\\(cite[a-zA-Z]*|ref|eqref|autoref|cref|Cref|label|url|includegraphics|input|include|"
               r"bibliography|bibliographystyle|graphicspath|vspace|hspace|footnotemark)\*?(\[[^\]]*\])*\{[^{}]*\}", " ", s)
    s = re.sub(r"\\href\{[^{}]*\}\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\$\$.*?\$\$|\$[^$]*\$|\\\(.*?\\\)", " MATH ", s)
    for _ in range(3):
        s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", s)
    s = s.replace("\\_", "_").replace("\\%", "%").replace("\\&", "&").replace("~", " ")
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    s = s.replace("{", "").replace("}", "")
    return re.sub(r"[ \t]+", " ", s)


def num_tex(s):
    """Like clean_tex, but keeps what is inside inline math, so that the numbers in a paragraph can be counted."""
    s = re.sub(r"\\(cite[a-zA-Z]*|ref|eqref|autoref|cref|Cref|label|url|includegraphics|input|include|"
               r"bibliography|bibliographystyle|graphicspath|vspace|hspace|footnotemark)\*?(\[[^\]]*\])*\{[^{}]*\}", " ", s)
    s = re.sub(r"\\(?:SI|qty)\s*(?:\[[^\]]*\])?\{([^{}]*)\}\{([^{}]*)\}", lambda m: m.group(1) + " " + (
        "%" if "percent" in m.group(2) else "dB" if "decibel" in m.group(2) else "ms" if "milli" in m.group(2) else ""), s)
    s = re.sub(r"\\num\s*(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", s)
    for a, b in (("\\%", "%"), ("\\times", "×"), ("\\pm", "±"), ("\\,", ""), ("\\;", " "), ("\\!", ""), ("\\ ", " "),
                 ("~", " "), ("$", " "), ("\\(", " "), ("\\)", " ")):
        s = s.replace(a, b)
    for _ in range(3):
        s = re.sub(r"\\[a-zA-Z]+\*?(?:\[[^\]]*\])?\{([^{}]*)\}", r"\1", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)
    return re.sub(r"[ \t]+", " ", s.replace("{", "").replace("}", ""))


def read_tex(path):
    missing = []
    lines = list(expand_tex(path, missing=missing))
    units, section, kind = [], "", "prose"
    env_stack = []
    started = not any("\\begin{document}" in l for _, _, l in lines)
    buf, starts = [], []
    preamble = []
    cites = []  # (keys list, loc)
    cite_secs = []  # (keys list, heading the citation sits under)
    fig_ref_secs = set()  # headings under which a figure is referenced
    pending = {"kind": None}

    def flush():
        nonlocal buf, starts
        if buf:
            text, ls = "", []
            for (loc, piece, _) in buf:
                ls.append((len(text), loc))
                text += piece + " "
            k = pending["kind"] or kind_now()
            env = ""
            if k == "caption":
                env = "table" if any(e.startswith(("table", "wraptable")) for e in env_stack) else "figure"
            units.append(Unit(text.strip(), ls[0][1], section, k, ls, raw=" ".join(r for _, _, r in buf), env=env))
        pending["kind"] = None
        buf, starts = [], []

    def kind_now():
        envs = [e for e in env_stack]
        if any(e in TABLE_ENVS for e in envs):
            return "table"
        if "abstract" in envs:
            return "abstract"
        if "thebibliography" in envs:
            return "refs"
        if any(e in LIST_ENVS for e in envs):
            return "list"
        return "prose"

    for f, i, line in lines:
        loc = f"{f}:{i}"
        if not started:
            preamble.append(line)
            if "\\begin{document}" in line:
                started = True
            continue
        for m in re.finditer(r"\\cite[a-zA-Z]*\*?(?:\[[^\]]*\])*\{([^{}]+)\}", line):
            if not any(e in SKIP_ENVS for e in env_stack):
                ks = [k.strip() for k in m.group(1).split(",") if k.strip()]
                before = SECTION_RE.search(line)
                sec_here = before.group(2).strip() if before and before.start() < m.start() else section
                cites.append((ks, loc))
                cite_secs.append((ks, sec_here))
        for m in re.finditer(r"\\(?:ref|cref|Cref|autoref|subref)\*?\{\s*(fig[^{}]*)\}", line, re.I):
            before = SECTION_RE.search(line)
            fig_ref_secs.add(before.group(2).strip() if before and before.start() < m.start() else section)
        tokens = re.split(r"(\\begin\{[^{}]+\}|\\end\{[^{}]+\}|\\item\b|\\caption\{)", line)
        for tok in tokens:
            if not tok:
                continue
            mb = re.match(r"\\begin\{([^{}]+)\}", tok)
            me = re.match(r"\\end\{([^{}]+)\}", tok)
            if mb:
                flush()
                env_stack.append(mb.group(1))
                continue
            if me:
                flush()
                if env_stack and env_stack[-1] == me.group(1):
                    env_stack.pop()
                elif me.group(1) in env_stack:
                    while env_stack and env_stack.pop() != me.group(1):
                        pass
                continue
            if tok == "\\item":
                flush()
                continue
            if tok == "\\caption{":
                flush()
                pending["kind"] = "caption"
                continue
            if any(e in SKIP_ENVS for e in env_stack):
                continue
            ms = SECTION_RE.search(tok)
            if ms:
                flush()
                section = ms.group(2).strip()
                units.append(Unit(section, loc, section, "heading", level=TEX_LEVEL[ms.group(1)]))
                tok = tok[ms.end():]
            if "\\end{document}" in tok:
                break
            if not tok.strip():
                if not line.strip():
                    flush()
                continue
            c = clean_tex(tok).strip()
            if c:
                buf.append((loc, c, num_tex(tok).strip()))
        if not line.strip():
            flush()
    flush()
    meta = {"preamble": "\n".join(preamble), "cites": cites, "cite_secs": cite_secs, "main": Path(path),
            "missing_inputs": missing, "fig_ref_secs": fig_ref_secs}
    # list detection: report each list environment once
    meta["list_envs"] = []
    sec, stack = "", []
    for f, i, line in lines:
        ms = SECTION_RE.search(line)
        if ms:
            sec = ms.group(2)
        for m in re.finditer(r"\\begin\{(itemize|enumerate|description)\}", line):
            meta["list_envs"].append((f"{f}:{i}", sec))
    return units, meta


HEADING_TXT = re.compile(r"^(#{1,6}\s+.+|(\d+(\.\d+)*|[一二三四五六七八九十]+[、.．])\s*\S.{0,40}|第[一二三四五六七八九十\d]+章.*|"
                         r"摘\s*要|Abstract|ABSTRACT|参考文献|References|REFERENCES|致\s*谢|Acknowledg(e)?ments?)$")
MD_LIST = re.compile(r"^\s*([-*+•](?=\s)|\d+[.)](?=\s)|\d+、|[（(]\d+[)）]|[①②③④⑤⑥⑦⑧⑨⑩])\s*")


ROMAN_HEAD = re.compile(r"^[IVX]{1,5}\.\s+[A-Z][A-Za-z &:,'\-]{2,60}$")
LETTER_HEAD = re.compile(r"^[A-Z]\.\s+[A-Z][A-Za-z0-9 &:,'\-/()]{3,70}$")
BACK_HEAD = re.compile(r"^(Appendix(\s+[A-Z])?\.?(\s+\S.*)?|APPENDIX(\s+[A-Z])?(\s+\S.*)?|Supplementary [Mm]aterials?|"
                       r"CRediT authorship contribution statement|Declaration of [Cc]ompeting [Ii]nterests?|"
                       r"Data [Aa]vailability( [Ss]tatement)?|Funding|Conflicts? of [Ii]nterest)$")
ROMAN_VAL = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12}


def squeeze_smallcaps(s):
    """pdftotext writes small-caps headings as 'R ELATED W ORK'; join the initials back."""
    return re.sub(r"\b([A-Z]) (?=[A-Z]{2,}\b)", r"\1", s)


NUM_HEAD = re.compile(r"^(\d{1,2}(?:\.\d{1,2}){0,3})\.?\s+([A-Z\u4e00-\u9fff][^=<>|{}]{1,68})$")


def txt_heading(s, in_refs, ieee, last_top):
    """(level, top-level number) of a plain-text heading line; level 0 when the line is not a heading.

    Numbered headings must continue the numbering (the next top-level number, or a sub-number of the current
    one), which keeps algorithm lines, table cells and equation fragments of a PDF text dump out."""
    if len(s) > 72 or re.search(r"[。．.!?！？;；,，:：]$", s):
        return 0, None
    q = squeeze_smallcaps(s)
    if ROMAN_HEAD.match(q):
        v = ROMAN_VAL.get(q.split(".")[0])
        if v and ((not ieee and v == 1) or (ieee and last_top is not None and v in (last_top + 1, last_top + 2))):
            return 1, v
        if not (ieee and LETTER_HEAD.match(s)):
            return 0, None
    if BACK_HEAD.match(q):
        return 1, None
    m = NUM_HEAD.match(q)
    if m:
        if ieee or in_refs:
            return 0, None
        nums = [int(x) for x in m.group(1).split(".")]
        if len(nums) == 1:
            ok = nums[0] in ((0, 1) if last_top is None else (last_top + 1, last_top + 2))
        else:
            ok = last_top is not None and nums[0] == last_top
        return (len(nums), nums[0]) if ok else (0, None)
    if re.match(r"^\d", q):
        return 0, None
    if HEADING_TXT.match(q):
        return 1, None
    if ieee and not in_refs and LETTER_HEAD.match(s):
        words = [w for w in s.split()[1:] if len(w) > 3]
        if len(s.split()) >= 3 and words and sum(w[0].isupper() for w in words) >= 0.6 * len(words):
            return 2, None
    return 0, None


def classify_heading(s):
    t = s.strip().lstrip("#").strip().lower()
    if re.match(r"^(摘\s*要|abstract)", t):
        return "abstract"
    if re.match(r"^(参考文献|references|bibliography)", t):
        return "refs"
    return None


def read_md_or_txt(path):
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    is_md = Path(path).suffix.lower() in (".md", ".markdown")
    raw_lines = text.splitlines()
    blank_paras = sum(1 for l in raw_lines if not l.strip()) > 0
    units, section, kind = [], "", "prose"
    in_code = False
    ieee, last_top = False, None
    # review copies carry a line number in front of every line; drop it
    nonblank = [l for l in raw_lines if l.strip()]
    numbered = not is_md and sum(1 for l in nonblank if re.match(r"^\s*\d{1,4}(\s{2,}\S|\s*$)", l)) >= 0.3 * max(1, len(nonblank))
    buf, starts = [], []

    def flush(k=None):
        nonlocal buf, starts
        if buf:
            t = ""
            ls = []
            for loc, piece in buf:
                ls.append((len(t), loc))
                t += piece + (" " if re.search(r"[A-Za-z0-9,.;:]$", piece) else "")
            units.append(Unit(t.strip(), ls[0][1], section, k or kind, ls))
        buf, starts = [], []

    for i, line in enumerate(raw_lines, 1):
        loc = f"第 {i} 行"
        if is_md and line.strip().startswith("```"):
            in_code = not in_code
            flush()
            continue
        if in_code:
            continue
        if numbered:
            if re.match(r"^\s*\d{1,4}\s*$", line):
                continue
            line = re.sub(r"^\s*\d{1,4}\s{2,}(?=\S)", "", line)
        s = line.strip()
        if not s:
            flush()
            continue
        if is_md:
            lvl, top = (len(s) - len(s.lstrip("#"))) if s.startswith("#") else 0, None
        else:
            lvl, top = txt_heading(s, kind == "refs", ieee, last_top)
        if lvl:
            flush()
            section = squeeze_smallcaps(s.lstrip("#").strip())
            ieee = ieee or (lvl == 1 and top is not None and bool(ROMAN_HEAD.match(section)))
            last_top = top if (top is not None and lvl == 1) else last_top
            kind = classify_heading(re.sub(r"^[IVX\d.]+\s+", "", section)) or classify_heading(section) or "prose"
            units.append(Unit(section, loc, section, "heading", level=lvl))
            continue
        if not is_md and re.match(r"^(摘\s*要|Abstract)\s*[:：—-]", s):
            flush()
            section, kind = "摘要", "abstract"
            s = re.sub(r"^(摘\s*要|Abstract)\s*[:：—-]\s*", "", s)
        if s.startswith("|"):
            flush()
            units.append(Unit(s, loc, section, "table"))
            continue
        if kind == "refs":
            flush()
            units.append(Unit(s, loc, section, "refs"))
            continue
        if MD_LIST.match(line) and kind != "refs":
            flush()
            units.append(Unit(MD_LIST.sub("", line).strip(), loc, section, "list"))
            continue
        buf.append((loc, s))
        if not blank_paras:
            flush()
    flush()
    return units, {"paragraphs_reliable": is_md}


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_styles(z):
    styles = {}
    try:
        root = ET.fromstring(z.read("word/styles.xml"))
    except KeyError:
        return styles
    for st in root.iter(W + "style"):
        sid = st.get(W + "styleId")
        name_el = st.find(W + "name")
        name = name_el.get(W + "val") if name_el is not None else sid
        ol = st.find(f"{W}pPr/{W}outlineLvl")
        styles[sid] = {"name": (name or "").lower(), "outline": ol is not None}
    return styles


def _docx_level(el, style_name, text):
    """Heading level from the style name (heading 2, 标题 2), the outline level, or the numbering in the text."""
    m = re.search(r"(\d)", style_name) if (style_name.startswith("heading") or style_name.startswith("标题")) else None
    if m:
        return int(m.group(1))
    ol = el.find(f"{W}pPr/{W}outlineLvl")
    if ol is not None and (ol.get(W + "val") or "").isdigit():
        return int(ol.get(W + "val")) + 1
    m = re.match(r"^(\d+(?:\.\d+)*)", text)
    return m.group(1).count(".") + 1 if m else 1


def read_docx(path):
    z = zipfile.ZipFile(path)
    styles = _docx_styles(z)
    root = ET.fromstring(z.read("word/document.xml"))
    body = root.find(W + "body")
    units, section, kind = [], "", "prose"
    sup_cites = []
    pidx = 0

    def para_text(p):
        out = []
        for r in p.iter(W + "r"):
            va = r.find(f"{W}rPr/{W}vertAlign")
            t = "".join((x.text or "") for x in r.iter(W + "t"))
            if va is not None and va.get(W + "val") == "superscript" and re.fullmatch(r"\s*\[?[\d,，\-–—~\s]+\]?\s*", t or ""):
                out.append("\u2063[" + t.strip().strip("[]") + "]\u2063")  # mark superscript citation
            else:
                out.append(t)
            if r.find(W + "tab") is not None:
                out.append("\t")
        return "".join(out)

    for el in body:
        if el.tag == W + "tbl":
            for p in el.iter(W + "p"):
                t = para_text(p).strip()
                if t:
                    units.append(Unit(t, f"表格（第 {pidx} 段之后）", section, "table"))
            continue
        if el.tag != W + "p":
            continue
        pidx += 1
        t = para_text(el).strip()
        if not t:
            continue
        ps = el.find(f"{W}pPr/{W}pStyle")
        sid = ps.get(W + "val") if ps is not None else ""
        st = styles.get(sid, {"name": (sid or "").lower(), "outline": False})
        is_heading = (st["name"].startswith("heading") or st["name"].startswith("标题") or st["name"] == "title"
                      or st["outline"] or el.find(f"{W}pPr/{W}outlineLvl") is not None)
        loc = f"第 {pidx} 段「{t[:10]}…」"
        if is_heading:
            section = t
            kind = classify_heading(t) or "prose"
            units.append(Unit(t, loc, section, "heading", level=_docx_level(el, st["name"], t)))
            continue
        if re.match(r"^(摘\s*要|Abstract)\s*[:：—-]", t):
            section, kind = "摘要", "abstract"
            t = re.sub(r"^(摘\s*要|Abstract)\s*[:：—-]\s*", "", t)
        elif re.fullmatch(r"(摘\s*要|abstract|参考文献|references)", t.strip().lower()):
            section, kind = t, classify_heading(t) or "prose"
            units.append(Unit(t, loc, section, "heading", level=1))
            continue
        is_list = el.find(f"{W}pPr/{W}numPr") is not None or "list" in st["name"] or "列表" in st["name"]
        is_caption = "caption" in st["name"] or "题注" in st["name"]
        k = kind if kind in ("refs", "abstract") else ("caption" if is_caption else ("list" if is_list else "prose"))
        if k == "prose" and MD_LIST.match(t):
            k = "list"
        units.append(Unit(t, loc, section, k))
    return units, {}


# --------------------------------------------------------------------------- rules
ZH_SELF = re.compile(r"本文|本方法|本研究|本模型|我们|所提|提出的方法|提出的模型|我们的方法")
EN_SELF = re.compile(r"\b(our|ours|we|us|the proposed|this (paper|work|method|approach|model))\b", re.I)
OTHERS = re.compile(r"现有|已有|传统|以往|主流|其他方法|其它方法|基线方法|这些方法|上述方法|前人|"
                    r"\b(existing|prior|previous|conventional|other methods|baselines?|these methods|they)\b", re.I)
CLAUSE_SPLIT = re.compile(r"[，,；;：:]")


def has_self(s):
    return bool(ZH_SELF.search(s) or EN_SELF.search(s))


def subject_is_self(sent, pos, mode):
    """mode 1: the clause (or the sentence before the match) must talk about the proposed method.
    mode 2: flag unless the clause before the match is about other methods."""
    cuts = [m.end() for m in CLAUSE_SPLIT.finditer(sent, 0, pos)]
    prefix = sent[cuts[-1] if cuts else 0:pos]
    if OTHERS.search(prefix):
        return False
    if mode == 2:
        return True
    return has_self(prefix) or has_self(sent[:pos])


MSG_A = "自我削弱：先判断是否必须讨论；必须保留时去掉削弱措辞、照实给出差距的数字，原因只写稿件已有或作者提供的解释，或收缩主张"
MSG_B = "过度对冲：有直接证据（对应的消融、分层或统计检验）时直接陈述并给出数字；证据只支持相关时保留限定或改成与证据相称的说法"
# (code, severity, regex, mode, message); mode 0 = always, 1 = about the proposed method, 2 = unless about other methods
RULES = [
    ("A", "高", r"遗憾的是|不幸的是|可惜的是|不得不承认|必须承认", 0, "自我削弱：删去这类措辞，事实照实陈述"),
    ("A", "高", r"\b(unfortunately|regrettably|admittedly|sadly)\b", 0, "自我削弱：删去这类措辞，事实照实陈述"),
    ("A", "高", r"仍(然)?(明显|大幅|显著)?落后|落后于|逊于|稍逊|略逊|略低于|略差于|不如|效果(有限|一般)|有限的(提升|改进|增益)|"
                r"存在(严重|明显|一定的?|以下|一些|若干|如下)?(不足|缺陷|局限)|未能|无法完全|难以完全|不可避免地|表现(较差|不佳|一般)", 1, MSG_A),
    ("A", "高", r"\b(still )?lags? behind\b|\bfalls? short\b|\bunderperforms?\b|\bslightly (worse|lower|inferior)\b|"
                r"\binferior to\b|\blimited (improvement|gain|performance)s?\b|\bfails? to\b|\bcannot fully\b|"
                r"\bnot as good as\b|\bsevere (limitation|drawback)s?\b", 1, MSG_A),
    ("A", "中", r"(仅|只)略?(高于|优于|领先|超过|提升|提高|改善|增加|获得|取得|带来|比)", 2,
     "“仅/只”削弱了结果：删去“仅”“略”，保留数字；若是强调优势（仅需、只用）可保留"),
    ("A", "低", r"略(高于|优于|好于|领先)|稍(高于|优于)", 2, "“略高于”弱化了优势：直接给出差值"),
    ("A", "低", r"\b(only|merely|just)\s+(improves?|increases?|gains?|outperforms?|achieves?|marginal|slightly|by)\b|"
                r"\bslightly (better|higher)\b|\bmarginal(ly)? (gains?|improvements?|better|higher)\b", 2,
     "“only/slightly”削弱了结果：直接给出差值；若是强调优势（only requires）可保留"),
    ("B", "中", r"在一定程度上|一定程度上|某种程度上", 0, MSG_B),
    ("B", "中", r"\bto (some|a certain) (extent|degree)\b|\bsomewhat\b", 0, MSG_B),
    ("B", "低", r"可能|或许|也许|似乎|有望|初步", 1, MSG_B),
    ("B", "低", r"\b(may|might|seems?|appears? to|preliminary|potentially|could potentially)\b", 1, MSG_B),
    ("C", "中", r"(本文方法|所提方法|我们的方法|本方法|本文|本模型)(仍)?(存在|有)(以下|一些|若干|如下)?(不足|局限|缺点|问题)|"
                r"局限性在于|不足之处在于", 0, "替审稿人总结弱点：删除，或改写为适用条件与扩展方向"),
    ("C", "低", r"需要指出的是|值得注意的是", 1, "常用来引出自我否定：确认后面不是在替审稿人总结弱点"),
    ("C", "中", r"\blimitations? of (our|this|the proposed)\b|\bour (method|approach|model) (has|suffers from|is limited)\b", 0,
     "替审稿人总结弱点：删除，或改写为适用条件与扩展方向"),
    ("C", "中", r"\bdrawbacks?\b|\bweakness(es)?\b|\bshortcomings?\b", 1, "替审稿人总结弱点：删除，或改写为适用条件与扩展方向"),
    ("E", "中", r"我们(首先|最初|起初|一开始|先)?(尝试|试图|试着|试验了)|经过(多次|反复|大量|若干)(尝试|实验|调试|调参)|后来(我们)?发现|"
                r"最初的(版本|方案|设计|模型)|起初|一开始|第[一二三四五六七八九十\d]+(版|个版本)|我们还(试过|尝试过)|走了弯路",
     0, "过程叙述：只保留最终成立的逻辑（问题 → 原因 → 本文做法）"),
    ("E", "中", r"\bwe (first |initially )?(tried|attempted|experimented with)\b|\bafter (several|many|multiple|numerous) "
                r"(attempts|trials|iterations|tries)\b|\binitially\b|\bat first\b|\bour (earlier|previous|first|initial) "
                r"(version|attempt|design|model)\b|\bit turned out\b|\btrial and error\b", 0,
     "过程叙述：只保留最终成立的逻辑（问题 → 原因 → 本文做法）"),
    ("I", "高", r"(改动|修改|改进)(前|后)(?!者)|改进前后|旧版本?|新版本|改进版", 0,
     "改动前后混写：指本文方法时直接用方法名；是对比时改成命名清楚的消融变体（如“w/o 某模块”）"),
    ("I", "高", r"\b(before|after) (the )?(modification|change|fix|improvement)s?\b|"
                r"\b(old|new|revised) version\b|\bours[-_ ]?v\d\b", 0,
     "改动前后混写：指本文方法时直接用方法名；是对比时改成命名清楚的消融变体（如“w/o 某模块”）"),
]
RULES = [(c, s, re.compile(p, re.I), n, m) for c, s, p, n, m in RULES]

FILE_RE = re.compile(r"(?<![\w.-])(?:[\w.-]+/)*[\w\-]+(?:\.[\w\-]+)*\.(?:py|ipynb|sh|bash|yaml|yml|json|jsonl|csv|tsv|txt|log|pth|pt|ckpt|"
                     r"safetensors|onnx|npy|npz|h5|hdf5|mat|pkl|pickle|ply|pcd|las|bag|db3|mcap|mp4|avi|mov|tar|zip|gz|"
                     r"cfg|ini|toml|cpp|cu|hpp|png|jpe?g|pdf|svg|eps|md|tex|bib|docx?|xlsx?|pptx?)\b", re.I)
PATH_RE = re.compile(r"(?:(?<=\s)|^)(?:\.{1,2}/|~/|/(?:home|mnt|data|root|usr|tmp|Users|opt|workspace)/|[A-Za-z]:\\)[^\s，。；,;]+")
EXPID_RE = re.compile(r"\b(exp|run|ckpt|trial|checkpoint)[_-]?\d+\b|第[一二三四五六七八九十\d]+次(实验|运行|训练)", re.I)
SNAKE_RE = re.compile(r"(?<![\\\w])[a-z][a-z0-9]*(?:_[a-z0-9]+){1,}\b")


PLACEHOLDER_RE = re.compile(r"\[\s*(?:TBD|TODO|FIXME|XXX|待补|待定|待填|占位)[^\]]{0,160}\]|\b(?:TBD|TODO|FIXME)\b|待补充")


def sentences(text):
    out, start = [], 0
    for m in re.finditer(r"[。！？!?；;]|\.(?=\s+[A-Z\u4e00-\u9fff]|$)", text):
        out.append((start, m.end()))
        start = m.end()
    if start < len(text):
        out.append((start, len(text)))
    return out


def snippet(text, a, b, pad=28):
    s = text[max(0, a - pad):min(len(text), b + pad)].replace("\n", " ")
    s = s.replace("\u2063", "")
    return ("…" if a > pad else "") + s.strip() + ("…" if b + pad < len(text) else "")


def is_intro(section):
    return bool(re.search(r"intro|引言|绪论|前言", section or "", re.I))


def is_limit_section(section):
    return bool(re.search(r"limitation|局限|不足", section or "", re.I))


def lint_units(units, meta, path, cfg=None):
    F = []
    for loc, name in meta.get("missing_inputs", []):
        F.append(Finding("J1", "提示", loc, "", name, "找不到被 \\input/\\include 的文件，这部分没有检查"))
    for u in units:
        if u.kind in ("heading", "refs"):
            continue
        t = u.text
        prose = u.kind in ("prose", "list", "caption", "abstract")
        if prose:
            for m in re.finditer(r"[?？]", t):
                if re.search(r"https?://\S*$", t[:m.start()]):
                    continue
                F.append(Finding("Q", "高", u.loc_at(m.start()), u.section, snippet(t, m.start(), m.end()),
                                 "正文出现疑问句：改成陈述句"))
            for m in PLACEHOLDER_RE.finditer(t):
                F.append(Finding("P", "高", u.loc_at(m.start()), u.section, snippet(t, m.start(), m.end(), 16),
                                 "稿件里留着占位符：补上内容；内容还没有时删掉这句，把缺的材料列给作者"))
        for a, b in sentences(t):
            sent = t[a:b]
            hits = {}  # code -> [severity, first offset, phrases, message]
            for code, sev, rx, mode, msg in RULES:
                if not prose and code != "I":  # tables: only the before/after check applies
                    continue
                for m in rx.finditer(sent):
                    if mode and not subject_is_self(sent, m.start(), mode):
                        continue
                    s2 = "提示" if code == "C" and is_limit_section(u.section) else sev
                    h = hits.setdefault(code, [s2, m.start(), [], msg])
                    if SEV_ORDER[s2] < SEV_ORDER[h[0]]:
                        h[0], h[3] = s2, msg
                    h[1] = min(h[1], m.start())
                    if m.group(0) not in h[2]:
                        h[2].append(m.group(0))
            if "A" in hits and "C" in hits and all(any(p in q for q in hits["C"][2]) for p in hits["A"][2]):
                del hits["A"]
            for code, (sev, off, phrases, msg) in hits.items():
                F.append(Finding(code, sev, u.loc_at(a + off), u.section, snippet(t, a, b, 0)[:90],
                                 f"{msg}（命中：{'、'.join(phrases)}）"))
        spans = []
        for rx, msg, sev in ((FILE_RE, "正文出现文件名：改用方法中的模块名或符号", "高"),
                             (PATH_RE, "正文出现路径：删除", "高"),
                             (EXPID_RE, "正文出现实验编号：删除或改成实验设置的描述", "中")):
            for m in rx.finditer(t):
                if any(not (m.end() <= s or m.start() >= e) for s, e in spans):
                    continue
                spans.append((m.start(), m.end()))
                F.append(Finding("J1", sev, u.loc_at(m.start()), u.section, snippet(t, m.start(), m.end(), 16), msg))
        if u.kind != "table":
            for m in SNAKE_RE.finditer(t):
                if any(not (m.end() <= s or m.start() >= e) for s, e in spans):
                    continue
                F.append(Finding("J1", "低", u.loc_at(m.start()), u.section, snippet(t, m.start(), m.end(), 16),
                                 "疑似代码标识符（下划线命名）：改成自然语言或方法符号"))
    F += check_lists(units, meta)
    F += check_citations(units, meta, path)
    F += check_abstract(units)
    F += check_structure(units, meta, path, cfg)
    F += check_numbers(units, meta, path, cfg)
    return F


def check_lists(units, meta):
    F = []
    if meta.get("list_envs") is not None:
        allowed = True
        for loc, sec in meta["list_envs"]:
            if is_intro(sec) and allowed:
                allowed = False
                continue
            F.append(Finding("J2", "中", loc, sec, "itemize/enumerate", "正文出现条目式列表：改成整段叙述（引言贡献点除外）"))
        return F
    blocks, cur = [], []
    for u in units:
        if u.kind == "list":
            cur.append(u)
        else:
            if cur:
                blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    allowed = True
    for b in blocks:
        if is_intro(b[0].section) and allowed:
            allowed = False
            continue
        F.append(Finding("J2", "中", b[0].loc, b[0].section, f"{len(b)} 条：{b[0].text[:30]}…",
                         "正文出现条目式分段描述：合并成整段叙述（引言贡献点除外）"))
    for u in units:
        if u.kind == "prose" and re.match(r"^(（\d+）|\(\d+\)|\d+）|[①②③④⑤⑥⑦⑧⑨⑩])", u.text):
            F.append(Finding("J2", "低", u.loc, u.section, u.text[:30] + "…", "段首编号的分段描述：考虑并入整段"))
    return F


ORDER_STYLES = {"unsrt", "unsrtnat", "ieeetr", "ieeetran", "ieeetrann", "elsarticle-num", "elsarticle-num-names",
                "gbt7714-numerical", "gbt7714-2005-numerical", "naturemag", "achemso", "unsrturl"}
# CVPR / ICCV (ieee_fullname, ieeenat_fullname) and ECCV (splncs04) sort alphabetically by default
ALPHA_STYLES = {"plain", "plainnat", "abbrv", "abbrvnat", "alpha", "ieee_fullname", "ieeenat_fullname", "apalike",
                "acm", "siam", "plainurl", "abbrvurl", "splncs04", "splncs"}
CITE_BRACKET = re.compile(r"\u2063?\[(\d+(?:\s*[-–—~]\s*\d+)?(?:\s*[,，、;；]\s*\d+(?:\s*[-–—~]\s*\d+)?)*)\]\u2063?")


def _expand(group):
    nums = []
    for part in re.split(r"[,，、;；]", group):
        part = part.strip()
        m = re.match(r"(\d+)\s*[-–—~]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            nums += list(range(a, b + 1)) if b >= a and b - a < 200 else [a, b]
        elif part.isdigit():
            nums.append(int(part))
    return nums


def _order_findings(seq):
    """seq: list of (numbers list, loc, section). Returns findings for order problems."""
    F, seen, maxn = [], set(), 0
    for nums, loc, sec in seq:
        if nums != sorted(nums):
            F.append(Finding("J3", "中", loc, sec, "[" + ", ".join(map(str, nums)) + "]",
                             "同一处的多篇引用没有按升序排列"))
        for n in nums:
            if n in seen:
                continue
            if n != maxn + 1:
                if maxn == 0:
                    msg = f"正文第一个引用编号是 [{n}]，应从 [1] 开始：参考文献按首次引用顺序编号"
                else:
                    msg = f"引用编号 [{n}] 首次出现时，前面最大编号是 [{maxn}]：参考文献应按首次引用顺序从小到大编号"
                F.append(Finding("J3", "高", loc, sec, f"[{n}]", msg))
            seen.add(n)
            maxn = max(maxn, n)
    return F, seen


def check_citations(units, meta, path):
    F = []
    if meta.get("cites") is not None:  # LaTeX
        pre = meta.get("preamble", "")
        m = re.search(r"\\bibliographystyle\{([^{}]+)\}", pre) or re.search(
            r"\\bibliographystyle\{([^{}]+)\}", "\n".join(u.text for u in units))
        main = meta["main"]
        if not m:
            for f, i, line in expand_tex(main):
                mm = re.search(r"\\bibliographystyle\{([^{}]+)\}", line)
                if mm:
                    m = mm
                    break
        bib_latex = re.search(r"\\usepackage\[([^\]]*)\]\{biblatex\}", pre)
        if bib_latex:
            opts = bib_latex.group(1)
            if "sorting=none" not in opts and not re.search(r"style=(ieee|numeric-comp|numeric)", opts):
                F.append(Finding("J3", "中", "导言区", "", "biblatex[" + opts + "]",
                                 "biblatex 未设置 sorting=none：编号可能不是按首次引用顺序"))
        alpha = False
        if m and not bib_latex:
            style = m.group(1).strip().lower()
            if style in ALPHA_STYLES:
                alpha = True
                F.append(Finding("J3", "提示", "导言区", "", f"\\bibliographystyle{{{style}}}",
                                 "该样式按作者字母排序（CVPR、ICCV、ECCV 模板默认如此），正文编号不会从小到大出现，"
                                 "这是模板行为，不逐条报错；投 IEEE、期刊或学位论文时改用按引用顺序编号的样式"))
            elif style not in ORDER_STYLES:
                F.append(Finding("J3", "提示", "导言区", "", f"\\bibliographystyle{{{style}}}",
                                 "无法判断该样式的排序方式：编译后在 PDF 里确认编号是否按首次引用递增"))
        if not re.search(r"\\usepackage(\[[^\]]*\])?\{cite\}", pre) and not re.search(
                r"\\usepackage\[[^\]]*sort[^\]]*\]\{natbib\}", pre) and not bib_latex:
            F.append(Finding("J3", "低", "导言区", "", "\\usepackage{cite}",
                             "未加载 cite 宏包（或 natbib 的 sort&compress）：同一处多篇引用不会自动升序压缩"))
        bbl = Path(main).with_suffix(".bbl")
        if bbl.exists() and not alpha:
            keys = re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^{}]+)\}", bbl.read_text(encoding="utf-8", errors="ignore"))
            num = {k.strip(): i + 1 for i, k in enumerate(keys)}
            seq = []
            for ks, loc in meta["cites"]:
                ns = [num[k] for k in ks if k in num]
                if ns:
                    seq.append((ns, loc, ""))
            sorted_inside = bool(re.search(r"\\usepackage(\[[^\]]*\])?\{cite\}", pre))
            f2, seen = _order_findings([(sorted(n) if sorted_inside else n, l, s) for n, l, s in seq])
            F += f2
            missing = sorted(set(num.values()) - seen)
            if missing and len(missing) < len(num):
                F.append(Finding("J3", "低", bbl.name, "", f"{len(missing)} 条", "参考文献列表中有条目未被正文引用"))
        return F
    seq = []
    for u in units:
        if u.kind in ("refs", "heading"):
            continue
        for m in CITE_BRACKET.finditer(u.text):
            nums = _expand(m.group(1))
            if nums:
                seq.append((nums, u.loc_at(m.start()), u.section))
    if seq:
        f2, seen = _order_findings(seq)
        F += f2
        refs = [u for u in units if u.kind == "refs"]
        listed = []
        for u in refs:
            mm = re.match(r"^\s*\[?(\d+)[\]\.．、)]", u.text)
            if mm:
                listed.append((int(mm.group(1)), u.loc))
        if listed:
            nums = [n for n, _ in listed]
            if nums != list(range(1, len(nums) + 1)):
                F.append(Finding("J3", "高", listed[0][1], "参考文献", f"{nums[:8]}…",
                                 "参考文献列表编号不是从 1 开始连续递增"))
            extra = sorted(seen - set(nums))
            if extra:
                F.append(Finding("J3", "高", "正文", "", str(extra[:10]), "正文引用了参考文献列表里没有的编号"))
            uncited = sorted(set(nums) - seen)
            if uncited:
                F.append(Finding("J3", "低", "参考文献", "", str(uncited[:10]), "这些参考文献在正文中没有被引用"))
    return F


METHOD_CUE = re.compile(r"提出|设计了|构建了|本文方法|we (propose|present|introduce|develop|design)|this (paper|work) "
                        r"(proposes|presents|introduces)|\b(named|called|dubbed)\b", re.I)
PROBLEM_CUE = re.compile(r"然而|但是|但|难以|无法|忽略|忽视|假设|失效|受限|瓶颈|不足|困难|挑战|难点|问题|失衡|偏差|however|fail|ignore|overlook|assum|suffer|"
                         r"struggle|limited|challenge|break", re.I)
RESULT_CUE = re.compile(r"\d+(\.\d+)?\s*(%|％|dB|ms|FPS|×|x\b|倍|个百分点|pp\b|mAP|NDS|mIoU|PSNR|SSIM)|"
                        r"(提升|提高|降低|减少|优于|超过|达到|领先)[^。；]{0,12}\d|"
                        r"(outperform|improv|reduc|surpass|achiev)[^.;]{0,40}\d", re.I)


def check_abstract(units):
    ab = [u for u in units if u.kind == "abstract"]
    if not ab:
        return [Finding("K", "提示", "全文", "", "", "没有识别到摘要：确认摘要说清了方法、问题和结果")]
    text = " ".join(u.text for u in ab)
    loc = ab[0].loc
    F = []
    if not METHOD_CUE.search(text):
        F.append(Finding("K", "高", loc, "摘要", text[:40] + "…", "摘要没有明确说出“本文提出了什么方法”"))
    if not PROBLEM_CUE.search(text):
        F.append(Finding("K", "中", loc, "摘要", text[:40] + "…", "摘要没有点出现有方法的缺口或失效机制"))
    if not RESULT_CUE.search(text):
        F.append(Finding("K", "高", loc, "摘要", text[:40] + "…", "摘要没有给出具体结果（数字、数据集、比较对象）"))
    first = re.split(r"[。.!?！？]", text.strip(), maxsplit=1)[0]
    if re.search(r"我们(首先|使用|采用|基于)|we (first|use|adopt|build on)|本文(首先|使用|采用)", first, re.I):
        F.append(Finding("K", "中", loc, "摘要", first[:40] + "…", "摘要从实现细节或过程开头：先讲重要问题和现有缺口"))
    F += abstract_profile(text, loc)
    return F


def abstract_profile(text, loc):
    """Compare an English abstract with the corpus profile in references/writing-patterns.md."""
    letters = sum(ch.isascii() and ch.isalpha() for ch in text)
    if letters < 0.6 * max(1, len(text.replace(" ", ""))):
        return []  # Chinese abstract: the corpus statistics are for English abstracts
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus"))
        from features import features  # noqa: E402
    except Exception:  # noqa: BLE001
        return []
    f = features("", text)
    F = []
    summary = (f"{f['words']} 词、{f['n_sent']} 句，步骤顺序 {f['moves_c']}"
               f"（B 背景 / G 缺口 / M 方法 / R 结果 / C 结论；语料常见 BGMR，中位数 178 词、8 句）")
    F.append(Finding("K", "提示", loc, "摘要", summary, "摘要画像，对照 references/writing-patterns.md 第 2 节"))
    if f["words"] < 140 or f["words"] > 230:
        F.append(Finding("K", "低", loc, "摘要", f"{f['words']} 词", "摘要长度偏离语料常态（四分位 156–201 词）"))
    if f["has_M"] and not f["gap_before_method"]:
        F.append(Finding("K", "低", loc, "摘要", f["moves_c"], "提出方法之前没有缺口句；69% 的方法论文先写现有方法的缺口"))
    if f["has_G"] and not f["gap_cause"]:
        F.append(Finding("K", "低", loc, "摘要", f["moves_c"],
                         "缺口句没有写出原因（because、due to、stems from 等）；写出失效原因，摘要才能和关口一的失效机制对上"))
    if not f["quant_result"]:
        F.append(Finding("K", "低", loc, "摘要", "", "结果句没有具体数字；只有 27% 的摘要给数字，高引论文更常给"))
    if f["question_n"]:
        F.append(Finding("K", "中", loc, "摘要", f"{f['question_n']} 处", "摘要里有疑问句（语料中只有 1.9% 的摘要这样写）"))
    return F


# --------------------------------------------------------------------------- structure
RELATED_RE = re.compile(r"related\s+works?|prior\s+work|previous\s+work|literature|相关工作|相关研究|研究现状|文献综述|国内外", re.I)
DISCUSS_RE = re.compile(r"^\s*(?:[IVX\d.]+\s+)?(discussions?|讨论)\s*$", re.I)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# Defaults per venue type. Journal numbers follow IEEE Transactions practice (references/writing-patterns.md,
# section 13); a venue_norms.json from lit_pool.py or the exemplars replaces them.
NORMS = {
    "journal": {"intro_words": 700, "intro_paragraphs": 5, "related_words": 650, "related_subsections": 3,
                "related_cites": 25, "refs": 40, "recent_refs": 10, "venue_refs": 4, "venue_recent_refs": 2,
                "discussion_words": 150, "sev": "高"},
    "conference": {"intro_words": 450, "intro_paragraphs": 4, "related_words": 300, "related_subsections": 2,
                   "related_cites": 15, "refs": 30, "recent_refs": 8, "venue_refs": 0, "venue_recent_refs": 0,
                   "discussion_words": 120, "sev": "中"},
}


def size_words(text):
    """English words plus Chinese characters converted at 1.6 characters per word."""
    cjk = len(CJK_RE.findall(text))
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", CJK_RE.sub(" ", text))) + round(cjk / 1.6)


def assign_tops(units):
    """Mark every unit with its top-level section. The level of the introduction's heading is taken as the section
    level, so a document title above it (# Title, then ## Introduction) does not swallow the whole paper."""
    heads = [u for u in units if u.kind == "heading"]
    intro = next((u for u in heads if is_intro(u.text)), None)
    base = intro.level if intro else (min(u.level for u in heads) if heads else 0)
    cur = ""
    for u in units:
        if u.kind == "heading" and u.level <= base:
            cur = u.text
        u.top = cur
    return base


def detect_venue_type(meta):
    pre = meta.get("preamble", "")
    m = re.search(r"\\documentclass(?:\[([^\]]*)\])?\{([^{}]+)\}", pre)
    if not m:
        return None
    opts, cls = (m.group(1) or "").lower(), m.group(2).lower()
    if cls in ("elsarticle", "svjour3", "sn-jnl", "iopart", "mdpi", "frontiersin") or "journal" in opts:
        return "journal"
    if cls == "ieeetran":
        return "conference" if "conference" in opts else "journal"
    if cls in ("llncs", "acmart") or re.search(r"\\usepackage(?:\[[^\]]*\])?\{(cvpr|iccv|eccv|wacv|neurips[_\d]*|icml\d*|"
                                                  r"iclr\d*_conference|corl[_\d]*|aaai\d*|ieeeconf|bmvc\w*)\}", pre, re.I):
        return "conference"
    return None


def bib_entries(main):
    """key -> (year, venue text) from the .bib files the document names (or the .bbl next to it)."""
    main = Path(main)
    src = "\n".join(l for _, _, l in expand_tex(main))
    names = []
    for m in re.finditer(r"\\(?:bibliography|addbibresource)\{([^{}]+)\}", src):
        names += [n.strip() for n in m.group(1).split(",") if n.strip()]
    out = {}
    for n in names:
        f = main.parent / (n if n.endswith(".bib") else n + ".bib")
        if not f.exists():
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", txt, re.S):
            body = m.group(3)
            y = re.search(r"\byear\s*=\s*[{\"]?\s*(\d{4})", body, re.I)
            v = None
            for fld in ("journal", "journaltitle", "booktitle", "howpublished", "publisher"):   # the venue, in this order
                v = re.search(rf"\b{fld}\s*=\s*[{{\"](.+?)[}}\"]\s*,?\s*(?:\n|$)", body, re.I | re.S)
                if v:
                    break
            arx = "arXiv" if re.search(r"arxiv", body, re.I) else ""
            out[m.group(2)] = (int(y.group(1)) if y else None, re.sub(r"\s+", " ", v.group(1)) if v else arx)
    if not out:
        bbl = main.with_suffix(".bbl")
        if bbl.exists():
            txt = bbl.read_text(encoding="utf-8", errors="ignore")
            for m in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^{}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\})", txt, re.S):
                ys = re.findall(r"\b(19\d\d|20\d\d)\b", m.group(2))
                out[m.group(1).strip()] = (int(ys[-1]) if ys else None, re.sub(r"\s+", " ", m.group(2)))
    return out


def structure_stats(units, meta, path, year=None, target_venue=None):
    """Measured structure of the manuscript: section sizes, the introduction, related work and the references."""
    year = year or datetime.date.today().year
    base = assign_tops(units)
    tv = re.compile(target_venue, re.I) if target_venue else None
    tops, order = {}, []
    for u in units:
        if u.kind == "heading":
            if u.level <= base and u.text not in tops:
                tops[u.text] = {"name": u.text, "words": 0, "paragraphs": 0, "subsections": 0, "list_items": 0,
                                "figure": False, "text": []}
                order.append(u.text)
            elif u.top in tops and u.level == base + 1:
                tops[u.top]["subsections"] += 1
            continue
        s = tops.get(u.top)
        if s is None or u.kind in ("refs", "table"):
            continue
        if u.kind == "caption":
            s["figure"] = True
            continue
        s["words"] += size_words(u.text)
        s["text"].append(u.text)
        if u.kind == "prose":
            s["paragraphs"] += 1
        elif u.kind == "list":
            s["list_items"] += 1
        if re.search(r"\bFig(?:ure|\.)|图\s*\d", u.text):
            s["figure"] = True

    def pick(pred):
        return next((tops[n] for n in order if pred(n)), None)
    intro, related = pick(is_intro), pick(lambda n: bool(RELATED_RE.search(n)))
    discussion = pick(lambda n: bool(DISCUSS_RE.match(n)))
    st = {"sections": [{k: tops[n][k] for k in ("name", "words", "paragraphs", "subsections")} for n in order],
          "paragraphs_reliable": meta.get("paragraphs_reliable", True), "year": year}
    if intro:
        txt = " ".join(intro["text"])
        enum = len(set(re.findall(r"(?:^|[\s(（])(?:\(?([1-4])[)）]|(First|Second|Third|Fourth)\b,?)", txt)))
        st["intro"] = {"words": intro["words"], "paragraphs": intro["paragraphs"], "figure": intro["figure"],
                       "contribution_items": intro["list_items"] or (enum if enum >= 2 else 0),
                       "contribution_list": intro["list_items"] >= 2,
                       "contribution_stated": bool(intro["list_items"] >= 2 or enum >= 2
                                                   or re.search(r"\bcontributions?\b|贡献", txt, re.I))}
    if related:
        st["related"] = {"words": related["words"], "subsections": related["subsections"]}
    if discussion:
        st["discussion"] = {"name": discussion["name"], "words": discussion["words"]}
    # references
    refs = {}
    if meta.get("cites") is not None:  # LaTeX: what is cited, looked up in the .bib or .bbl
        bib = bib_entries(meta["main"])
        cited = []
        for ks, _ in meta["cites"]:
            cited += [k for k in ks if k not in cited]
        info = [bib.get(k, (None, "")) for k in cited]
        refs = {"count": len(cited), "years_known": sum(1 for y, _ in info if y)}
        sec_top = {}
        for u in units:
            if u.kind == "heading":
                sec_top[u.text] = u.top
        if related:
            rel = set()
            for ks, sec in meta.get("cite_secs", []):
                if sec_top.get(sec, sec) == related["name"]:
                    rel.update(ks)
            st["related"]["cites"] = len(rel)
        if intro and any(sec_top.get(s, s) == intro["name"] for s in meta.get("fig_ref_secs", ())):
            st["intro"]["figure"] = True
    else:  # plain text or Word: the numbered reference list
        entries, cur = [], None
        ref_units = [u for u in units if u.kind == "refs"]
        bracket = any(re.match(r"^\s*\[\d+\]", u.text) for u in ref_units)
        for u in ref_units:
            m = re.match(r"^\s*\[(\d+)\]\s*\S", u.text) if bracket else re.match(r"^\s*(\d+)[.、]\s+\S", u.text)
            if m and int(m.group(1)) == len(entries) + 1:
                cur = [u.text]
                entries.append(cur)
            elif cur is not None:
                cur.append(u.text)
        info = []
        for e in entries:
            txt = " ".join(e)
            ys = re.findall(r"\b(19\d\d|20\d\d)\b", txt)
            ys = [int(y) for y in ys if int(y) <= year + 1]
            info.append((max(ys) if ys else None, txt))
        refs = {"count": len(entries), "years_known": sum(1 for y, _ in info if y)}
        if related:
            nums = set()
            for txt in related["text"]:
                for m in CITE_BRACKET.finditer(txt):
                    nums.update(_expand(m.group(1)))
            st["related"]["cites"] = len(nums)
    refs["recent"] = sum(1 for y, _ in info if y and y >= year - 1)
    if tv:
        refs["venue"] = sum(1 for _, v in info if v and tv.search(squeeze_ws(v)))
        refs["venue_recent"] = sum(1 for y, v in info if v and tv.search(squeeze_ws(v)) and y and y >= year - 1)
    st["refs"] = refs
    return st


def squeeze_ws(s):
    return re.sub(r"\s+", " ", s)


def load_norms(venue_type, norms_file):
    n = dict(NORMS.get(venue_type or "", {}))
    if norms_file and Path(norms_file).exists():
        try:
            data = json.loads(Path(norms_file).read_text(encoding="utf-8"))
        except ValueError:
            data = {}
        n.update({k: v for k, v in data.get("targets", {}).items() if isinstance(v, (int, float))})
        n["_source"] = str(norms_file)
        n["_target_venue"] = data.get("target_venue_regex")
        n["_venue_type"] = data.get("venue_type")
        n["_median_refs"] = data.get("measured", {}).get("median_refs")
        n["_venue"] = data.get("venue")
    return n


def check_structure(units, meta, path, cfg):
    cfg = cfg or {}
    vt = cfg.get("venue_type") or None
    norms_probe = load_norms(None, cfg.get("norms"))
    vt = vt or norms_probe.get("_venue_type") or detect_venue_type(meta)
    n = load_norms(vt, cfg.get("norms"))
    target = cfg.get("target_venue") or n.get("_target_venue")
    st = structure_stats(units, meta, path, cfg.get("year"), target)
    meta["structure"] = st
    y0 = st["year"] - 1
    F = []
    parts = []
    if "intro" in st:
        parts.append(f"引言 {st['intro']['words']} 词" + (f"、{st['intro']['paragraphs']} 段" if st["paragraphs_reliable"] else ""))
    if "related" in st:
        parts.append(f"相关工作 {st['related']['words']} 词、{st['related']['subsections']} 小节、引用 {st['related'].get('cites', 0)} 篇")
    r = st["refs"]
    parts.append(f"参考文献 {r['count']} 条（{y0} 年以来 {r['recent']} 条" + (f"，目标期刊 {r['venue']} 条、其中近两年 {r['venue_recent']} 条" if target else "") + "）")
    kind = {"journal": "期刊稿", "conference": "会议稿"}.get(vt, "未识别稿件类型，只报告数字；用 --venue-type 指定后才按门槛检查")
    F.append(Finding("S", "提示", "全文", "", "；".join(parts), f"结构与篇幅（{kind}"
                     + (f"，门槛来自 {n['_source']}" if n.get("_source") else "")
                     + (f"；{n.get('_venue') or '目标期刊'}同主题论文的参考文献数中位数是 {n['_median_refs']}" if n.get("_median_refs") else "") + "）"))
    if not vt:
        return F
    sev = cfg.get("severity") or n["sev"]

    def low(name, have, need, how):
        if need and have < need:
            F.append(Finding("S", sev, "全文", name, f"{have}（门槛 {need}）", how))
    if "intro" in st:
        i = st["intro"]
        low("引言", i["words"], n["intro_words"], "引言偏短：按“背景 → 现有思路 → 普遍问题 → 本文的核心想法 → 方法与实验概述 → "
            "三点贡献”补齐（writing.md 第 4 节）")
        if st["paragraphs_reliable"]:
            low("引言", i["paragraphs"], n["intro_paragraphs"], "引言段数偏少：现有研究的思路、存在的问题、本文的核心想法各自成段")
        if not i["contribution_stated"]:
            F.append(Finding("S", sev, "全文", "引言", "没有贡献点", "引言末尾列出三条贡献，条目式，每条对应方法的一个部分和一组实验"))
        elif not i["contribution_list"]:
            F.append(Finding("S", "中", "全文", "引言", "贡献点写在段落里", "贡献点改成条目式的三条，互不重复、逐条递进，每条对应方法的一个部分和一组实验"))
        elif i["contribution_items"] not in (3, 4):
            F.append(Finding("S", "中", "全文", "引言", f"贡献点 {i['contribution_items']} 条", "贡献点收成三条，互不重复、逐条递进"))
        if not i["figure"]:
            F.append(Finding("S", "中", "全文", "引言", "没有引用图", "引言配一张动机图：把现有做法和本文的想法画出来，突出区别"))
    else:
        F.append(Finding("S", "提示", "全文", "", "", "没有识别到引言"))
    if "related" in st:
        rl = st["related"]
        low("相关工作", rl["words"], n["related_words"], "相关工作偏短：按技术路线分小节，把每条路线的代表工作讲全、讲出递进关系，"
            "每小节最后落到它留下的问题")
        low("相关工作", rl["subsections"], n["related_subsections"], "相关工作小节偏少：每条相邻的技术路线一个小节")
        low("相关工作", rl.get("cites", 0), n["related_cites"], "相关工作引用偏少：从文献池补全每条路线的代表工作和近两年的工作")
    else:
        F.append(Finding("S", sev, "全文", "", "没有相关工作一节", "单独写相关工作一节"))
    low("参考文献", r["count"], n["refs"], "参考文献偏少：用 lit_pool.py 建文献池，补到门槛以上；每条都要是核实过的条目")
    if r["years_known"] >= 0.6 * max(1, r["count"]):
        low("参考文献", r["recent"], n["recent_refs"], f"{y0} 年以来的文献偏少：补近两年的相关工作")
    if target:
        low("参考文献", r["venue"], n["venue_refs"], "目标期刊的文献偏少：补引目标期刊上的相关论文")
        low("参考文献", r["venue_recent"], n["venue_recent_refs"], f"目标期刊 {y0} 年以来的论文引得偏少：用 lit_pool.py venue 查近两年的相关论文")
    elif vt == "journal":
        F.append(Finding("S", "提示", "全文", "参考文献", "", "没有指定目标期刊（--target-venue 或 venue_norms.json），未检查目标期刊近两年论文的引用数"))
    if "discussion" in st and st["discussion"]["words"] < n["discussion_words"]:
        F.append(Finding("S", sev, "全文", st["discussion"]["name"], f"{st['discussion']['words']} 词",
                         "讨论一节太短：并入实验分析或结论，或者删掉；要保留就写成有实质内容的分析"))
    return F


# --------------------------------------------------------------------------- numbers (rule D)
# How published papers use numbers, measured on 28 full texts (references/writing-patterns.md, section 14):
# experiments prose carries a median of 0.8 measured values per 100 words (nine in ten papers stay under 1.3),
# a five-sentence stretch with five or more values is rare (5% of stretches), abstracts carry 0-1 result values
# (at most 3), figure captions run to about 40 words and table captions to about 20.
VAL_UNIT = r"%|％|×|x\b|dB|ms|fps|FPS|MB|GB|KB|Hz|pp\b|倍|个百分点|毫秒"
NUM_RE = re.compile(r"(?<![A-Za-z\d.,\-])[+−-]?\d+(?:[.,]\d+)*\s?(?:" + VAL_UNIT + r")?(?![A-Za-z\d])")
NUM_SKIP = re.compile(
    r"\[[\d,\s–\-]+\]|\b(?:Tables?|Tab\.|Figures?|Figs?\.|Sections?|Sec\.|Eqs?\.|Equations?|Algorithms?|Alg\.|Appendix|"
    r"App\.|Steps?|Stage|Phase|Rows?|Columns?)\s*\(?[A-Z]?[IVX\d]+[a-z]?\)?(?:\s*(?:and|,|–|-)\s*\(?[\dIVX]+[a-z]?\)?)*|"
    r"[表图式节第]\s*\d+(?:\.\d+)*|\b[A-Za-z]+[-–]\d+\b|\b(?:19|20)\d\d\b")
NEG_RE = re.compile(r"\b(not|cannot|rather than|without|neither|no longer|does not|do not|did not)\b", re.I)
EXP_START = re.compile(r"^(experiments?|experimental|empirical|evaluations?|results|benchmarks?|performance evaluation|"
                       r"实验|结果|评估|评测)", re.I)
EXP_STOP = re.compile(r"^(conclusions?|concluding|discussions?|limitations?|related works?|acknowledge?ments?|references|"
                      r"bibliography|broader impact|future work|appendix|supplementary|结论|总结|讨论|局限|相关工作|致谢|"
                      r"参考文献|附录)", re.I)
SETUP_NAME = re.compile(r"setup|setting|implementation|dataset|protocol|metric|detail|baseline|compared method|"
                        r"hyper-?parameter|配置|设置|实现|数据集|指标|细节|对比方法|超参", re.I)
ABS_DETAIL = re.compile(r"\[\s*[-+−]?\d[\d.]*\s*,\s*[-+−]?\d[\d.]*\s*\]|\b9[059]\s?%\s*(?:confidence|interval|CI)|"
                        r"confidence interval|\bseeds?\b|bootstrap|\bpaired\b|置信区间|种子", re.I)
PDF_SEC = re.compile(
    r"^(introduction|related works?|background|preliminar(y|ies)|problem (statement|formulation|setup)|overview|"
    r"(the |our |proposed )?(method|methods|methodology|approach|framework|model)\b.{0,40}|"
    r"experiments?\b.{0,40}|experimental\b.{0,40}|evaluations?\b.{0,40}|results\b.{0,40}|benchmarks?\b.{0,30}|"
    r"performance evaluation|ablation.{0,30}|analysis|discussions?\b.{0,40}|limitations?\b.{0,40}|"
    r"conclusions?\b.{0,50}|future work|broader impact.{0,20}|acknowledge?ments?|references|bibliography|"
    r"appendix.{0,40}|supplementary.{0,40})$", re.I)
PDF_CAPTION = re.compile(r"^(Table|TABLE|Tab\.|Fig\.|Figure|FIGURE)\s*([IVX]+|\d+)\s*([.:|]\s|[.:|]?$)")
PDF_NOISE = re.compile(r"has been accepted for publication|Authorized licensed use|content may change prior to final "
                       r"publication|Personal use is permitted|^Citation information: DOI|©\s?20\d\d IEEE|"
                       r"Permission to make digital")
EN_ABBR = re.compile(r"\b(Fig|Figs|Tab|Eq|Eqs|Sec|e\.g|i\.e|et al|vs|cf|resp|approx|w\.r\.t|No|Ref)\.\s", re.I)
NUM_NORMS = {"exp_values_per_100w": 2.0, "abstract_values": 3, "block_values": 5, "block_density": 4.0,
             "figure_caption_words": 80, "table_caption_words": 60, "negation_share": 0.15, "exp_share": 0.42}


def numerals(s):
    return [m.group(0).strip() for m in NUM_RE.finditer(NUM_SKIP.sub(" ", s))]


def is_value(n):
    """A measured value rather than a count: it has decimals, a percent sign or a unit."""
    return bool(re.search(r"\.\d|%|％|×|x$|dB|ms|fps|FPS|MB|GB|KB|倍|个百分点|毫秒", n))


def values_in(s):
    return [n for n in numerals(s) if is_value(n)]


def n_decimals(n):
    m = re.search(r"\.(\d+)", n)
    return len(m.group(1)) if m else 0


def en_sentences(text):
    t = EN_ABBR.sub(lambda m: m.group(0).replace(". ", ".§"), text)
    parts = re.split(r"(?<=[.!?。！？])\s*(?=[A-Z(\[“\"一-鿿])", t)
    return [p.replace("§", " ").strip() for p in parts if len(p.split()) >= 4 or len(CJK_RE.findall(p)) >= 8]


def _median(xs):
    xs = sorted(xs)
    return None if not xs else (xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2)


def pdf_heading(line):
    s = squeeze_smallcaps(line.strip())
    if not s or len(s) > 62 or len(s.split()) > 8 or re.search(r"[.,;:!?]$", s):
        return None
    name = re.sub(r"^([IVX]{1,5}\.|\d{1,2}\.?)\s+", "", s)
    if not PDF_SEC.match(name) or not name[0].isupper():
        return None
    return name if sum(c.isalpha() or c in " -&," for c in name) >= 0.9 * len(name) else None


def pdf_split(lines):
    """Prose text and captions of a stretch of a PDF text dump; table cells, axis labels and formulas are dropped."""
    blocks, cur = [], []
    for l in list(lines) + [""]:
        if l.strip():
            cur.append(l.strip())
        elif cur:
            blocks.append(cur)
            cur = []
    prose, caps = [], []
    for b in blocks:
        m = PDF_CAPTION.match(b[0])
        if m:
            keep = [b[0]]
            for l in b[1:]:      # the caption ends where the table body or the plot labels begin
                w = re.findall(r"[A-Za-z][A-Za-z\-']+", l)
                if len(w) < 4 or len(w) < 0.6 * len(l.split()):
                    break
                keep.append(l)
            caps.append(("table" if m.group(1).lower().startswith("tab") else "figure", " ".join(keep)))
            continue
        for l in b:
            w = re.findall(r"[A-Za-z][A-Za-z\-']+", l)
            if len(w) >= 6 and len(w) >= 0.6 * len(l.split()):
                prose.append(l)
    return re.sub(r"(\w)-\s+(\w)", r"\1\2", " ".join(prose)), caps


def _exp_summary(blocks, caps, body_words, abstract, source):
    """blocks: [(loc, section, text for counting, words, is_setup)]; caps: [(kind, loc, section, text)].

    Paragraphs under a setup heading (datasets, implementation details, metrics) are left out of the counts:
    their numbers are settings, not results."""
    all_words = sum(b[3] for b in blocks)
    blocks = [b for b in blocks if not b[4]]
    words = sum(b[3] for b in blocks)
    vals = [v for b in blocks for v in values_in(b[2])]
    sents = [s for b in blocks for s in en_sentences(b[2])]
    english = sum(len(CJK_RE.findall(b[2])) for b in blocks) < 0.2 * max(1, words)
    fig = [size_words(c[3]) for c in caps if c[0] == "figure"]
    tab = [size_words(c[3]) for c in caps if c[0] == "table"]
    st = {"source": source, "abstract": abstract,
          "experiments": {"words": words, "sentences": len(sents), "values": len(vals),
                          "values_per_100w": round(100 * len(vals) / words, 2) if words else None,
                          "value_sentence_share": round(sum(1 for s in sents if values_in(s)) / len(sents), 3) if sents else None,
                          "negation_share": round(sum(1 for s in sents if NEG_RE.search(s)) / len(sents), 3) if sents and english else None,
                          "decimals3_share": round(sum(1 for v in vals if n_decimals(v) >= 3) / len(vals), 3) if vals else None,
                          "decimals4": sum(1 for v in vals if n_decimals(v) >= 4),
                          "share_of_body": round(all_words / body_words, 2) if body_words else None,
                          "body_words": body_words},
          "captions": {"figures": len(fig), "figure_words_median": _median(fig), "tables": len(tab),
                       "table_words_median": _median(tab),
                       "with_3_values": sum(1 for c in caps if len(values_in(c[3])) >= 3)}}
    return st


def numbers_stats(units, meta, path):
    """How the manuscript uses numbers in the abstract, the experiments prose and the captions."""
    abstract = None
    if meta.get("paragraphs_reliable") is False:          # plain text, usually a PDF dump
        lines = []
        raw = Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
        nonblank = [l for l in raw if l.strip()]
        front = sum(1 for l in nonblank if re.match(r"^\s*\d{1,4}\s{2,}\S", l)) >= 0.3 * max(1, len(nonblank))
        alone = sum(1 for l in nonblank if re.fullmatch(r"\s*\d{3}\s*", l)) >= 0.15 * max(1, len(nonblank))
        for l in raw:
            if (front and re.match(r"^\s*\d{1,4}\s*$", l)) or (alone and re.fullmatch(r"\s*\d{3}\s*", l)) or PDF_NOISE.search(l):
                continue
            lines.append(re.sub(r"^\s*\d{1,4}\s{2,}(?=\S)", "", l) if front else l)
        heads = [(pdf_heading(l), i) for i, l in enumerate(lines)]
        heads = [(n, i) for n, i in heads if n]
        text = "\n".join(lines)
        m = re.search(r"\bA ?bstract\b\s*[—:\-.]?\s*|\bA ?BSTRACT\b\s*|摘\s*要\s*[:：]?\s*", text)
        if m and m.start() < 6000:
            rest = text[m.end(): m.end() + 4000]
            end = re.search(r"\n\s*(Index Terms|Keywords?|Key words|关键词|CCS Concepts|(\d\.?|I\.)?\s*I ?ntroduction\s*\n|"
                            r"(\d\.?|I\.)?\s*I ?NTRODUCTION\s*\n|\d\s*\n\s*Introduction)", rest)
            ab = re.sub(r"(\w)-\s+(\w)", r"\1\2", " ".join(rest[: end.start() if end else 1700].split()))
            abstract = {"words": size_words(ab), "values": len(values_in(ab)), "list": values_in(ab)[:16],
                        "protocol_detail": bool(ABS_DETAIL.search(ab))}
        a0 = next((i for n, i in heads if EXP_START.match(n)), None)
        blocks, caps, body = [], [], 0
        if a0 is not None:
            b0 = next((i for n, i in heads if i > a0 and EXP_STOP.match(n)), len(lines))
            prose, cps = pdf_split(lines[a0 + 1: b0])
            ss = en_sentences(prose)
            for k in range(0, len(ss), 5):                # five sentences stand in for a paragraph
                chunk = " ".join(ss[k: k + 5])
                blocks.append((f"实验第 {k + 1}–{min(k + 5, len(ss))} 句", "", chunk, size_words(chunk), False))
            caps = [(kind, "图表说明", "", c) for kind, c in cps]
            stop = next((i for n, i in heads if re.match(r"references|bibliography|appendix|supplementary|acknowledg", n, re.I)), len(lines))
            body = size_words(pdf_split(lines[:stop])[0])
        st = _exp_summary(blocks, caps, body, abstract, "text")
    else:
        base = assign_tops(units)
        tops = [u.text for u in units if u.kind == "heading" and u.level <= base]
        name = lambda s: re.sub(r"^[IVX\d.]+\s+", "", s).strip()
        i0 = next((i for i, s in enumerate(tops) if EXP_START.match(name(s))), None)
        exp = set()
        if i0 is not None:
            i1 = next((i for i in range(i0 + 1, len(tops)) if EXP_STOP.match(name(tops[i]))), len(tops))
            exp = set(tops[i0:i1])
        ab = " ".join((u.raw or u.text) for u in units if u.kind == "abstract")
        if ab.strip():
            abstract = {"words": size_words(ab), "values": len(values_in(ab)), "list": values_in(ab)[:16],
                        "protocol_detail": bool(ABS_DETAIL.search(ab))}
        blocks = [(u.loc, u.section, u.raw or u.text, size_words(u.text), bool(SETUP_NAME.search(u.section)))
                  for u in units if u.kind == "prose" and u.top in exp]
        caps = [(u.env or ("table" if re.match(r"\s*(表|Table|Tab\.)", u.text) else "figure"), u.loc, u.section, u.raw or u.text)
                for u in units if u.kind == "caption" and u.top in exp]
        stop = next((i for i, s in enumerate(tops) if re.match(r"references|bibliography|appendix|supplementary|acknowledg|"
                                                               r"参考文献|附录|致谢", name(s), re.I)), len(tops))
        keep = set(tops[:stop])
        body = sum(size_words(u.text) for u in units if u.kind in ("prose", "list") and u.top in keep)
        st = _exp_summary(blocks, caps, body, abstract, "units")
    st["_blocks"], st["_caps"] = blocks, caps
    return st


def check_numbers(units, meta, path, cfg):
    cfg = cfg or {}
    n = dict(NUM_NORMS)
    loaded = load_norms(None, cfg.get("norms"))
    n.update({k: v for k, v in loaded.items() if k in NUM_NORMS})
    st = numbers_stats(units, meta, path)
    meta["numbers"] = {k: v for k, v in st.items() if not k.startswith("_")}
    cap = cfg.get("severity")

    def sev(s):
        return cap if cap and SEV_ORDER[s] < SEV_ORDER[cap] else s
    F = []
    ab, ex, cp = st["abstract"], st["experiments"], st["captions"]
    if ab:
        if ab["values"] > n["abstract_values"]:
            F.append(Finding("D", sev("高"), "摘要", "", "、".join(ab["list"][:8]) + ("…" if ab["values"] > 8 else ""),
                             f"摘要里有 {ab['values']} 个结果数值（已发表论文通常 0–1 个，最多 {n['abstract_values']} 个）："
                             "只留最硬的一两个，写清在哪个数据集、比谁、提升多少，其余留给实验一节"))
        if ab["protocol_detail"]:
            F.append(Finding("D", sev("中"), "摘要", "", "", "摘要里出现置信区间、种子数或检验方式：这些是实验细节，移到实验一节或表格"))
    if not ex["words"]:
        return F
    d = ex["values_per_100w"]
    hi = n["exp_values_per_100w"]
    summary = (f"实验正文每百词 {d} 个数值，{round(100 * (ex['value_sentence_share'] or 0))}% 的句子带数值"
               "（已发表论文的中位数是每百词 0.7 个、11% 的句子，九成论文每百词不超过 1.3 个）")
    enough = ex["words"] >= 300          # a few paragraphs say little about the section as a whole
    if not enough:
        F.append(Finding("D", "提示", "实验", "", summary, "实验正文不足 300 词，只按段检查"))
    elif d > hi:
        F.append(Finding("D", sev("高"), "实验", "", summary, "数字报得太密：数字留在表和图里，正文每段写结论、指向图表、"
                         "引一两个最关键的数，再解释原因（writing.md 第 4.6 节）"))
    elif d > 0.65 * hi:
        F.append(Finding("D", sev("中"), "实验", "", summary, "数字偏密：检查下面点名的段落，把逐项报数改成结论加原因"))
    else:
        F.append(Finding("D", "提示", "实验", "", summary, "数字的用量在常见范围内"))
    dense = 0
    for loc, sec, text, words, setup in st["_blocks"]:
        v = len(values_in(text))
        if setup or v < n["block_values"] or 100 * v / max(1, words) < n["block_density"]:
            continue
        dense += 1
        F.append(Finding("D", sev("高" if v >= 8 else "中"), loc, sec, clean_tex(text)[:44].strip() + f"…（{v} 个数值，{words} 词）",
                         "这一段在逐项报数：先写这组结果说明什么，指向表或图，只引最关键的一两个数（优先写相对量），"
                         "再解释原因；其余数字留在表里"))
    meta["numbers"]["experiments"]["dense_blocks"] = dense
    if ex["decimals4"] >= 3:
        ex4 = [v for b in st["_blocks"] if not b[4] for v in values_in(b[2]) if n_decimals(v) >= 4][:5]
        F.append(Finding("D", sev("中"), "实验", "", "、".join(ex4), f"正文里有 {ex['decimals4']} 个数值保留四位以上小数"
                         "（已发表论文的正文里几乎没有）：按指标的惯例取位；差别要到第四位小数才看得出时，这个差别通常撑不起主张"))
    if enough and ex["sentences"] >= 20 and ex["negation_share"] is not None and ex["negation_share"] > n["negation_share"]:
        F.append(Finding("D", sev("中"), "实验", "", f"{round(100 * ex['negation_share'])}% 的句子带否定或限定",
                         "实验一节否定句偏多（已发表论文的中位数是 7%）：改成正面陈述，适用条件集中写在一处"))
    if ex["body_words"] >= 1500 and ex["share_of_body"] and ex["share_of_body"] > n["exp_share"]:
        F.append(Finding("D", sev("中"), "实验", "", f"实验占正文 {round(100 * ex['share_of_body'])}%",
                         "实验一节偏长（已发表论文的中位数是 30%，九成不超过 38%）：不支撑主张的实验移到附录或补充材料"))
    for kind, loc, sec, text in st["_caps"]:
        w, v = size_words(text), len(values_in(text))
        lim = n["figure_caption_words"] if kind == "figure" else n["table_caption_words"]
        what = "图题" if kind == "figure" else "表题"
        if w > lim or v >= 3:
            why = []
            if w > lim:
                why.append(f"{w} 词（已发表论文的{what}中位数约 {40 if kind == 'figure' else 20} 词）")
            if v >= 3:
                why.append(f"含 {v} 个结果数值")
            F.append(Finding("D", sev("中"), loc, sec, clean_tex(text)[:44].strip() + "…", f"{what}{'，'.join(why)}："
                             f"{what}只写比较的是什么、怎么读这张{'图' if kind == 'figure' else '表'}，最多加一句结论；"
                             "实验协议、结果复述和限定说明放到正文或附录"))
    return F


# --------------------------------------------------------------------------- report
NAMES = {"Q": "疑问句", "A": "自我削弱", "B": "过度对冲", "C": "替审稿人总结弱点", "E": "过程叙述",
         "I": "改动前后混写", "J1": "文件名/路径/代码标识符", "J2": "条目式分段", "J3": "引用顺序", "K": "摘要三件事",
         "S": "结构与篇幅", "D": "数字的用法", "P": "占位符"}


def report(path, findings, maxn):
    findings = sorted(findings, key=lambda f: (SEV_ORDER.get(f.sev, 9), f.code))
    counts = {s: sum(1 for f in findings if f.sev == s) for s in SEV_ORDER}
    out = [f"# 文稿初筛：{Path(path).name}", "",
           f"共 {len(findings)} 处疑点（高 {counts['高']}、中 {counts['中']}、低 {counts['低']}、提示 {counts['提示']}）。"
           "脚本只负责找疑点，逐句确认语境后再改；描述已有方法缺口的句子不算防御性写作。", ""]
    by_code = {}
    for f in findings:
        by_code.setdefault(f.code, []).append(f)
    for code in ["S", "D", "P", "Q", "A", "B", "C", "E", "I", "J1", "J2", "J3", "K"]:
        fs = by_code.get(code)
        if not fs:
            continue
        out.append(f"## {code} {NAMES[code]}（{len(fs)}）")
        out.append("")
        out.append("| 级别 | 位置 | 片段 | 说明 |")
        out.append("|---|---|---|---|")
        for f in fs[:maxn]:
            sn = f.snippet.replace("|", "\\|")
            out.append(f"| {f.sev} | {f.loc}{('（' + f.section[:16] + '）') if f.section else ''} | {sn} | {f.msg} |")
        if len(fs) > maxn:
            out.append(f"| … | | 另有 {len(fs) - maxn} 处 | |")
        out.append("")
    if not findings:
        out.append("没有发现疑点。仍需按 writing.md 逐段做语义审查。")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--max", type=int, default=300, help="max rows per rule in the report")
    ap.add_argument("--venue-type", choices=["journal", "conference"], help="default: read from the document class")
    ap.add_argument("--target-venue", help="regex for the target journal in the reference list, "
                                           "e.g. 'Circuits and Systems for Video Technology'")
    ap.add_argument("--norms", help="venue_norms.json with thresholds (default: research/writing_profile/venue_norms.json "
                                    "when it exists)")
    ap.add_argument("--structure-severity", choices=["高", "中", "低"],
                    help="highest severity of the structure (S) and number-use (D) findings "
                         "(default for S: 高 for journals, 中 for conferences)")
    ap.add_argument("--stats", action="store_true", help="print the measured structure as JSON and exit")
    ap.add_argument("--year", type=int, help="current year for the 'recent references' count (default: today)")
    a = ap.parse_args(argv)
    p = Path(a.file)
    ext = p.suffix.lower()
    if ext == ".tex":
        units, meta = read_tex(p)
    elif ext == ".docx":
        units, meta = read_docx(p)
    elif ext in (".md", ".markdown", ".txt"):
        units, meta = read_md_or_txt(p)
    else:
        sys.exit("支持 .tex、.md、.txt、.docx")
    norms = a.norms or ("research/writing_profile/venue_norms.json"
                        if Path("research/writing_profile/venue_norms.json").exists() else None)
    cfg = {"venue_type": a.venue_type, "target_venue": a.target_venue, "norms": norms, "year": a.year,
           "severity": a.structure_severity}
    if a.stats:
        if not meta:
            meta = {}
        n = load_norms(None, norms)
        st = structure_stats(units, meta, p, a.year, a.target_venue or n.get("_target_venue"))
        st["numbers"] = {k: v for k, v in numbers_stats(units, meta, p).items() if not k.startswith("_")}
        print(json.dumps(st, ensure_ascii=False, indent=1))
        return 0
    findings = lint_units(units, meta, p, cfg)
    if a.json:
        print(json.dumps([f.__dict__ for f in findings], ensure_ascii=False, indent=1))
    else:
        print(report(p, findings, a.max))
    return 0


if __name__ == "__main__":
    sys.exit(main())
