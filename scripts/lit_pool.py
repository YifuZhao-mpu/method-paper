#!/usr/bin/env python3
"""Verified literature pool for the related work and the reference list.

Every entry is taken from Crossref or arXiv metadata, so no reference is written from memory. The pool lives in
research/refs_pool.json; refs_pool.md (reading list by group) and refs_pool.bib are regenerated on every change.

  lit_pool.py find "Circuits and Systems for Video Technology"          # ISSN of the target journal
  lit_pool.py venue --issn 1051-8215 --query "gaussian splatting compression" --from 2025 [--must "gaussian|splat"]
  lit_pool.py topic --query "spherical harmonics compression" --from 2022 [--must REGEX] [--tag "SH compression"]
  lit_pool.py arxiv --query "gaussian splatting compression" [--rows 30] [--tag ...]
  lit_pool.py doi 10.1145/3592433 10.1109/TCSVT.2025.3541234 [--tag baseline]
  lit_pool.py import old_refs.bib | crossref_response.json [--tag ...]   # .bib entries stay unverified until `check`
  lit_pool.py norms --issn 1051-8215 --query "gaussian splatting" --from 2025 --venue "IEEE TCSVT" \
                    --target-regex "Circuits and Systems for Video Technology" [--exemplars a.json b.json]
  lit_pool.py check paper/refs.bib [--add]  # each entry against Crossref / arXiv: verified, mismatch, year, not found
  lit_pool.py drop KEY [KEY ...]            # remove off-topic entries
  lit_pool.py status

`venue` collects recent papers of the target journal on the topic (group "目标期刊"); `norms` measures how many
references such papers carry and writes research/writing_profile/venue_norms.json, which paper_lint.py reads.
Set CROSSREF_MAILTO to your contact address to use Crossref's polite pool (optional).
Network: api.crossref.org and export.arxiv.org. Only the Python standard library is used.
"""
import argparse
import datetime
import difflib
import html
import json
import os
import re
import statistics
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

CR = "https://api.crossref.org"
ARXIV = "http://export.arxiv.org/api/query"
# `references-count` is the selectable name; full records carry `reference-count` as well
SELECT = ("DOI,title,author,issued,references-count,is-referenced-by-count,container-title,volume,issue,page,type,"
          "abstract,published-print,published-online")
POOL = Path("research/refs_pool.json")
NORMS = Path("research/writing_profile/venue_norms.json")
STOP = {"a", "an", "the", "of", "for", "and", "on", "in", "with", "to", "via", "from", "by", "towards", "toward", "is", "are"}


# ----------------------------------------------------------------------------------------------- network
def _agent():
    mail = os.environ.get("CROSSREF_MAILTO", "").strip()
    return "method-paper-lit-pool/1.0" + (f" (mailto:{mail})" if mail else "")


def fetch(url, tries=4):
    """GET with retries; returns the body as text. Raises RuntimeError with a readable message."""
    last = ""
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _agent(), "Accept": "application/json, application/atom+xml"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 404):
                raise RuntimeError(f"{last}: {url}")
            if e.code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f"{last}: {url}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(getattr(e, "reason", e))
        time.sleep(1.5 * (k + 1))
    raise RuntimeError(f"请求失败（{last}）：{url}")


def fetch_json(url):
    return json.loads(fetch(url))


def cr_list(path, params):
    """Crossref list query; falls back to a query without `select` when the server rejects the field list."""
    q = dict(params)
    q.setdefault("select", SELECT)
    try:
        return fetch_json(f"{CR}{path}?{urllib.parse.urlencode(q)}")["message"]
    except RuntimeError as e:
        if "HTTP 400" not in str(e):
            raise
        q.pop("select", None)
        return fetch_json(f"{CR}{path}?{urllib.parse.urlencode(q)}")["message"]


# ----------------------------------------------------------------------------------------------- entries
def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", "", s or ""))
    return re.sub(r"\s+", " ", s).strip()


