#!/usr/bin/env python3
"""Build a corpus of top-venue papers for writing-pattern statistics.

    python build_corpus.py --out research/corpus [--fields name=regex ...] [--venues cvpr,iccv,...]
                           [--years 2021-2026] [--target 1000] [--raw DIR]

Downloads Paper Copilot lists (https://github.com/papercopilot/paperlists) into <out>/raw on first use,
keeps papers whose title matches a sub-field regex (or whose abstract matches it three times),
and writes:
  all_matched.csv  every matching paper, accepted or rejected
  core.csv         stratified sample of accepted papers (target size, top-tier papers up to half per field)
  contrast.csv     rejected or withdrawn ICLR/NeurIPS submissions in the same fields
Default fields are the four used in references/writing-patterns.md.
"""
import argparse, csv, json, os, random, re, collections, urllib.request

BASE = "https://raw.githubusercontent.com/papercopilot/paperlists/main"
LFS = "https://media.githubusercontent.com/media/papercopilot/paperlists/main"
AVAILABLE = {
    "cvpr": [2021, 2022, 2023, 2024, 2025], "iccv": [2021, 2023, 2025], "eccv": [2022, 2024],
    "nips": [2021, 2022, 2023, 2024, 2025], "iclr": [2022, 2023, 2024, 2025, 2026],
    "corl": [2021, 2022, 2023, 2024, 2025], "rss": [2024, 2025], "icra": [2024], "iros": [2024], "3dv": [2025],
}
FOLDER = {"3dv": "3dv"}
GROUP = {"cvpr": "CV", "iccv": "CV", "eccv": "CV", "3dv": "CV", "nips": "ML", "iclr": "ML",
         "corl": "Robotics", "rss": "Robotics", "icra": "Robotics", "iros": "Robotics"}
DEFAULT_FIELDS = {
    "pano": r"panoram|\b360\s*(°|-?degree|deg)|\b360[- ]?(image|video|camera|view|scene|depth)|omnidirectional|"
            r"equirectangular|fisheye|fish-eye|spherical (image|camera|video)",
    "render": r"gaussian splat|\b3dgs\b|radiance field|\bnerfs?\b|novel[- ]view synthesis|neural rendering|neural radiance",
    "lidar": r"\blidar\b|\bradar\b|bird'?s[- ]eye[- ]view|\bbev\b|3d object detection|3d occupancy|semantic occupancy|"
             r"autonomous driving",
    "robot": r"robot|vision[- ]language[- ]action|\bvlas?\b|imitation learning|visuomotor|bimanual|dexterous|"
             r"\bgrasp(s|ing)?\b|teleoperat|embodied",
}
POINT_CLOUD = re.compile(r"point clouds?", re.I)
DRIVING_CTX = re.compile(r"\blidar\b|driving|autonomous|outdoor|street|\bradar\b|vehicle", re.I)
MANIP = re.compile(r"(?<!image )(?<!face )(?<!video )(?<!attribute )(?<!semantic )(?<!text )(?<!style )(?<!photo )"
                   r"manipulation", re.I)
ROBOT_CTX = re.compile(r"robot|polic(y|ies)|embodied|gripper|grasp|end-effector", re.I)
TOP = {"oral", "highlight", "spotlight", "award candidate", "top-5%", "top-25%"}


def tier(status):
    s = (status or "").strip().lower()
    if s in TOP:
        return "top"
    if s in {"poster", "accept", ""}:
        return "poster"
    if s in {"reject", "withdraw", "desk reject"}:
        return "rejected"
    return "other"


def fetch(venue, year, raw):
    os.makedirs(raw, exist_ok=True)
    path = os.path.join(raw, f"{venue}{year}.json")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return path
    for base in (BASE, LFS):
        url = f"{base}/{FOLDER.get(venue, venue)}/{venue}{year}.json"
        try:
            data = urllib.request.urlopen(url, timeout=300).read()
        except Exception as e:  # noqa: BLE001
            print(f"  download failed {url}: {e}")
            continue
        if data[:40].startswith(b"version https://git-lfs"):
            continue  # pointer file; try the LFS host
        open(path, "wb").write(data)
        return path
    return None


