#!/usr/bin/env python3
"""Opportunity types and method operators in accepted papers: python ops.py --dir research/writing_profile [--md out.md]

Reads <dir>/all_matched.csv (from build_corpus.py), keeps accepted papers (tier poster/top), and tags each title+abstract
with keyword rules: the opportunity it claims (robustness under conditions, removing a privileged input or supervision,
efficiency, a new capability or setting, unification) and the method operators it names (representation, exact
computation, priors and constraints, supervision source, joint optimisation, uncertainty, adaptive allocation,
decoupling, foundation models, temporal or multi-view consistency, fusion, hierarchy). Also counts evidence norms
(state-of-the-art claims, real-world experiments, named benchmarks). Writes <dir>/ops.json with shares by field and
venue group plus the most-cited example titles per field and tag.

Keyword rules see only what an abstract says: shares are lower bounds of how often an operator is used and are meant
for comparing fields, venues and operators, not as exact rates.
"""
import argparse
import csv
import json
import os
import re
from collections import Counter

F = re.I
OPP = {
    "T1 条件崩溃与泛化": r"\b(robust(ness)?|challenging (conditions|scenarios|environments|cases|scenes)|adverse|degrad\w*|fail(s|ure|ures|ing)?\b|struggl\w*|corner[- ]cases?|long[- ]tail\w*|out[- ]of[- ]distribution|occlu\w*|low[- ]light|night\w*|weather|noisy|sparse (points?|observations?|views?|inputs?)|few[- ]shot|unseen|generali[sz]\w*)",
    "T2 去掉特权输入或监督": r"\b(without (any |the |requiring |the need (of|for) |relying on |access to )?(ground[- ]truth|gt\b|calibration|camera pos\w*|pos(e|es)\b|depth|labels?|annotations?|supervision|lidar|known|explicit|pre-?computed|privileged|expert)|(self|un|weakly|semi)[- ]supervised|label[- ]efficient|annotation[- ]free|calibration[- ]free|pose[- ]free|target[- ]free|map[- ]free|marker[- ]?less|colmap[- ]free|does not require|do not require|no need for|eliminat\w+ the need|privileged information)",
    "T3 效率": r"\b(real[- ]time|efficien\w*|faster|speed[- ]?ups?|\d+(\.\d+)?\s?(x|×)\s(faster|speed\w*|less|fewer|smaller)|lightweight|low[- ]latency|latency|memory[- ](efficient|footprint|consumption)|compact|compress\w*|\bfps\b|on[- ]board|edge devices?|computational(ly)? (cost|efficient|overhead|burden))",
    "T4 新能力或新设定": r"\b(for the first time|(the )?first (method|approach|framework|work|system|attempt|to)\b|novel (task|setting|problem)|new (task|setting|problem|paradigm|capabilit\w+)|enabl(e|es|ing) (robots?|agents?|the robot) to|previously (impossible|infeasible|unexplored|unattainable))",
    "T5 统一或简化": r"\b(unified|unif(y|ies|ying)|single (model|network|framework|policy|stage)|end[- ]to[- ]end|all[- ]in[- ]one|simple (yet|but) (effective|strong|powerful)|simplif\w+)",
}
OPS = {
    "O1 换表示": r"\b((novel|new|hybrid|compact|explicit|implicit|structured|unified|continuous|hierarchical|spherical|sparse|neural|disentangled|object[- ]centric|semantic|layered)\s+([\w-]+\s+)?representations?\b|we (propose to )?represent\b|represent(s|ing)? (each|the scene|scenes|objects?|maps?|the environment)\s+(as|with|by)\b)",
    "O2 去近似或精确化": r"\b(approximation errors?|inaccurate approximations?|(crude|coarse|first[- ]order|local) approximations?|exact (rendering|projection|computation|solution|formulation|gradient\w*|integration)|closed[- ]form|analytic(al|ally)? (solution|formulation|gradient\w*|derivation|model)|anti[- ]alias\w*|aliasing|distortion[- ]aware|unbiased|(projection|approximation|estimation) (bias|error)s?|precise (projection|rendering|modeling|geometry))",
    "O3 先验或约束": r"\b((geometric|geometry|depth|shape|physical|physics|semantic|learned|strong|structural|diffusion|generative|kinematic|motion|spatial|temporal|data[- ]driven|monocular|normal|scene|appearance|visual|3d|2d|human|map|planar|smoothness|sparsity|low[- ]rank)\s+priors?\b|prior (knowledge|information|distributions?|models?)\b|regulari[sz]\w+|(geometric|physical|kinematic|dynamic|contact|collision|consistency|safety|motion|structural|symmetry|planarity|smoothness) constraints?\b|physic(s|al(ly)?)[- ](informed|based|aware|consistent|constrained|plausib\w+)|geometric consistency)",
    "O4 新监督来源或蒸馏": r"\b(self[- ]supervis\w+|distill\w*|teacher|pseudo[- ]labels?\w*|contrastive|cross[- ]modal (supervision|distillation|learning)|weak(ly)? supervis\w+|supervision (signals?|from)|label[- ]free|reward (models?|shaping|learning))",
    "O5 联合求解或可微化": r"\b(joint(ly)? (optimi[sz]\w+|estimat\w+|learn\w*|reason\w*|solv\w*|model\w*|train\w*|predict\w*)|simultaneous(ly)? (optimi[sz]\w+|estimat\w+|locali[sz]\w+|recover\w*)|tightly[- ]coupled|end[- ]to[- ]end (optimi[sz]\w+|differentiable|trainable)|differentiable)",
    "O6 不确定性或可靠性": r"\b(uncertaint\w+|confidence (estimat\w+|scores?|maps?|weight\w*|aware|calibrat\w+|measures?)|reliability (estimat\w+|weight\w*|aware|maps?|scores?)|probabilistic|bayesian|aleatoric|epistemic|risk[- ]aware)",
    "O7 自适应分配": r"\b(adaptive(ly)?\b|dynamic(ally)? (allocat\w+|select\w+|adjust\w+|weight\w+|sampl\w+)|learn(ed|able) (sampling|selection|weighting)|densif\w+|prun(e|es|ed|ing)\b|sparse (convolution|attention|computation|quer(y|ies))|importance sampling)",
    "O8 解耦或分解": r"\b(decoupl\w+|disentangl\w+|factori[sz]\w+|decompos\w+|separat(e|es|ing) (the )?(static|dynamic|geometry|appearance|motion|semantic|foreground|background))",
    "O9 借基础模型或相邻领域": r"\b(foundation models?|pre[- ]?trained (vision|language|visual|2d|image|diffusion|large|video)|vision[- ]language|large language models?|\bllms?\b|\bvlms?\b|diffusion (models?|priors?|polic\w+)|\bclip\b|segment anything|\bsam\b|\bdino\w*|stable diffusion|video (generation|diffusion) models?)",
    "O10 时间或多视一致": r"\b(temporal(ly)? (consisten\w+|coheren\w+|fusion|aggregat\w+|context|information)|multi[- ]view consisten\w+|cross[- ]view|cycle[- ]consisten\w+|spatio[- ]?temporal|across (frames|views|time steps|timesteps))",
    "O11 多模态融合": r"\b(fus(e|es|ed|ing|ion)\b|multi[- ]?modal\w*|camera[- ](lidar|radar)|lidar[- ]camera|radar[- ]camera|sensor fusion)",
    "O12 分层或由粗到细": r"\b(hierarch\w+|coarse[- ]to[- ]fine|multi[- ]scale|multi[- ]resolution|multi[- ]level|pyramid)",
}
NORMS = {
    "sota": r"\b(state[- ]of[- ]the[- ]art|outperform\w*|surpass\w*|sota)\b",
    "real_world": r"\b(real[- ]world (experiments?|robots?|deployment|tests?|evaluation|trials?|tasks?)|real robots?|physical robots?|hardware (experiments?|deployment|platform)|on a real)\b",
    "benchmark_named": r"\b(nuscenes|kitti|waymo|view[- ]of[- ]delft|vod\b|tj4d\w*|k-radar|semantickitti|mip-nerf ?360|tanks and temples|deep blending|libero|calvin|robotwin|simplerenv|scannet\w*|replica|matterport\w*|stanford2d3d|structured3d|benchmarks?)\b",
    "plug_in": r"\b(plug[- ]and[- ]play|plug[- ]in|drop[- ]in|seamlessly integrat\w+|can be (easily |seamlessly )?(integrated|incorporated|applied|plugged) (in)?to|compatible with (existing|various|most|off-the-shelf)|model[- ]agnostic|backbone[- ]agnostic)",
}
GROUPS = {"机器人会议": {"icra", "iros", "corl", "rss"}, "视觉会议": {"cvpr", "iccv", "eccv", "3dv", "wacv"},
          "学习会议": {"iclr", "nips", "neurips", "icml"}, "图形会议": {"siggraph", "siggraphasia"}}