def norm_title(s):
    s = unicodedata.normalize("NFKD", clean(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _year(item):
    for k in ("published-print", "issued", "published-online", "published"):
        dp = (item.get(k) or {}).get("date-parts") or [[None]]
        if dp and dp[0] and dp[0][0]:
            return int(dp[0][0])
    return None


def from_crossref(item):
    authors = []
    for a in item.get("author") or []:
        if a.get("family"):
            authors.append({"family": clean(a["family"]), "given": clean(a.get("given", ""))})
        elif a.get("name"):
            authors.append({"family": clean(a["name"]), "given": ""})
    typ = item.get("type", "")
    vol, pages = item.get("volume", ""), item.get("page", "")
    if not vol and pages in ("1-1", "1"):   # early-access records carry a placeholder page range
        pages = ""
    return {"doi": (item.get("DOI") or "").lower(), "title": clean((item.get("title") or [""])[0]), "authors": authors,
            "year": _year(item), "venue": clean((item.get("container-title") or [""])[0]),
            "volume": vol, "issue": item.get("issue", ""), "pages": pages,
            "type": "article" if typ == "journal-article" else "inproceedings" if typ == "proceedings-article" else
            "preprint" if typ == "posted-content" else (typ or "misc"),
            "refs": item.get("reference-count") or item.get("references-count") or 0,
            "cited_by": item.get("is-referenced-by-count") or 0,
            "abstract": clean(item.get("abstract", ""))[:1500], "source": "crossref", "verified": True}


def from_arxiv(xml_text):
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    out = []
    for e in ET.fromstring(xml_text).findall("a:entry", ns):
        m = re.search(r"arxiv\.org/abs/([^v\s]+)", e.findtext("a:id", "", ns))
        if not m:
            continue
        names = [clean(a.findtext("a:name", "", ns)) for a in e.findall("a:author", ns)]
        authors = [{"family": n.split()[-1], "given": " ".join(n.split()[:-1])} for n in names if n]
        jref, doi = clean(e.findtext("x:journal_ref", "", ns)), clean(e.findtext("x:doi", "", ns)).lower()
        out.append({"doi": "", "arxiv": m.group(1), "title": clean(e.findtext("a:title", "", ns)), "authors": authors,
                    "year": int(e.findtext("a:published", "0000", ns)[:4]) or None, "venue": f"arXiv preprint arXiv:{m.group(1)}",
                    "volume": "", "issue": "", "pages": "", "type": "preprint", "refs": 0, "cited_by": 0,
                    "abstract": clean(e.findtext("a:summary", "", ns))[:1500], "source": "arxiv", "verified": True,
                    "published_as": (jref or (f"doi:{doi}" if doi else ""))})
    return out


def ascii_word(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]", "", s).lower()


def make_key(e, taken):
    fam = ascii_word(e["authors"][0]["family"]) if e.get("authors") else "anon"
    word = next((ascii_word(w) for w in re.split(r"[\s:\-–—/]+", e.get("title", "")) if ascii_word(w) and ascii_word(w) not in STOP), "paper")
    base = f"{fam or 'anon'}{e.get('year') or ''}{word}"
    key, k = base, 0
    while key in taken:
        k += 1
        key = base + "abcdefghijklmnopqrstuvwxyz"[(k - 1) % 26] * (1 + (k - 1) // 26)
    return key


class Pool:
    def __init__(self, path=POOL):
        self.path = Path(path)
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.entries = data.get("entries", [])
        self.labels = data.get("labels", {})   # display names of groups, e.g. venue:1051-8215 -> IEEE TCSVT

    def find(self, e):
        nt = norm_title(e.get("title", ""))
        for x in self.entries:
            if (e.get("doi") and x.get("doi") == e["doi"]) or (e.get("arxiv") and x.get("arxiv") == e["arxiv"]) \
                    or (nt and norm_title(x["title"]) == nt):
                return x
        return None

    def add(self, e, tag):
        """Add or merge an entry; returns True when it is new."""
        if not e.get("title"):
            return False
        old = self.find(e)
        if old:
            if tag and tag not in old["tags"]:
                old["tags"].append(tag)
            # a journal or proceedings record replaces a preprint record of the same paper
            if old.get("type") == "preprint" and e.get("type") in ("article", "inproceedings") and e.get("verified"):
                keep = {"key": old["key"], "tags": old["tags"], "arxiv": old.get("arxiv", "")}
                old.clear()
                old.update(e, **keep)
            elif not old.get("verified") and e.get("verified"):
                keep = {"key": old["key"], "tags": old["tags"]}
                old.clear()
                old.update(e, **keep)
            return False
        e = dict(e)
        e["tags"] = [tag] if tag else []
        taken = {x["key"] for x in self.entries}
        e["key"] = e["key"] if e.get("key") and e["key"] not in taken else make_key(e, taken)
        self.entries.append(e)
        return True

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"labels": self.labels, "entries": self.entries}, ensure_ascii=False, indent=1),
                             encoding="utf-8")
        self.path.with_suffix(".bib").write_text(to_bib(self.entries), encoding="utf-8")
        self.path.with_suffix(".md").write_text(to_md(self.entries, labels=self.labels), encoding="utf-8")


