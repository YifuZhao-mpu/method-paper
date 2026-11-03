#!/usr/bin/env python3
"""Where a method-paper project spends its rounds: python funnel.py [--out research/funnel.md]

Run from the paper repository root. Reads research/pipeline.json (stage history, decisions), the PIPELINE line of every
Codex run in research/codex/ and research/findings.md, and reports facts only: how many times each gate ran and what it
decided, how screening rounds ended, anchors, where the time went. A few rule-based hints at the end point to the
sections of references/method-design.md that address the pattern.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline as P  # noqa: E402  (parse_pipeline, last_line)

R = Path("research")
KEYS = {"problems": "candidates", "gate1": "gate1", "methods": "options", "method_card": "method_card", "gate2": "gate2",
        "plan": "plan", "review": "review", "draft": "draft", "revise": "revise", "polish": "polish"}


def accepted_runs(hist):
    """Run numbers whose verdict the flow acted on, replayed from the history: each start of a Codex stage and each retry
    opens run n+1; a done accepts the open run; a retry right after a done (the author re-ran a verdict that paused the
    flow) withdraws it. Refused outputs never get a done."""
    cur, acc, open_done = defaultdict(int), defaultdict(set), {}
    for h in hist:
        s, ev = h.get("stage"), h.get("event")
        if s not in P.IDX:
            continue
        if ev == "start":
            open_done.clear()
            if P.STAGES[P.IDX[s]]["owner"] == "codex":
                cur[s] += 1
        elif ev == "retry":
            if s in open_done:
                acc[s].discard(open_done.pop(s))
            cur[s] += 1
        elif ev == "done":
            acc[s].add(cur[s])
            open_done[s] = cur[s]
    return acc


def codex_verdicts(hist):
    acc = accepted_runs(hist)
    out = defaultdict(list)
    for p in sorted((R / "codex").glob("*-*.md")):
        m = re.match(r"^(.*)-(\d+)\.md$", p.name)
        if not m or p.stat().st_size == 0 or int(m.group(2)) not in acc.get(m.group(1), set()):
            continue
        pl = P.parse_pipeline(P.last_line(p)) or {}
        out[m.group(1)].append((int(m.group(2)), pl))
    return {k: [pl for _, pl in sorted(v, key=lambda x: x[0])] for k, v in out.items()}


def parse_time(s):
    try:
        return dt.datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="also write the report here (markdown)")
    a = ap.parse_args()
    if not (R / "pipeline.json").exists():
        sys.exit("没有 research/pipeline.json：在论文仓库根目录运行")
    st = json.loads((R / "pipeline.json").read_text(encoding="utf-8"))
    d, hist = st.get("decisions", {}), st.get("history", [])
    cv = codex_verdicts(hist)
    lines = ["# 流程漏斗（自动统计，只陈述事实）", ""]
    times = [t for t in (parse_time(h.get("time")) for h in hist) if t]
    span = f"{times[0]:%Y-%m-%d} 到 {times[-1]:%Y-%m-%d}（{(times[-1] - times[0]).days} 天）" if times else "—"
    lines += [f"当前阶段：{st.get('stage')}{'（已暂停：' + st['stopped'] + '）' if st.get('stopped') else ''}；记录跨度：{span}", ""]

    lines += ["## Codex 各阶段的结论（只算流程采用了的运行，被校验拒绝或被 retry 替换的不算）", "",
              "| 阶段 | 次数 | 结论 |", "|---|---|---|"]
    gate_counts = {}
    for sid, key in KEYS.items():
        runs = cv.get(sid, [])
        if not runs:
            continue
        if sid in ("gate1", "gate2", "review"):
            c = Counter(pl.get(key, "无 PIPELINE 行") for pl in runs)
            gate_counts[sid] = c
            extra = ""
            if sid == "review":
                cc = Counter(pl["conditions"] for pl in runs if "conditions" in pl)
                extra = ("；机制探针 " + "、".join(f"{k} {v}" for k, v in cc.items())) if cc else ""
            desc = "、".join(f"{k} {v}" for k, v in c.most_common()) + extra
        elif sid == "methods":
            back = sum(1 for pl in runs if pl.get("back") == "problems")
            n_opts = sum(len([x for x in pl.get("options", "").split(",") if x]) for pl in runs)
            desc = f"共列出 {n_opts} 个候选" + (f"；{back} 次建议回到找问题" if back else "")
        elif sid == "problems":
            desc = f"候选编号已用到 C{d.get('max_candidate', 0)}"
        else:
            desc = "—"
        lines.append(f"| {sid} | {len(runs)} | {desc} |")

    starts = [h for h in hist if h.get("event") == "start"]
    screen_out = Counter()
    for h in hist:  # how each screen ended: the note of the next stage's start, or of the pause
        note, ev = h.get("note", ""), h.get("event")
        if ev == "start":
            if note.startswith("初筛达到判据"):
                screen_out["有候选达到判据"] += 1
            elif note.startswith("初筛有接近判据"):
                screen_out["只有接近的（细化）"] += 1
            elif note.startswith("初筛全部未达标"):
                screen_out["全部未达标"] += 1
            elif note.startswith("初筛没有复现"):
                screen_out["阳性对照全部不成立"] += 1
            elif note.startswith("初筛跳过"):
                screen_out["跳过"] += 1
        elif ev == "stop" and h.get("stage") == "screen":
            if "阳性对照全部不成立" in note:
                screen_out["阳性对照全部不成立"] += 1
            elif note.startswith("补强轮"):
                screen_out["补强候选全部未达标"] += 1
            else:
                screen_out["全部未达标"] += 1
    lines += ["", "## 初筛", ""]
    lines.append("、".join(f"{k} {v} 次" for k, v in screen_out.items()) if screen_out else "还没有初筛记录。")

    lines += ["", "## 锚点和否定记录", ""]
    anchors = d.get("anchors", [])
    cur = d.get("passed_candidate")

    def fails(x):
        return d.get("method_failures", 0) if x["id"] == cur else x.get("failures", 0)
    lines.append("锚点（通过关口一的机会）：" + ("、".join(f"{x['id']}（{'当前，' if x['id'] == cur else ''}方法周期失败 {fails(x)} 次）"
                                                 for x in anchors) if anchors else "无"))
    if d.get("tried_candidates"):
        lines.append("验证过未通过或被放弃的候选：" + "、".join(d["tried_candidates"]))
    fp = R / "findings.md"
    if fp.exists():
        fc = Counter(m.group(1) for m in re.finditer(r"^- \S+ · (\S+) · ", fp.read_text(encoding="utf-8"), re.M))
        lines.append("否定记录：" + "、".join(f"{k} {v} 条" for k, v in fc.most_common()))

    dur = defaultdict(float)
    for h1, h2 in zip(starts, starts[1:]):
        t1, t2 = parse_time(h1.get("time")), parse_time(h2.get("time"))
        if t1 and t2:
            dur[h1["stage"]] += (t2 - t1).total_seconds() / 3600
    if dur:
        lines += ["", "## 时间花在哪（阶段开始到下一阶段开始，小时）", "", "| 阶段 | 小时 |", "|---|---|"]
        lines += [f"| {k} | {v:.1f} |" for k, v in sorted(dur.items(), key=lambda kv: -kv[1])[:10]]

    hints = []
    g1 = gate_counts.get("gate1", Counter())
    if sum(g1.values()) >= 6 and g1.get("pass", 0) <= 1:
        hints.append("关口一跑了很多次却很少通过：检查候选是否都在自建的诊断设置里量、是否只来自“条件崩溃”一类；试试去掉特权输入、"
                     "效率这几类机会（method-design.md 第 2 节），已经量过差距的机会可以用 goto problems --seed '<机会>' 直接交给下一轮。")
    if len(cv.get("method_card", [])) >= 5 and not screen_out.get("有候选达到判据"):
        hints.append("方法卡写了很多版，初筛却没有一次达标：先按 method-design.md 第 4–6 节一轮出 6–8 个覆盖不同算子的候选，"
                     "热启动初筛，接近的细化，不要在单个方案上反复改卡。")
    first_pass = next((i for i, h in enumerate(hist) if h.get("stage") == "code_survey" and h.get("event") == "start"), None)
    if first_pass is not None and any(h.get("stage") == "problems" and h.get("event") == "start" for h in hist[first_pass:]):
        hints.append("有锚点之后又回到了找问题：确认锚点的提升空间是否真的被实验否定；没有被否定的锚点不必放弃，用 "
                     "goto methods --anchor <编号> 回到它。")
    g2 = gate_counts.get("gate2", Counter())
    if g2.get("fail", 0) >= 2:
        hints.append("关口二多次判换方法：读 gates.md 里的理由。方法有效但差距不够的，新流程判补强（保留方法、加强），只有撞车、"
                     "非创新清单和增益被对照复现才换方法。")
    rv = Counter(pl.get("conditions") for pl in cv.get("review", []) if "conditions" in pl)
    if rv.get("unmet", 0) >= 2:
        hints.append("机制探针多次落空：新流程先做初筛，关口二判的是有数字的方法；探针只在没有初筛时用，而且预测只写评测里能直接"
                     "看到的结果。")
    lines += ["", "## 提示", ""] + ([f"- {h}" for h in hints] if hints else ["- 没有触发规则。"])
    text = "\n".join(lines) + "\n"
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
