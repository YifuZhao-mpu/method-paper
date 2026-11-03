#!/usr/bin/env python3
"""Descriptive statistics and stratified contrasts: python stats.py --dir research/corpus [--boot 1000]

Writes <dir>/stats.json: core description (overall, by field, by venue group), oral/highlight vs poster,
ICLR/NeurIPS accepted vs rejected, within-venue-year citation quartiles, and year trends.
Differences are weighted means of within-stratum differences (venue x year x field), bootstrap CIs, BH-FDR q-values.
"""
import csv, json, math, os, random, statistics, collections

ROOT = "."
FIELD_ORDER = []  # filled from the data

BIN = ["has_G", "gap_before_method", "gap_cause", "insight", "quant_result", "sota_claim", "general_claim",
       "effic_claim", "code_release", "extensive_phrase", "first_claim", "realworld", "named_in_abstract",
       "bench_named", "title_colon_name", "method_first_sentence", "has_R", "has_C", "has_B", "any_question",
       "any_hedge", "any_weak"]
CONT = ["words", "n_sent", "words_per_sent", "n_numbers", "hedge_n", "weak_n", "title_words", "first_M_pos", "n_M",
        "n_G", "n_B", "n_R", "we_n"]


def load(name):
    rows = list(csv.DictReader(open(os.path.join(ROOT, name))))
    for r in rows:
        for k in BIN + CONT:
            if k in r and r[k] != "":
                r[k] = float(r[k])
        r["any_question"] = float(float(r["question_n"]) > 0)
        r["any_hedge"] = float(float(r["hedge_n"]) > 0)
        r["any_weak"] = float(float(r["weak_n"]) > 0)
        r["field1"] = r.get("primary") or next((f for f in FIELD_ORDER if f in r["fields"].split("|")), r["fields"].split("|")[0])
        r["year"] = int(r["year"])
        r["gs"] = float(r["gs_citation"]) if r.get("gs_citation") not in ("", None) else None
    return rows


def pct(x):
    return round(100 * x, 1)


def describe(rows, label):
    out = {"label": label, "n": len(rows)}
    for k in BIN:
        vals = [r[k] for r in rows if r.get(k) != ""]
        out[k] = pct(sum(vals) / len(vals)) if vals else None
    for k in CONT:
        vals = sorted(r[k] for r in rows if r.get(k) != "")
        if vals:
            q = statistics.quantiles(vals, n=4) if len(vals) > 3 else [vals[0], statistics.median(vals), vals[-1]]
            out[k] = {"median": round(statistics.median(vals), 2), "q1": round(q[0], 2), "q3": round(q[-1], 2),
                      "mean": round(statistics.mean(vals), 2)}
    out["moves_top"] = collections.Counter(r["moves_c"] for r in rows).most_common(10)
    out["first_move"] = {k: pct(v / len(rows)) for k, v in collections.Counter(r["first_move"] for r in rows).items()}
    out["title_lead"] = collections.Counter(r["title_lead"] for r in rows if r["title_lead"]).most_common(8)
    return out


def stratified_diff(rows, group_fn, strata_fn, feats, n_boot=None, seed=0):
    """group_fn -> 1 / 0 / None. Weighted mean of within-stratum differences (w = n1 n0 / (n1 + n0))."""
    strata = collections.defaultdict(lambda: ([], []))
    for r in rows:
        g = group_fn(r)
        if g is None:
            continue
        strata[strata_fn(r)][0 if g == 1 else 1].append(r)
    strata = {k: v for k, v in strata.items() if len(v[0]) >= 3 and len(v[1]) >= 3}
    n1 = sum(len(v[0]) for v in strata.values()); n0 = sum(len(v[1]) for v in strata.values())
    n_boot = n_boot or globals().get("N_BOOT", 1000)
    n_boot_req = n_boot
    rnd = random.Random(seed)
    res = {}
    for f in feats:
        def est(st):
            num = den = 0.0
            for a, b in st.values():
                va = [r[f] for r in a if r.get(f) != ""]; vb = [r[f] for r in b if r.get(f) != ""]
                if len(va) < 2 or len(vb) < 2:
                    continue
                w = len(va) * len(vb) / (len(va) + len(vb))
                num += w * (statistics.mean(va) - statistics.mean(vb)); den += w
            return num / den if den else float("nan")
        point = est(strata)
        if not strata or math.isnan(point):
            res[f] = {"diff": None, "ci": None, "p": 1.0, "mean1": None, "mean0": None, "note": "too few papers"}
            continue
        boots = []
        for _ in range(n_boot):
            st = {k: ([rnd.choice(a) for _ in a], [rnd.choice(b) for _ in b]) for k, (a, b) in strata.items()}
            b = est(st)
            if not math.isnan(b):
                boots.append(b)
        if len(boots) < 20:
            res[f] = {"diff": None, "ci": None, "p": 1.0, "mean1": None, "mean0": None, "note": "too few papers"}
            continue
        n_boot = len(boots)
        boots.sort()
        lo, hi = boots[int(0.025 * n_boot)], boots[int(0.975 * n_boot) - 1]
        se = statistics.pstdev(boots) or 1e-9
        z = point / se
        p = math.erfc(abs(z) / math.sqrt(2))
        n_boot = n_boot_req
        g1 = [r[f] for v in strata.values() for r in v[0] if r.get(f) != ""]
        g0 = [r[f] for v in strata.values() for r in v[1] if r.get(f) != ""]
        scale = 100 if f in BIN else 1
        res[f] = {"diff": round(point * scale, 2), "ci": [round(lo * scale, 2), round(hi * scale, 2)], "p": p,
                  "mean1": round(statistics.mean(g1) * scale, 2), "mean0": round(statistics.mean(g0) * scale, 2)}
    # Benjamini-Hochberg across features
    ps = sorted(((v["p"], k) for k, v in res.items()))
    m = len(ps)
    adj = {}
    prev = 1.0
    for i in range(m - 1, -1, -1):
        p, k = ps[i]
        prev = min(prev, p * m / (i + 1))
        adj[k] = prev
    for k in res:
        res[k]["q"] = round(adj[k], 4)
        res[k]["p"] = round(res[k]["p"], 4)
    return {"n1": n1, "n0": n0, "strata": len(strata), "features": res}