def bib_escape(s):
    return re.sub(r"([&%#_])", r"\\\1", s)


def to_bib(entries):
    out = ["% Generated by method-paper lit_pool.py from Crossref / arXiv metadata. Do not edit by hand.\n"]
    for e in sorted(entries, key=lambda x: x["key"]):
        if not e.get("verified"):
            continue
        kind = {"article": "article", "inproceedings": "inproceedings", "preprint": "article"}.get(e["type"], "misc")
        f = [("author", " and ".join(f"{a['family']}, {a['given']}".rstrip(", ") for a in e["authors"]) or "Anonymous"),
             ("title", "{" + bib_escape(e["title"]) + "}")]
        if kind == "inproceedings":
            f.append(("booktitle", bib_escape(e["venue"])))
        else:
            f.append(("journal", bib_escape(e["venue"])))
        f.append(("year", str(e.get("year") or "")))
        for k, v in (("volume", e.get("volume")), ("number", e.get("issue")), ("pages", (e.get("pages") or "").replace("-", "--").replace("----", "--")),
                     ("doi", e.get("doi"))):
            if v:
                f.append((k, v))
        if e["type"] == "article" and not e.get("volume") and not e.get("pages"):
            f.append(("note", "early access"))
        out.append("@%s{%s,\n%s\n}\n" % (kind, e["key"], ",\n".join(f"  {k} = {{{v}}}" for k, v in f if v)))
    return "\n".join(out)


def group_of(tag):
    return {"venue": "目标期刊", "topic": "主题", "arxiv": "预印本", "doi": "指定文献", "import": "导入"}.get(tag.split(":", 1)[0], tag)