def matcher(fields, builtin):
    rx = {k: re.compile(v, re.I) for k, v in fields.items()}

    def match(title, abstract):
        out, text = [], title + " " + abstract
        for k, r in rx.items():
            t, a = len(r.findall(title)), len(r.findall(abstract))
            if builtin and k == "lidar" and DRIVING_CTX.search(text):
                t += len(POINT_CLOUD.findall(title)); a += len(POINT_CLOUD.findall(abstract))
            if builtin and k == "robot" and ROBOT_CTX.search(text):
                t += len(MANIP.findall(title)); a += len(MANIP.findall(abstract))
            if t >= 1 or a >= 3:
                out.append(k)
        return out
    return match


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--raw", help="directory with downloaded lists (default: <out>/raw)")
    ap.add_argument("--fields", nargs="*", help="name=regex; default: the four built-in fields")
    ap.add_argument("--venues", default=",".join(AVAILABLE))
    ap.add_argument("--years", default="2021-2026")
    ap.add_argument("--target", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    builtin = not a.fields
    fields = DEFAULT_FIELDS if builtin else dict(f.split("=", 1) for f in a.fields)
    y0, y1 = (int(x) for x in a.years.split("-"))
    raw = a.raw or os.path.join(a.out, "raw")
    match = matcher(fields, builtin)
    rows = []
    for v in a.venues.split(","):
        v = v.strip().lower()
        for y in AVAILABLE.get(v, []):
            if not (y0 <= y <= y1):
                continue
            path = fetch(v, y, raw)
            if not path:
                continue
            for p in json.load(open(path)):
                title = " ".join(str(p.get("title", "")).split())
                ab = " ".join(str(p.get("abstract", "")).split())
                if len(ab) < 300 or not title:
                    continue
                fl = match(title, ab)
                if not fl:
                    continue
                tr = str(p.get("track", "")).lower()
                if tr and "main" not in tr and v in ("nips", "iclr"):
                    continue
                gs = p.get("gs_citation")
                ra = p.get("rating_avg", "")
                if isinstance(ra, list):
                    ra = round(sum(ra) / len(ra), 3) if ra else ""
                rows.append({"venue": v, "year": y, "group": GROUP[v], "status": str(p.get("status", "")).strip(),
                             "tier": tier(p.get("status")), "fields": "|".join(fl), "title": title, "abstract": ab,
                             "gs_citation": gs if isinstance(gs, (int, float)) and gs >= 0 else "", "rating_avg": ra,
                             "arxiv": p.get("arxiv", ""), "pdf": p.get("pdf", ""), "site": p.get("site", ""),
                             "openreview": p.get("openreview", ""), "id": p.get("id", "")})
    seen = {}
    for r in rows:
        key = re.sub(r"[^a-z0-9]", "", r["title"].lower())
        if key not in seen or (seen[key]["tier"] == "rejected" and r["tier"] != "rejected"):
            seen[key] = r
    rows = list(seen.values())
    if not rows:
        raise SystemExit("no papers matched; check --fields, --venues and network access to raw.githubusercontent.com")
    os.makedirs(a.out, exist_ok=True)
    cols = list(rows[0].keys())

    def write(name, rs, extra=()):
        with open(os.path.join(a.out, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, cols + list(extra)); w.writeheader(); w.writerows(rs)

    write("all_matched.csv", rows)
    acc = [r for r in rows if r["tier"] in ("top", "poster")]
    rej = [r for r in rows if r["tier"] == "rejected" and r["venue"] in ("iclr", "nips")]
    write("contrast.csv", rej)
    rnd = random.Random(a.seed)
    per = max(1, a.target // len(fields))
    core, used = [], set()
    for k in fields:
        pool = [r for r in acc if k in r["fields"].split("|") and id(r) not in used]
        tops = [r for r in pool if r["tier"] == "top"]; posters = [r for r in pool if r["tier"] == "poster"]
        rnd.shuffle(tops); rnd.shuffle(posters)
        pick = tops[:min(len(tops), per // 2)]
        buckets = collections.defaultdict(list)
        for r in posters:
            buckets[(r["group"], r["year"])].append(r)
        keys, i = sorted(buckets), 0
        while len(pick) < per and any(buckets[x] for x in keys):
            b = buckets[keys[i % len(keys)]]
            if b:
                pick.append(b.pop())
            i += 1
        for r in pick:
            used.add(id(r)); core.append(dict(r, primary=k))
    write("core.csv", core, extra=("primary",))
    print(f"matched {len(rows)}, accepted {len(acc)}, rejected ICLR/NeurIPS {len(rej)}, core {len(core)}")
    print("core by field and tier:", dict(collections.Counter((r["primary"], r["tier"]) for r in core)))


if __name__ == "__main__":
    main()