def main():
    import argparse
    global ROOT, FIELD_ORDER
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    ROOT = a.dir
    head = list(csv.DictReader(open(os.path.join(ROOT, "core.csv"))))
    FIELD_ORDER = list(dict.fromkeys(r["primary"] for r in head))
    globals()["N_BOOT"] = a.boot
    core = load("core_features.csv")
    allm = load("all_matched_features.csv")
    out = {}
    out["core_types"] = dict(collections.Counter(r["type"] for r in core))
    cm = [r for r in core if r["type"] == "method"]
    out["core_method_overall"] = describe(cm, "core method papers")
    out["core_method_by_field"] = {f: describe(x, f) for f in FIELD_ORDER if (x := [r for r in cm if r["field1"] == f])}
    out["core_method_by_group"] = {g: describe(x, g) for g in ["CV", "ML", "Robotics"] if (x := [r for r in cm if r["group"] == g])}

    acc = [r for r in allm if r["tier"] in ("top", "poster") and r["type"] == "method"]
    feats = BIN + CONT
    out["top_vs_poster"] = stratified_diff(
        acc, lambda r: 1 if r["tier"] == "top" else 0, lambda r: (r["venue"], r["year"], r["field1"]), feats)
    mlrows = [r for r in allm if r["venue"] in ("iclr", "nips") and r["type"] == "method" and r["tier"] in ("top", "poster", "rejected")]
    out["accepted_vs_rejected"] = stratified_diff(
        mlrows, lambda r: 0 if r["tier"] == "rejected" else 1, lambda r: (r["venue"], r["year"], r["field1"]), feats)
    # citations: within venue-year quartiles, 2021-2024
    cit = [r for r in acc if r["gs"] is not None and r["year"] <= 2024]
    byvy = collections.defaultdict(list)
    for r in cit:
        byvy[(r["venue"], r["year"])].append(r)
    for k, v in byvy.items():
        vals = sorted(x["gs"] for x in v)
        if len(vals) < 8:
            for x in v: x["citq"] = None
            continue
        q1, q3 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
        for x in v:
            x["citq"] = 1 if x["gs"] >= q3 and x["gs"] > q1 else (0 if x["gs"] <= q1 and x["gs"] < q3 else None)
    out["cited_top_vs_bottom_quartile"] = stratified_diff(
        cit, lambda r: r.get("citq"), lambda r: (r["venue"], r["year"], r["field1"]), feats)
    # trends by year (accepted method papers, all matched)
    trend = {}
    for y in range(2021, 2027):
        ry = [r for r in acc if r["year"] == y]
        if not ry:
            continue
        trend[y] = {"n": len(ry), **{k: pct(sum(r[k] for r in ry) / len(ry)) for k in
                                      ["gap_before_method", "gap_cause", "insight", "quant_result", "sota_claim",
                                       "general_claim", "effic_claim", "code_release", "title_colon_name",
                                       "named_in_abstract", "realworld", "first_claim", "any_hedge"]},
                    "words_median": statistics.median(r["words"] for r in ry)}
    out["trend"] = trend
    json.dump(out, open(os.path.join(ROOT, "stats.json"), "w"), indent=1, ensure_ascii=False)
    print("written", os.path.join(ROOT, "stats.json"))


if __name__ == "__main__":
    main()