def to_md(entries, year=None, labels=None):
    year = year or datetime.date.today().year
    labels = labels or {}
    ver = [e for e in entries if e.get("verified")]
    venue = [e for e in ver if any(t.startswith("venue:") for t in e["tags"])]
    L = ["# 文献池", "",
         "由 lit_pool.py 从 Crossref 和 arXiv 的元数据生成，不要手改。写相关工作和参考文献只从这里取条目（BibTeX 在 refs_pool.bib，"
         "引用键是第一列）；池里没有的文献先用 `lit_pool.py doi` 或 `topic` 加进来。检索结果按相关度排序，可能混入不相关的论文，"
         "引用前要读过摘要或原文，不相关的用 `lit_pool.py drop <键>` 去掉。", "",
         f"共 {len(ver)} 条已核实：{year - 1} 年以来 {sum(1 for e in ver if (e.get('year') or 0) >= year - 1)} 条；目标期刊 {len(venue)} 条，"
         f"其中 {year - 1} 年以来 {sum(1 for e in venue if (e.get('year') or 0) >= year - 1)} 条。"
         + (f"另有 {len(entries) - len(ver)} 条未核实（见文末），核实前不能引用。" if len(ver) < len(entries) else ""), ""]
    tags = []
    for e in ver:
        for t in e["tags"] or ["未分组"]:
            if t not in tags:
                tags.append(t)
    tags.sort(key=lambda t: (not t.startswith("venue:"), t.startswith("arxiv:"), t))
    for t in tags:
        rows = [e for e in ver if t in (e["tags"] or ["未分组"])]
        rows.sort(key=lambda e: (-(e.get("year") or 0), -e.get("cited_by", 0)))
        name = t.split(":", 1)
        shown = labels.get(t) or (name[1] if len(name) > 1 else "")
        L += [f"## {group_of(t)}{('：' + shown) if shown else ''}（{len(rows)}）", "",
              "| 引用键 | 年 | 题目 | 出处 | 参考文献数 | 被引 |", "|---|---|---|---|---|---|"]
        for e in rows:
            note = f"（已发表：{e['published_as']}，引用正式版本）" if e.get("published_as") else ""
            L.append(f"| {e['key']} | {e.get('year') or ''} | {e['title'].replace('|', '/')} | {e['venue'].replace('|', '/')}{note} | "
                     f"{e.get('refs') or ''} | {e.get('cited_by') or ''} |")
        L.append("")
    abst = [e for e in ver if e.get("abstract")]
    if abst:
        L += ["## 摘要节选", ""]
        for e in sorted(abst, key=lambda x: x["key"]):
            L += [f"**{e['key']}**：{e['abstract'][:600]}", ""]
    bad = [e for e in entries if not e.get("verified")]
    if bad:
        L += ["## 未核实（不能引用）", ""] + [f"- {e['key']}：{e['title']}（{e.get('venue', '')}，{e.get('year') or ''}）" for e in bad] + [""]
    return "\n".join(L)


# ----------------------------------------------------------------------------------------------- commands
def from_date(v):
    return f"{v}-01-01" if re.fullmatch(r"\d{4}", str(v)) else str(v)


def keep(e, must):
    return not must or re.search(must, e["title"] + " " + e.get("abstract", ""), re.I)


def cmd_find(a):
    msg = fetch_json(f"{CR}/journals?{urllib.parse.urlencode({'query': a.name, 'rows': 8})}")["message"]
    if not msg.get("items"):
        print("没有找到匹配的期刊")
        return 1
    for it in msg["items"]:
        print(f"{'/'.join(it.get('ISSN') or [])}\t{it.get('title')}\t{it.get('publisher', '')}")
    return 0


def collect(a, path, params, tag, label=None):
    pool = Pool(a.pool)
    if label:
        pool.labels[tag] = label
    msg = cr_list(path, params)
    new = seen = 0
    for it in msg.get("items", []):
        e = from_crossref(it)
        if e["type"] not in ("article", "inproceedings") or not keep(e, a.must):
            continue
        seen += 1
        new += pool.add(e, tag)
    pool.save()
    print(f"{pool.labels.get(tag, tag)}：检索到 {len(msg.get('items', []))} 条（库中共 {msg.get('total-results', '?')} 条匹配），保留 {seen} 条，新增 {new} 条；"
          f"文献池现有 {len(pool.entries)} 条 → {pool.path.with_suffix('.md')}")
    return 0


def cmd_venue(a):
    params = {"query": a.query, "filter": f"from-pub-date:{from_date(a.since)}", "rows": a.rows, "sort": "relevance"}
    return collect(a, f"/journals/{a.issn}/works", params, f"venue:{a.issn}", a.tag)


def cmd_topic(a):
    params = {"query.bibliographic": a.query, "rows": a.rows, "sort": "relevance",
              "filter": f"from-pub-date:{from_date(a.since)},type:journal-article,type:proceedings-article"}
    return collect(a, "/works", params, f"topic:{a.tag or a.query}")