def vgroup(v):
    return next((g for g, s in GROUPS.items() if v.lower() in s), "其他")


def tag(r):
    a = f"{r['title']}. {r['abstract']}"
    return ({k for k, p in OPP.items() if re.search(p, a, F)}, {k for k, p in OPS.items() if re.search(p, a, F)},
            {k for k, p in NORMS.items() if re.search(p, a, F)})


def share(rs):
    n = len(rs)
    co, cp, cn, nops = Counter(), Counter(), Counter(), []
    for r in rs:
        t, o, m = r["_tags"]
        cp.update(t)
        co.update(o)
        cn.update(m)
        nops.append(len(o))
    return {"n": n, "opp": {k: round(cp[k] / n, 3) for k in OPP}, "ops": {k: round(co[k] / n, 3) for k in OPS},
            "norms": {k: round(cn[k] / n, 3) for k in NORMS}, "ops_ge2": round(sum(x >= 2 for x in nops) / n, 3)}


def cites(r):
    try:
        return float(r.get("gs_citation") or 0)
    except ValueError:
        return 0.0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="corpus folder with all_matched.csv (build_corpus.py output)")
    ap.add_argument("--min-n", type=int, default=60, help="smallest group reported")
    ap.add_argument("--md", help="also write a markdown summary here")
    a = ap.parse_args()
    rows = [r for r in csv.DictReader(open(os.path.join(a.dir, "all_matched.csv"), encoding="utf-8"))
            if r.get("tier") in ("poster", "top") and len(r.get("abstract") or "") > 200]
    for r in rows:
        r["_tags"] = tag(r)
        r["_fields"] = [f for f in (r.get("fields") or "").split("|") if f]
    fields = sorted({f for r in rows for f in r["_fields"]})
    out = {"source": f"{a.dir}/all_matched.csv, accepted (tier poster/top), keyword rules on title+abstract",
           "n": len(rows), "overall": share(rows), "by_field": {}, "by_field_venue": {}, "examples": {}}
    for f in fields:
        sub = [r for r in rows if f in r["_fields"]]
        if len(sub) >= a.min_n:
            out["by_field"][f] = share(sub)
        for g in GROUPS:
            s2 = [r for r in sub if vgroup(r["venue"]) == g]
            if len(s2) >= a.min_n:
                out["by_field_venue"][f"{f}/{g}"] = share(s2)
        for k in list(OPS) + list(OPP):
            hits = sorted((r for r in sub if k in r["_tags"][0] | r["_tags"][1]), key=cites, reverse=True)
            out["examples"][f"{f}|{k}"] = [[r["title"], r["venue"].upper(), r["year"]] for r in hits[:5]]
    with open(os.path.join(a.dir, "ops.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    groups = [("全部", out["overall"])] + list(out["by_field"].items()) + list(out["by_field_venue"].items())
    head = "| 组 | n | " + " | ".join(k.split()[1] for k in OPS) + " |"
    lines = ["## 方法算子（摘要提到的比例）", "", head, "|" + "---|" * (len(OPS) + 2)]
    lines += [f"| {g} | {s['n']} | " + " | ".join(f"{s['ops'][k]:.0%}" for k in OPS) + " |" for g, s in groups]
    lines += ["", "## 机会类型与证据习惯", "", "| 组 | n | " + " | ".join(k.split()[1] for k in OPP)
              + " | 声称 SOTA | 真机实验 | 点名 benchmark | 可插拔 |", "|" + "---|" * (len(OPP) + 6)]
    lines += [f"| {g} | {s['n']} | " + " | ".join(f"{s['opp'][k]:.0%}" for k in OPP) + " | "
              + " | ".join(f"{s['norms'][k]:.0%}" for k in ("sota", "real_world", "benchmark_named", "plug_in")) + " |"
              for g, s in groups]
    text = "\n".join(lines) + "\n"
    print(text)
    if a.md:
        open(a.md, "w", encoding="utf-8").write(text)


if __name__ == "__main__":
    main()
