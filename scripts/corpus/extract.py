#!/usr/bin/env python3
"""Compute title/abstract features: python extract.py --dir research/corpus"""
import argparse, csv, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from features import features  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dir", required=True)
a = ap.parse_args()
for name in ["core", "all_matched"]:
    path = os.path.join(a.dir, f"{name}.csv")
    if not os.path.exists(path):
        continue
    rows = list(csv.DictReader(open(path)))
    out = [{**{k: r[k] for k in r if k != "abstract"}, **features(r["title"], r["abstract"])} for r in rows]
    with open(os.path.join(a.dir, f"{name}_features.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, list(out[0].keys())); w.writeheader(); w.writerows(out)
    print(name, len(out))