def cmd_arxiv(a):
    q = a.query if a.raw else " AND ".join(f"all:{w}" for w in a.query.split())
    url = f"{ARXIV}?{urllib.parse.urlencode({'search_query': q, 'start': 0, 'max_results': a.rows, 'sortBy': 'relevance'})}"
    pool = Pool(a.pool)
    got = [e for e in from_arxiv(fetch(url)) if keep(e, a.must) and (not a.since or (e["year"] or 0) >= int(str(a.since)[:4]))]
    new = sum(pool.add(e, f"arxiv:{a.tag or a.query}") for e in got)
    pool.save()
    print(f"arXiv：保留 {len(got)} 条，新增 {new} 条；已正式发表的预印本在表里有标注，用 `lit_pool.py doi` 换成正式版本")
    return 0


def cmd_doi(a):
    pool = Pool(a.pool)
    bad = 0
    for d in a.dois:
        d = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:)", "", d.strip(), flags=re.I)
        try:
            e = from_crossref(fetch_json(f"{CR}/works/{urllib.parse.quote(d, safe='/')}")["message"])
        except RuntimeError as ex:
            print(f"没有查到 {d}：{ex}")
            bad += 1
            continue
        new = pool.add(e, f"doi:{a.tag}" if a.tag else "doi")
        print(f"{'新增' if new else '已有'} {pool.find(e)['key']}：{e['title']}（{e['venue']}，{e['year']}）")
    pool.save()
    return 1 if bad else 0


BIB_ENTRY = re.compile(r"@(\w+)\s*\{\s*([^,\s]+)\s*,(.*?)(?=\n@|\Z)", re.S)


def bib_field(body, name):
    m = re.search(rf"\b{name}\s*=\s*(\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}}|\"[^\"]*\"|\d+)", body, re.I | re.S)
    return clean(m.group(1).strip('{}"')).replace("{", "").replace("}", "") if m else ""


def read_bib(path):
    out = []
    for m in BIB_ENTRY.finditer(Path(path).read_text(encoding="utf-8", errors="ignore")):
        kind, key, body = m.group(1).lower(), m.group(2), m.group(3)
        if kind in ("comment", "string", "preamble"):
            continue
        authors = []
        for n in re.split(r"\s+and\s+", bib_field(body, "author")):
            n = n.strip()
            if not n:
                continue
            fam, giv = (n.split(",", 1) + [""])[:2] if "," in n else (n.split()[-1], " ".join(n.split()[:-1]))
            authors.append({"family": fam.strip(), "given": giv.strip()})
        venue = bib_field(body, "journal") or bib_field(body, "booktitle")
        ax = re.search(r"arXiv[:\s]*(\d{4}\.\d{4,5})", body, re.I)
        y = bib_field(body, "year")
        out.append({"key": key, "doi": re.sub(r"^https?://(dx\.)?doi\.org/", "", bib_field(body, "doi")).lower(),
                    "arxiv": ax.group(1) if ax else "", "title": bib_field(body, "title"), "authors": authors,
                    "year": int(y) if y.isdigit() else None, "venue": venue, "volume": bib_field(body, "volume"),
                    "issue": bib_field(body, "number"), "pages": bib_field(body, "pages"),
                    "type": "inproceedings" if kind in ("inproceedings", "conference") else "article" if kind == "article" else "misc",
                    "bibtype": kind, "refs": 0, "cited_by": 0, "abstract": "", "source": "bib", "verified": False})
    return out


def cmd_import(a):
    pool = Pool(a.pool)
    p = Path(a.file)
    if p.suffix.lower() == ".json":
        msg = json.loads(p.read_text(encoding="utf-8")).get("message", {})
        items = [from_crossref(it) for it in (msg.get("items") or ([msg] if msg.get("DOI") else []))]
    else:
        items = read_bib(p)
    new = sum(pool.add(e, f"import:{a.tag or p.name}") for e in items if keep(e, a.must))
    pool.save()
    unv = sum(1 for e in pool.entries if not e.get("verified"))
    print(f"导入 {len(items)} 条，新增 {new} 条" + (f"；{unv} 条来自 .bib、尚未核实，运行 `lit_pool.py check {a.file} --add` 核实后才进入 refs_pool.bib" if unv else ""))
    return 0


NOT_PAPERS = ("misc", "online", "software", "manual", "techreport", "book", "inbook", "incollection", "phdthesis",
              "mastersthesis", "booklet", "unpublished", "electronic", "dataset", "standard")


def verify(e):
    """Look an entry up; returns (status, verified entry or None, note).

    verified; mismatch: the DOI or arXiv number belongs to another paper; year: found, but the year differs;
    notfound: neither Crossref nor arXiv has the title; manual: not a paper (book, software, web page), not checked."""
    want = norm_title(e["title"])
    if e.get("bibtype") in NOT_PAPERS and not e.get("doi") and not e.get("arxiv"):
        return "manual", None, "非论文条目（书、软件、网页等），没有自动核对"

    def sim(t):
        return difflib.SequenceMatcher(None, want, norm_title(t)).ratio()
    try:
        if e.get("doi"):
            got = from_crossref(fetch_json(f"{CR}/works/{urllib.parse.quote(e['doi'], safe='/')}")["message"])
            s = sim(got["title"])
            return ("verified", got, "") if s >= 0.85 else ("mismatch", got, f"DOI 指向的题目是“{got['title']}”")
        if e.get("arxiv"):
            got = from_arxiv(fetch(f"{ARXIV}?{urllib.parse.urlencode({'id_list': e['arxiv']})}"))
            if got and sim(got[0]["title"]) >= 0.85:
                return "verified", got[0], ""
            return "mismatch", (got[0] if got else None), "arXiv 编号对应的题目不同" if got else "arXiv 上没有这个编号"
        fam = e["authors"][0]["family"] if e.get("authors") else ""
        msg = cr_list("/works", {"query.bibliographic": f"{e['title']} {fam}".strip(), "rows": 5})
        best = max((from_crossref(it) for it in msg.get("items", [])), key=lambda g: sim(g["title"]), default=None)
        if best and sim(best["title"]) >= 0.9:
            if e.get("year") and best.get("year") and abs(e["year"] - best["year"]) > 1:
                return "year", best, (f"稿件写 {e['year']}，Crossref 的记录是 {best['year']}（{best['venue']}）；"
                                      "可能是同一工作的另一个版本，对着原文确认")
            return "verified", best, f"补上 DOI {best['doi']}"
        ax = from_arxiv(fetch(f"{ARXIV}?{urllib.parse.urlencode({'search_query': 'ti:' + chr(34) + e['title'] + chr(34), 'max_results': 3})}"))
        hit = max(ax, key=lambda g: sim(g["title"]), default=None)
        if hit and sim(hit["title"]) >= 0.9:
            return "verified", hit, f"arXiv:{hit['arxiv']}"
        return "notfound", None, "Crossref 和 arXiv 都没有查到这个题目；对着原文核对题目和出处"
    except RuntimeError as ex:
        return "error", None, str(ex)


def cmd_check(a):
    entries = read_bib(a.bib)
    pool = Pool(a.pool)
    order = ("mismatch", "year", "notfound", "manual", "error", "verified")
    rows, counts = [], {k: 0 for k in order}
    for e in entries:
        st, got, note = verify(e)
        counts[st] += 1
        rows.append((e["key"], st, e["title"], note))
        if a.add and st == "verified":
            got = dict(got, key=e["key"]) if not pool.find(got) else got
            pool.add(got, f"import:{Path(a.bib).name}")
        time.sleep(0.15)
    if a.add:
        done = {r[0] for r in rows if r[1] == "verified"}
        pool.entries = [x for x in pool.entries if x.get("verified") or x.get("source") != "bib" or x["key"] not in done]
        pool.save()
    # rows use the lint report's columns, so the section can be pasted into research/lint_report.md as it is
    sev = {"mismatch": "高", "year": "中", "notfound": "中", "manual": "提示", "error": "提示"}
    name = {"mismatch": "不一致", "year": "年份不同", "notfound": "查不到", "manual": "未核对", "error": "查询失败"}
    L = [f"# 参考文献核对：{a.bib}", "",
         f"共 {len(entries)} 条：已核实 {counts['verified']}，不一致 {counts['mismatch']}，年份不同 {counts['year']}，查不到 {counts['notfound']}"
         + (f"，非论文条目未核对 {counts['manual']}" if counts["manual"] else "")
         + (f"，查询失败 {counts['error']}（网络问题，重跑）" if counts["error"] else "") + "。"
         "不一致指 DOI 或 arXiv 编号对应的是另一篇论文，必须改正；年份不同和查不到的条目请作者对着原文核对，不擅自删除。", ""]
    bad = [r for r in rows if r[1] != "verified"]
    if bad:
        L += [f"## 参考文献核对（{len(bad)}）", "", "| 级别 | 位置 | 片段 | 说明 |", "|---|---|---|---|"]
        L += [f"| {sev[s]} | 参考文献（{k}） | {t.replace('|', '/')} | {name[s]}：{n} |"
              for k, s, t, n in sorted(bad, key=lambda r: order.index(r[1]))]
        L.append("")
    good = [r for r in rows if r[1] == "verified"]
    if good:
        L += [f"## 已核实（{len(good)}）", "", "、".join(k + (f"（{n}）" if n else "") for k, _, _, n in good), ""]
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(L[2] + f" → {out}")
    return 1 if counts["mismatch"] or counts["notfound"] or counts["year"] else 0


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0
    pos = (len(xs) - 1) * q
    lo = int(pos)
    return xs[lo] + (xs[min(lo + 1, len(xs) - 1)] - xs[lo]) * (pos - lo)


def cmd_norms(a):
    year = a.year or datetime.date.today().year
    measured, targets = {"query": a.query, "from": str(a.since)}, {}
    if a.issn:
        msg = cr_list(f"/journals/{a.issn}/works", {"query": a.query, "filter": f"from-pub-date:{from_date(a.since)},type:journal-article",
                                                    "rows": a.rows, "sort": "relevance"})
        items = [from_crossref(it) for it in msg.get("items", [])]
        items = [e for e in items if keep(e, a.must) and not re.search(r"\b(survey|review|overview)\b", e["title"], re.I)]
        counts = sorted(e["refs"] for e in items if e["refs"] >= 10)   # early-access records carry no reference list yet
        measured.update({"papers": len(items), "reference_counts": counts})
        if len(counts) >= 3:
            measured.update({"median_refs": round(statistics.median(counts)), "q1_refs": round(quantile(counts, 0.25)),
                             "q3_refs": round(quantile(counts, 0.75))})
            targets["refs"] = max(40 if a.venue_type == "journal" else 30, int(0.75 * statistics.median(counts)))
    ex = []
    for f in a.exemplars or []:
        try:
            s = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"读不了 {f}，跳过")
            continue
        num = s.get("numbers") or {}
        ex.append({"file": Path(f).name, "intro_words": s.get("intro", {}).get("words"), "related_words": s.get("related", {}).get("words"),
                   "related_subsections": s.get("related", {}).get("subsections"), "refs": s.get("refs", {}).get("count"),
                   "exp_values_per_100w": (num.get("experiments") or {}).get("values_per_100w"),
                   "abstract_values": (num.get("abstract") or {}).get("values"),
                   "figure_caption_words": (num.get("captions") or {}).get("figure_words_median")})
    if ex:
        measured["exemplars"] = ex

        def med(k):
            v = [x[k] for x in ex if x.get(k)]
            return statistics.median(v) if v else None
        base = {"journal": {"intro_words": 700, "related_words": 650, "refs": 40},
                "conference": {"intro_words": 450, "related_words": 300, "refs": 30}}[a.venue_type]
        for k in ("intro_words", "related_words", "refs"):
            m = med(k)
            if m:
                targets[k] = max(targets.get(k, 0), base[k], int(0.75 * m))
        # how densely the exemplars report numbers: the default limits are relaxed when the venue's papers use more
        dens = [x["exp_values_per_100w"] for x in ex if x.get("exp_values_per_100w") is not None]
        if dens:
            targets["exp_values_per_100w"] = max(2.0, round(1.5 * statistics.median(dens), 1))
        absv = [x["abstract_values"] for x in ex if x.get("abstract_values") is not None]
        if absv:
            targets["abstract_values"] = max(3, max(absv))
    data = {"venue": a.venue or a.issn or "", "venue_type": a.venue_type, "issn": a.issn or "",
            "target_venue_regex": a.target_regex or "", "measured": measured, "targets": targets,
            "made": datetime.date.today().isoformat(), "year": year}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    c = measured.get("reference_counts", [])
    print(f"{data['venue']}：" + (f"{len(c)} 篇同主题论文的参考文献数 {c[0]}–{c[-1]}，中位数 {measured.get('median_refs', '样本不足')}；" if c else "没有取到参考文献数；")
          + f"门槛 {targets or '用默认值'} → {out}")
    return 0


def cmd_drop(a):
    pool = Pool(a.pool)
    before = len(pool.entries)
    pool.entries = [e for e in pool.entries if e["key"] not in set(a.keys)]
    pool.save()
    print(f"去掉 {before - len(pool.entries)} 条，文献池现有 {len(pool.entries)} 条")
    return 0


def cmd_status(a):
    pool = Pool(a.pool)
    if not pool.entries:
        print("文献池是空的")
        return 1
    print("\n".join(to_md(pool.entries, labels=pool.labels).splitlines()[4:5]))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", default=str(POOL), help="pool file (default research/refs_pool.json)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("find", help="ISSN of a journal by name")
    p.add_argument("name")
    p.set_defaults(fn=cmd_find)
    for name, fn in (("venue", cmd_venue), ("topic", cmd_topic), ("arxiv", cmd_arxiv)):
        p = sub.add_parser(name)
        if name == "venue":
            p.add_argument("--issn", required=True)
        if name == "arxiv":
            p.add_argument("--raw", action="store_true", help="pass --query to the arXiv API unchanged")
        p.add_argument("--query", required=True)
        p.add_argument("--from", dest="since", default=str(datetime.date.today().year - (1 if name == "venue" else 4)),
                       help="earliest year or date")
        p.add_argument("--rows", type=int, default=40)
        p.add_argument("--must", help="regex the title or abstract must match")
        p.add_argument("--tag", help="group name in the pool")
        p.set_defaults(fn=fn)
    p = sub.add_parser("doi")
    p.add_argument("dois", nargs="+")
    p.add_argument("--tag")
    p.set_defaults(fn=cmd_doi)
    p = sub.add_parser("import")
    p.add_argument("file")
    p.add_argument("--tag")
    p.add_argument("--must")
    p.set_defaults(fn=cmd_import)
    p = sub.add_parser("check")
    p.add_argument("bib")
    p.add_argument("--add", action="store_true", help="add the verified entries to the pool under their keys")
    p.add_argument("--out", default="research/refs_check.md")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("norms")
    p.add_argument("--issn")
    p.add_argument("--query", default="")
    p.add_argument("--from", dest="since", default=str(datetime.date.today().year - 1))
    p.add_argument("--rows", type=int, default=60)
    p.add_argument("--must")
    p.add_argument("--venue", help="display name, e.g. 'IEEE TCSVT'")
    p.add_argument("--venue-type", choices=["journal", "conference"], default="journal")
    p.add_argument("--target-regex", help="regex that matches the venue name in a reference list")
    p.add_argument("--exemplars", nargs="*", help="JSON files written by `paper_lint.py <exemplar> --stats`")
    p.add_argument("--year", type=int)
    p.add_argument("--out", default=str(NORMS))
    p.set_defaults(fn=cmd_norms)
    p = sub.add_parser("drop")
    p.add_argument("keys", nargs="+")
    p.set_defaults(fn=cmd_drop)
    p = sub.add_parser("status")
    p.set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except RuntimeError as e:
        print(f"网络请求失败：{e}\n检查能否访问 api.crossref.org 和 export.arxiv.org；不通时把已有的 .bib 用 `lit_pool.py import` 导入，"
              "联网后再 `check`。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
