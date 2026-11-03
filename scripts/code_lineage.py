#!/usr/bin/env python3
"""How was a paper's open-source code put together? Clone the repositories of the closest works and report which
codebases each one builds on, which base files it changed and by how much, and how much new code it adds.

    python code_lineage.py --repos owner/repo [owner/repo ...] --out research/code_survey
    python code_lineage.py --from-md research/gates.md research/problem_cards.md --out research/code_survey
    python code_lineage.py --list repos.txt --out DIR [--jobs 4] [--keep]

For every repository it
  1. clones only the code and text files (shallow, sparse, blobs fetched on demand);
  2. reads README acknowledgments ("based on", "built upon", "borrowed from" ...) and .gitmodules;
  3. counts third-party imports and imports of known research codebases (dependency use);
  4. finds vendored copies of known codebases by their characteristic files, picks the base commit whose files match
     the copy best (exact git blob hashes over the base history), then counts unchanged, modified (+/- lines) and
     removed base files, and the new code outside the base;
  5. follows submodules that point to a modified fork of a known codebase (for example a changed CUDA rasterizer).

Writes DIR/lineage.json and DIR/lineage.md (the markdown report is what the method reasoning stage reads).
Needs git >= 2.27 and network access to github.com. Standard library only.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime as dt
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

CODE_EXT = {".py", ".pyx", ".cu", ".cuh", ".cpp", ".cc", ".cxx", ".c", ".h", ".hpp", ".hh"}
CFG_EXT = {".yaml", ".yml", ".toml", ".cfg", ".sh"}
SPARSE = ["*.py", "*.pyx", "*.cu", "*.cuh", "*.cpp", "*.cc", "*.cxx", "*.c", "*.h", "*.hpp", "*.hh", "*.yaml", "*.yml",
          "*.toml", "*.cfg", "*.sh", "*.md", "*.rst", "README*", "LICENSE*", ".gitmodules", "requirements*.txt",
          "CMakeLists.txt", "setup.py", "environment*.yml"]
THIRD_PARTY_DIRS = re.compile(r"(^|/)(third_party|thirdparty|third-party|3rdparty|external|externals|vendor|deps)(/|$)", re.I)

# name -> repo, characteristic files (relative to the codebase root), import names, field
KNOWN = {
    "gaussian-splatting": {"repo": "graphdeco-inria/gaussian-splatting", "field": "render", "imports": [],
                           "files": ["scene/gaussian_model.py", "gaussian_renderer/__init__.py", "utils/sh_utils.py",
                                     "scene/dataset_readers.py", "scene/cameras.py", "utils/graphics_utils.py",
                                     "arguments/__init__.py", "utils/loss_utils.py", "train.py", "render.py"]},
    "diff-gaussian-rasterization": {"repo": "graphdeco-inria/diff-gaussian-rasterization", "field": "render",
                                    "imports": [],
                                    "files": ["cuda_rasterizer/forward.cu", "cuda_rasterizer/backward.cu",
                                              "cuda_rasterizer/rasterizer_impl.cu", "rasterize_points.cu", "ext.cpp",
                                              "diff_gaussian_rasterization/__init__.py", "cuda_rasterizer/auxiliary.h"]},
    "nerf-pytorch": {"repo": "yenchenlin/nerf-pytorch", "field": "render", "imports": [],
                     "files": ["run_nerf.py", "run_nerf_helpers.py", "load_llff.py", "load_blender.py"]},
    "torch-ngp": {"repo": "ashawkey/torch-ngp", "field": "render", "imports": [],
                  "files": ["nerf/network.py", "nerf/renderer.py", "nerf/utils.py", "nerf/provider.py",
                            "raymarching/raymarching.py"]},
    "multinerf": {"repo": "google-research/multinerf", "field": "render", "imports": [],
                  "files": ["internal/models.py", "internal/train_utils.py", "internal/render.py", "internal/datasets.py"]},
    "threestudio": {"repo": "threestudio-project/threestudio", "field": "render", "imports": ["threestudio"],
                    "files": ["threestudio/systems/base.py", "threestudio/models/geometry/base.py", "launch.py",
                              "threestudio/utils/base.py"]},
    "dust3r": {"repo": "naver/dust3r", "field": "render", "imports": ["dust3r"],
               "files": ["dust3r/model.py", "dust3r/inference.py", "dust3r/losses.py", "dust3r/cloud_opt/base_opt.py"]},
    "nerfstudio": {"repo": "nerfstudio-project/nerfstudio", "field": "render", "imports": ["nerfstudio"], "files": []},
    "gsplat": {"repo": "nerfstudio-project/gsplat", "field": "render", "imports": ["gsplat"], "files": []},
    "tiny-cuda-nn": {"repo": "NVlabs/tiny-cuda-nn", "field": "render", "imports": ["tinycudann"], "files": []},
    "OpenPCDet": {"repo": "open-mmlab/OpenPCDet", "field": "lidar", "imports": ["pcdet"],
                  "files": ["pcdet/models/detectors/detector3d_template.py", "pcdet/datasets/dataset.py",
                            "pcdet/models/backbones_3d/spconv_backbone.py", "pcdet/models/dense_heads/anchor_head_template.py",
                            "tools/train.py", "pcdet/utils/common_utils.py", "pcdet/datasets/augmentor/data_augmentor.py"]},
    "mmdetection3d": {"repo": "open-mmlab/mmdetection3d", "field": "lidar", "imports": ["mmdet3d"],
                      "files": ["mmdet3d/models/detectors/base.py", "mmdet3d/datasets/nuscenes_dataset.py",
                                "mmdet3d/models/dense_heads/centerpoint_head.py", "mmdet3d/core/bbox/structures/lidar_box3d.py",
                                "mmdet3d/structures/bbox_3d/lidar_box3d.py", "mmdet3d/datasets/kitti_dataset.py",
                                "mmdet3d/models/voxel_encoders/voxel_encoder.py"]},
    "BEVFormer": {"repo": "fundamentalvision/BEVFormer", "field": "lidar", "imports": [],
                  "files": ["projects/mmdet3d_plugin/bevformer/modules/encoder.py",
                            "projects/mmdet3d_plugin/bevformer/modules/spatial_cross_attention.py",
                            "projects/mmdet3d_plugin/bevformer/detectors/bevformer.py"]},
    "BEVDet": {"repo": "HuangJunJie2017/BEVDet", "field": "lidar", "imports": [],
               "files": ["mmdet3d/models/necks/view_transformer.py", "mmdet3d/models/detectors/bevdet.py"]},
    "bevfusion-mit": {"repo": "mit-han-lab/bevfusion", "field": "lidar", "imports": [],
                      "files": ["mmdet3d/models/fusion_models/bevfusion.py", "mmdet3d/models/fusers/conv.py",
                                "mmdet3d/ops/bev_pool/bev_pool.py"]},
    "CenterPoint": {"repo": "tianweiy/CenterPoint", "field": "lidar", "imports": ["det3d"],
                    "files": ["det3d/models/detectors/base.py", "det3d/models/bbox_heads/center_head.py",
                              "det3d/datasets/nuscenes/nuscenes.py"]},
    "Pointcept": {"repo": "Pointcept/Pointcept", "field": "lidar", "imports": ["pointcept"],
                  "files": ["pointcept/models/builder.py", "pointcept/engines/train.py", "pointcept/datasets/builder.py",
                            "pointcept/models/point_transformer_v3/point_transformer_v3m1_base.py"]},
    "mmdetection": {"repo": "open-mmlab/mmdetection", "field": "lidar", "imports": ["mmdet"], "files": []},
    "spconv": {"repo": "traveller59/spconv", "field": "lidar", "imports": ["spconv"], "files": []},
    "MinkowskiEngine": {"repo": "NVIDIA/MinkowskiEngine", "field": "lidar", "imports": ["MinkowskiEngine"], "files": []},
    "diffusion_policy": {"repo": "real-stanford/diffusion_policy", "field": "robot", "imports": ["diffusion_policy"],
                         "files": ["diffusion_policy/policy/diffusion_unet_image_policy.py",
                                   "diffusion_policy/model/diffusion/conditional_unet1d.py",
                                   "diffusion_policy/workspace/train_diffusion_unet_image_workspace.py",
                                   "diffusion_policy/model/vision/multi_image_obs_encoder.py",
                                   "diffusion_policy/dataset/base_dataset.py"]},
    "robomimic": {"repo": "ARISE-Initiative/robomimic", "field": "robot", "imports": ["robomimic"],
                  "files": ["robomimic/algo/bc.py", "robomimic/models/obs_nets.py", "robomimic/utils/train_utils.py",
                            "robomimic/config/base_config.py"]},
    "act": {"repo": "tonyzhaozh/act", "field": "robot", "imports": [],
            "files": ["imitate_episodes.py", "policy.py", "detr/models/detr_vae.py", "detr/models/transformer.py",
                      "utils.py"]},
    "lerobot": {"repo": "huggingface/lerobot", "field": "robot", "imports": ["lerobot"], "files": []},
    "openpi": {"repo": "Physical-Intelligence/openpi", "field": "robot", "imports": ["openpi"],
               "files": ["src/openpi/models/pi0.py", "src/openpi/training/config.py", "src/openpi/policies/policy.py"]},
    "openvla": {"repo": "openvla/openvla", "field": "robot", "imports": ["prismatic"],
                "files": ["prismatic/models/vlas/openvla.py", "prismatic/vla/datasets/datasets.py", "vla-scripts/finetune.py"]},
    "octo": {"repo": "octo-models/octo", "field": "robot", "imports": ["octo"],
             "files": ["octo/model/octo_model.py", "octo/model/octo_module.py", "scripts/finetune.py"]},
    "legged_gym": {"repo": "leggedrobotics/legged_gym", "field": "robot", "imports": ["legged_gym"],
                   "files": ["legged_gym/envs/base/legged_robot.py", "legged_gym/envs/base/legged_robot_config.py",
                             "legged_gym/utils/task_registry.py"]},
    "rsl_rl": {"repo": "leggedrobotics/rsl_rl", "field": "robot", "imports": ["rsl_rl"],
               "files": ["rsl_rl/algorithms/ppo.py", "rsl_rl/runners/on_policy_runner.py", "rsl_rl/modules/actor_critic.py"]},
    "habitat": {"repo": "facebookresearch/habitat-lab", "field": "robot", "imports": ["habitat", "habitat_sim"], "files": []},
    "deoxys": {"repo": "UT-Austin-RPL/deoxys_control", "field": "robot", "imports": ["deoxys"], "files": []},
    "detectron2": {"repo": "facebookresearch/detectron2", "field": "lidar", "imports": ["detectron2"], "files": []},
    "robosuite": {"repo": "ARISE-Initiative/robosuite", "field": "robot", "imports": ["robosuite"], "files": []},
    "libero": {"repo": "Lifelong-Robot-Learning/LIBERO", "field": "robot", "imports": ["libero"], "files": []},
    "maniskill": {"repo": "haosulab/ManiSkill", "field": "robot", "imports": ["mani_skill", "mani_skill2"], "files": []},
    "isaac": {"repo": "isaac-sim/IsaacLab", "field": "robot", "imports": ["isaacgym", "isaaclab", "omni"], "files": []},
    "stable-baselines3": {"repo": "DLR-RM/stable-baselines3", "field": "robot", "imports": ["stable_baselines3"], "files": []},
}
GENERIC_LIBS = {"torch", "numpy", "scipy", "cv2", "PIL", "matplotlib", "tqdm", "yaml", "einops", "imageio", "skimage",
                "sklearn", "pandas", "h5py", "wandb", "tensorboard", "tensorboardX", "omegaconf", "hydra", "click",
                "termcolor", "plyfile", "trimesh", "open3d", "jax", "flax", "optax", "tensorflow", "torchvision",
                "lpips", "kornia", "timm", "transformers", "diffusers", "pytorch_lightning", "lightning", "accelerate",
                "mmcv", "mmengine", "mmseg", "pytorch3d", "gym", "gymnasium", "mujoco", "dm_control", "pybullet",
                "numba", "pycocotools", "nuscenes", "waymo_open_dataset", "pyquaternion", "shapely", "easydict",
                "huggingface_hub", "datasets", "peft", "safetensors", "clip", "open_clip", "sentencepiece", "rich",
                "setuptools", "torch_scatter", "torch_cluster", "pointnet2_ops", "simple_knn", "tyro", "viser"}
# folders named after a known library are vendored copies of it, not new code
LIB_DIRS = (GENERIC_LIBS - {"setuptools", "clip"}) | {m for i in KNOWN.values() for m in i["imports"]} | {
    "robosuite", "robomimic", "mmcv", "mmdet", "detectron2", "segment_anything", "groundingdino", "open_clip", "pytorch3d",
    "nerfstudio", "gsplat", "isaacgym", "legged_gym", "rsl_rl", "mujoco_menagerie", "dinov2", "sam2", "depth_anything",
    "taming", "ldm", "lpips", "pointnet2", "pointnet2_ops_lib", "chamfer_dist", "emd", "knn_cuda", "Pointnet2_PyTorch"}
GH_RE = re.compile(r"github\.com/([A-Za-z0-9][A-Za-z0-9_.\-]*)/([A-Za-z0-9_.\-]+)")
NOT_OWNERS = {"user-attachments", "orgs", "topics", "features", "apps", "settings", "sponsors", "marketplace", "login",
              "about", "pricing", "collections", "explore", "issues", "pulls", "notifications", "site", "security",
              "enterprise", "users", "assets", "search", "trending", "readme", "customer-stories"}


def gh_repos(text):
    """owner/repo pairs in free text (markdown-escaped underscores restored, GitHub's own pages skipped)."""
    out = []
    for m in GH_RE.finditer(text.replace("\\_", "_")):
        if m.group(1).lower() in NOT_OWNERS:
            continue
        r = norm_repo(m.group(1), m.group(2))
        if not r.lower().endswith(".github.io"):
            out.append(r)
    return out
ACK_RE = re.compile(r"(based on|built (up)?on|build (up)?on|borrow|adapted from|modified from|derived from|codebase|"
                    r"code base|heavily (rely|relies|based)|thanks? (to )?the|acknowledg|we use the code|"
                    r"implementation (is )?(based|built)|inspired by|refer to)", re.I)


def run(cmd, cwd=None, timeout=300, check=True):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if check and p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)}: {p.stderr.strip()[:300]}")
    return p.stdout


def norm_repo(owner, name):
    name = re.sub(r"(\.git)?[).,;:\]]*$", "", name)
    return f"{owner}/{name}"


def blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def loc(text):
    return sum(1 for l in text.splitlines() if l.strip())


STR_OR_COMMENT = re.compile(r'("(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\')|//[^\n]*|/\*.*?\*/', re.S)


def normalized_lines(text, path):
    """Code lines with comments and formatting removed: Python goes through ast.unparse (so black or yapf rewrites,
    quote style and comments do not count); C/C++/CUDA has comments stripped and whitespace collapsed."""
    if path.endswith(".py"):
        try:
            import ast
            text = ast.unparse(ast.parse(text))
        except Exception:  # noqa: BLE001  (Python 2 code, syntax errors)
            text = re.sub(r"(?m)^\s*#.*$", "", text)
    else:
        text = STR_OR_COMMENT.sub(lambda m: m.group(1) or "", text)
    return [re.sub(r"\s+", " ", l).strip() for l in text.splitlines() if l.strip()]


def line_change(old, new, path):
    """Added and deleted code lines between two versions of a file, ignoring comments and formatting."""
    a, b = normalized_lines(old, path), normalized_lines(new, path)
    if a == b:
        return 0, 0
    add = dele = 0
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op in ("replace", "delete"):
            dele += i2 - i1
        if op in ("replace", "insert"):
            add += j2 - j1
    return add, dele


# ----------------------------------------------------------------------------- cloning
def clone_sparse(repo, dest, timeout=240):
    if (dest / ".git").exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "-q", "--depth", "1", "--filter=blob:none", "--no-checkout",
         f"https://github.com/{repo}", str(dest)], timeout=timeout)
    run(["git", "-C", str(dest), "sparse-checkout", "set", "--no-cone", *SPARSE], timeout=60)
    run(["git", "-C", str(dest), "checkout", "-q"], timeout=timeout)


class Base:
    """A blobless bare clone of a known codebase, with blob hashes per sampled commit."""

    def __init__(self, name, cache):
        self.name, self.info = name, KNOWN[name]
        self.dir = cache / f"{name}.git"
        if not self.dir.exists():
            run(["git", "clone", "-q", "--bare", "--filter=blob:none", f"https://github.com/{self.info['repo']}",
                 str(self.dir)], timeout=600)
        log = run(["git", "-C", str(self.dir), "log", "--branches", "--format=%H %cs"], timeout=120).split("\n")
        self.all_commits = [l.split() for l in log if l.strip()]
        # sample up to 80 commits evenly over the history (newest first); compare() refines around the best one
        self.step = max(1, len(self.all_commits) // 80)
        self.commits = self.all_commits[::self.step]
        if self.all_commits and self.all_commits[-1] not in self.commits:
            self.commits.append(self.all_commits[-1])
        self._trees = {}
        self._content = {}

    def prefetch(self, commit, paths):
        """Fetch the blobs of `paths` at `commit` in one batch (a partial clone otherwise fetches them one by one)."""
        paths = list(paths)
        for i in range(0, len(paths), 200):
            run(["git", "-C", str(self.dir), "diff", "--stat", "4b825dc642cb6eb9a060e54bf8d69288fbee4904", commit, "--",
                 *paths[i:i + 200]], timeout=300, check=False)

    def tree(self, commit):
        if commit not in self._trees:
            out = run(["git", "-C", str(self.dir), "ls-tree", "-r", commit], timeout=120)
            t = {}
            for l in out.splitlines():
                meta, path = l.split("\t", 1)
                mode, typ, sha = meta.split()
                if typ == "blob" and Path(path).suffix.lower() in CODE_EXT | CFG_EXT:
                    t[path] = sha
            self._trees[commit] = t
        return self._trees[commit]

    def content(self, sha):
        if sha not in self._content:
            p = subprocess.run(["git", "-C", str(self.dir), "cat-file", "-p", sha], capture_output=True, timeout=120,
                               env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
            self._content[sha] = p.stdout.decode("utf-8", "replace") if p.returncode == 0 else None
        return self._content[sha]


# ----------------------------------------------------------------------------- analysis
def repo_files(root):
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts:
            out[p.relative_to(root).as_posix()] = p
    return out


def acknowledgments(root, self_repo):
    acks, links = [], set()
    for p in sorted(root.glob("README*")) + sorted(root.glob("*/README*"))[:3]:
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        lines = txt.splitlines()
        in_ack = False
        for i, l in enumerate(lines):
            if re.match(r"^\s*#+\s*(acknowledg|credits|thanks|related (projects|code|repos))", l, re.I):
                in_ack = True
                continue
            if in_ack and re.match(r"^\s*#+\s", l):
                in_ack = False
            hit = in_ack or ACK_RE.search(l)
            for r in gh_repos(l):
                if r.lower() == self_repo.lower():
                    continue
                links.add(r)
                if hit:
                    acks.append(r)
    return sorted(set(acks)), sorted(links)


def submodules(root):
    p = root / ".gitmodules"
    if not p.exists():
        return []
    out = []
    for m in re.finditer(r"path\s*=\s*(\S+)\s*\n\s*url\s*=\s*(\S+)", p.read_text(errors="ignore")):
        g = gh_repos(m.group(2))
        out.append({"path": m.group(1), "repo": g[0] if g else m.group(2)})
    return out


IMPORT_RE = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w]*)[\w.]*\s+import|import\s+([A-Za-z_][\w]*))", re.M)


def imports(files, root, scan=None):
    local = {Path(p).parts[0].removesuffix(".py") for p in files} | {Path(p).stem for p in files if p.endswith(".py")}
    for p in files:  # packages nested under src/ or a top folder
        parts = Path(p).parts
        if len(parts) > 1:
            local.add(parts[1].removesuffix(".py"))
    std = set(getattr(sys, "stdlib_module_names", ())) | {"__future__"}
    c = Counter()
    for p, fp in (files if scan is None else scan).items():
        if not p.endswith(".py"):
            continue
        try:
            txt = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        mods = {a or b for a, b in IMPORT_RE.findall(txt)}
        for m in mods:
            if m and m not in std and m not in local:
                c[m] += 1
    return c


def best_prefix(paths, base_paths):
    """Folder under which a vendored copy of the base sits ('' for the repository root)."""
    votes = Counter()
    bp = set(base_paths)
    for p in paths:
        parts = p.split("/")
        for i in range(len(parts)):
            rest = "/".join(parts[i:])
            if rest in bp:
                votes["/".join(parts[:i])] += 1
                break
    return votes.most_common(1)[0] if votes else ("", 0)


def detect_vendored(files):
    found = []
    for name, info in KNOWN.items():
        fp = info["files"]
        if not fp:
            continue
        hits = Counter()
        for f in fp:
            for p in files:
                if p == f or p.endswith("/" + f):
                    hits[p[: len(p) - len(f)].rstrip("/")] += 1
        for prefix, n in hits.most_common():  # every copy (e.g. two modified rasterizers side by side)
            if n >= max(2, (len(fp) + 1) // 2):
                found.append((name, prefix))
    return found


MAX_FULL_HISTORY = 1000
DERIVED = {"BEVDet": "mmdetection3d", "bevfusion-mit": "mmdetection3d"}  # child codebase -> the codebase it copies


def compare(files, base: Base, prefix):
    """Match the vendored copy against the base history and count unchanged / modified / removed files.

    The base version is the commit with the most byte-identical files; among tied commits (common when a fork changed
    every file that later changed upstream) it is the version needing the fewest changed or missing code lines, and
    the oldest such commit."""
    sub = {p[len(prefix) + 1:] if prefix else p: fp for p, fp in files.items()
           if (p.startswith(prefix + "/") if prefix else True) and Path(p).suffix.lower() in CODE_EXT | CFG_EXT}
    hashes = {p: blob_sha(fp.read_bytes()) for p, fp in sub.items()}
    texts, diffs = {}, {}

    def new_text(p):
        if p not in texts:
            texts[p] = sub[p].read_text(encoding="utf-8", errors="replace")
        return texts[p]

    def change(p, sha):
        if (p, sha) not in diffs:
            old = base.content(sha)
            diffs[(p, sha)] = None if old is None else line_change(old, new_text(p), p)
        return diffs[(p, sha)]

    def exact(commit):
        return sum(1 for p, s in base.tree(commit).items() if hashes.get(p) == s)

    full = len(base.all_commits) <= MAX_FULL_HISTORY
    scores = {c: (exact(c), d) for c, d in (base.all_commits if full else base.commits)}
    if not full:  # refine around every sampled commit that reached the top score
        top = max(v[0] for v in scores.values())
        idx = {c: j for j, (c, _) in enumerate(base.all_commits)}
        for c in [c for c, v in list(scores.items()) if v[0] == top]:
            j = idx[c]
            for c2, d2 in base.all_commits[max(0, j - base.step): j + base.step + 1]:
                scores.setdefault(c2, (exact(c2), d2))
    top = max(v[0] for v in scores.values())
    groups = {}
    for c, (sc, d) in sorted(scores.items(), key=lambda kv: kv[1][1]):  # oldest first
        if sc != top:
            continue
        t = base.tree(c)
        sig = tuple(sorted((p, s) for p, s in t.items() if Path(p).suffix.lower() in CODE_EXT and hashes.get(p) != s))
        groups.setdefault(sig, (c, d))
    ranked = sorted(groups.items(), key=lambda kv: (sum(1 for p, _ in kv[0] if p not in hashes), len(kv[0])))[:12]
    best = None
    for sig, (c, d) in ranked:
        base.prefetch(c, [p for p, _ in sig])
        cost = 0
        for p, sha in sig:
            if p in hashes:
                ch = change(p, sha)
                cost += sum(ch) if ch else 0
            else:
                old = base.content(sha)
                cost += len(normalized_lines(old, p)) if old else 0
        if best is None or (cost, d) < best[0]:
            best = ((cost, d), c, d)
    _, commit, date = best
    t = base.tree(commit)
    code_base = {p: s for p, s in t.items() if Path(p).suffix.lower() in CODE_EXT}
    modified, reformatted, unreadable = [], [], []
    for p, sha in code_base.items():
        if p in hashes and hashes[p] != sha:
            ch = change(p, sha)
            if ch is None:
                unreadable.append(p)
            elif ch[0] or ch[1]:
                old = base.content(sha) or ""
                modified.append({"path": p, "added": ch[0], "deleted": ch[1], "base_loc": len(normalized_lines(old, p))})
            else:
                reformatted.append(p)
    unchanged = [p for p, sha in code_base.items() if hashes.get(p) == sha] + reformatted
    removed = [p for p in code_base if p not in hashes]
    base_dirs = {str(Path(p).parent) for p in t} - {"."}
    new_files = [p for p in sub if p not in t and Path(p).suffix.lower() in CODE_EXT and str(Path(p).parent) in base_dirs
                 and not THIRD_PARTY_DIRS.search(p) and not p.startswith("submodules/")]
    return {"base": base.name, "base_repo": base.info["repo"], "prefix": prefix, "commit": commit[:10], "_commit": commit,
            "commit_date": date, "candidates_tied": len(groups),
            "base_code_files": len(code_base), "unchanged": len(unchanged), "reformatted_only": len(reformatted),
            "modified": len(modified), "unreadable": len(unreadable),
            "removed": len(removed), "added_lines": sum(m["added"] for m in modified),
            "deleted_lines": sum(m["deleted"] for m in modified),
            "modified_files": sorted(modified, key=lambda m: -(m["added"] + m["deleted"])),
            "new_files_inside": new_files,
            "new_files_inside_loc": sum(loc(new_text(p)) for p in new_files)}


def get_base(bname, bases, lock_bases, cache):
    with lock_bases[bname]:
        if bname not in bases:
            bases[bname] = Base(bname, cache)
    return bases[bname]


def analyze(repo, work, cache, bases, lock_bases):
    name = repo.replace("/", "__")
    dest = work / "repos" / name
    res = {"repo": repo, "ok": False}
    try:
        clone_sparse(repo, dest)
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e)[:300]
        return res
    files = repo_files(dest)
    code = {p: fp for p, fp in files.items() if Path(p).suffix.lower() in CODE_EXT}
    res["n_code_files"] = len(code)
    res["loc_total"] = sum(loc(fp.read_text(encoding="utf-8", errors="replace")) for fp in code.values())
    acks, res["readme_links"] = acknowledgments(dest, repo)
    owner = repo.split("/")[0].lower()
    res["acknowledged"] = [a for a in acks if a.split("/")[0].lower() != owner]
    res["own_related"] = [a for a in acks if a.split("/")[0].lower() == owner]
    res["submodules"] = submodules(dest)
    res["vendored"] = []
    claimed, roots = set(), set()
    found = detect_vendored(files)
    found = [(b, pre) for b, pre in found if not (b in DERIVED.values() and any(
        DERIVED.get(b2) == b and pre2 == pre for b2, pre2 in found))]  # keep only the derived codebase for one copy
    for bname, prefix in found:
        if KNOWN[bname]["repo"].lower() == repo.lower():
            res["is_known_base"] = bname
            continue
        base = get_base(bname, bases, lock_bases, cache)
        cmp = compare(files, base, prefix)
        res["vendored"].append(cmp)
        root = (prefix + "/") if prefix else ""
        tree = base.tree(cmp.pop("_commit"))
        claimed |= {p for p in code if p.startswith(root) and p[len(root):] in tree}
        roots |= {root + p.split("/")[0] + "/" for p in tree if "/" in p}
    # submodules that are modified copies of a known codebase (e.g. a changed CUDA rasterizer under another name)
    res["submodule_forks"] = []
    official = {i["repo"].lower() for i in KNOWN.values()}
    for sm in [x for x in res["submodules"] if re.fullmatch(r"[\w.\-]+/[\w.\-]+", x["repo"])
               and x["repo"].lower() not in official][:4]:
        try:
            sdest = work / "repos" / (name + "__sub__" + sm["path"].replace("/", "_"))
            clone_sparse(sm["repo"], sdest)
            sfiles = repo_files(sdest)
            for bname, prefix in detect_vendored(sfiles):
                c = compare(sfiles, get_base(bname, bases, lock_bases, cache), prefix)
                c.pop("_commit", None)
                c["submodule"] = sm
                res["submodule_forks"].append(c)
        except Exception as e:  # noqa: BLE001
            res["submodule_forks"].append({"submodule": sm, "error": str(e)[:200]})
    # imports from the repository's own files only (a vendored framework's imports are not the paper's choices)
    imp = imports(files, dest, scan={p: fp for p, fp in files.items() if p not in claimed})
    res["imports"] = dict(imp.most_common(40))
    res["dependency_bases"] = sorted({n for n, i in KNOWN.items() for m in i["imports"] if imp.get(m)})
    inside = lambda p: any(p.startswith(r) for r in roots)  # noqa: E731
    third = {p for p in code if p not in claimed and (THIRD_PARTY_DIRS.search(p) or (not inside(p) and (
        p.startswith("submodules/") or any(seg in LIB_DIRS for seg in Path(p).parts[:-1]))))}
    newc = {p: fp for p, fp in code.items() if p not in claimed and p not in third}
    res["new_code"] = {"files": len(newc),
                       "loc": sum(loc(fp.read_text(encoding="utf-8", errors="replace")) for fp in newc.values()),
                       "top_dirs": Counter(str(Path(p).parent) for p in newc).most_common(6)}
    res["third_party_loc"] = sum(loc(code[p].read_text(encoding="utf-8", errors="replace")) for p in third)
    res["ok"] = True
    return res


# ----------------------------------------------------------------------------- report
def describe(r):
    if not r.get("ok"):
        return f"### {r['repo']}\n\n克隆或分析失败：{r.get('error', '')}\n"
    out = [f"### {r['repo']}", ""]
    out.append(f"代码 {r['n_code_files']} 个文件、{r['loc_total']} 行（非空行）；本仓库新写的代码约 {r['new_code']['loc']} 行，"
               f"分布在 {', '.join(d for d, _ in r['new_code']['top_dirs'][:4]) or '—'}。")
    if r["vendored"]:
        for v in r["vendored"]:
            where = f"放在 `{v['prefix']}/` 下" if v["prefix"] else "放在仓库根目录"
            out.append(f"- 拷入并修改了 **{v['base']}**（{v['base_repo']}，最接近 {v['commit_date']} 的版本，{where}）："
                       f"{v['base_code_files']} 个原有代码文件中 {v['unchanged']} 个未改、{v['modified']} 个修改"
                       f"（+{v['added_lines']} / -{v['deleted_lines']} 行）、{v['removed']} 个删除；"
                       f"在原有目录里新增 {len(v['new_files_inside'])} 个文件。")
            for m in v["modified_files"][:8]:
                out.append(f"  - `{m['path']}` +{m['added']} / -{m['deleted']}（原文件 {m['base_loc']} 行）")
    for v in r.get("submodule_forks", []):
        if "error" in v:
            out.append(f"- 子模块 {v['submodule']['repo']} 无法分析：{v['error']}")
        else:
            out.append(f"- 子模块 `{v['submodule']['path']}` 指向 **{v['base']}** 的修改版 {v['submodule']['repo']}："
                       f"{v['modified']} 个文件修改（+{v['added_lines']} / -{v['deleted_lines']} 行）")
            for m in v["modified_files"][:6]:
                out.append(f"  - `{m['path']}` +{m['added']} / -{m['deleted']}")
    if r["dependency_bases"]:
        out.append(f"- 作为依赖库调用：{', '.join(r['dependency_bases'])}")
    if r["acknowledged"]:
        out.append(f"- README 致谢或声明基于：{', '.join(r['acknowledged'][:10])}")
    if r.get("own_related"):
        out.append(f"- README 提到的作者自己的其他仓库：{', '.join(r['own_related'][:6])}")
    libs = [k for k in r["imports"] if k not in GENERIC_LIBS][:10]
    if libs:
        out.append(f"- 其他第三方导入：{', '.join(libs)}")
    if r.get("third_party_loc"):
        out.append(f"- 第三方目录（third_party、submodules 等）中的代码 {r['third_party_loc']} 行，未计入新代码")
    if r.get("is_known_base"):
        out.append(f"- 这个仓库本身就是内置列表里的基础代码库 {r['is_known_base']}")
    if not (r["vendored"] or r["dependency_bases"] or r["acknowledged"] or r.get("is_known_base")):
        out.append("- 没有识别出所基于的已知代码库（可能是从头实现，或基于不在内置列表里的仓库）")
    return "\n".join(out) + "\n"


def report(results):
    head = ["# 最接近工作的代码构成", "",
            f"生成时间 {dt.date.today()}。逐个仓库说明：基于哪些代码库（拷入修改、作为依赖、README 声明）、改了原代码库的哪些文件、"
            "改了多少、自己新写了多少。行数只数非空行；修改行数是去掉注释和格式差异后的代码行差异（Python 先经 ast 规范化），只是"
            "重新格式化的文件算作未改。\"最接近的版本\"是原代码库历史中和拷入文件完全一致的文件最多、其余差异最小的那次提交；仓库如果经由中间仓库（例如某个 3DGS "
            "变体）改来，中间仓库的改动也算在内。\"新写的代码\"指不属于识别出的基础代码库、也不在第三方目录里的代码，可能包含从"
            "未识别的仓库拷来的代码。"
            "这份报告只描述代码事实，方法上的本质差异由推理方判断。", ""]
    return "\n".join(head + [describe(r) for r in results])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repos", nargs="*", default=[], help="owner/repo or GitHub URLs")
    ap.add_argument("--from-md", nargs="*", default=[], help="markdown files to collect GitHub links from")
    ap.add_argument("--list", help="text file with one owner/repo or URL per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", help="where to keep base codebase clones (default OUT/bases)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--keep", action="store_true", help="keep the cloned repositories")
    ap.add_argument("--max", type=int, default=12, help="max repositories taken from --from-md")
    a = ap.parse_args(argv)
    repos = []
    for r in a.repos + (Path(a.list).read_text().split() if a.list else []):
        g = gh_repos(r)
        repos.append(g[0] if g else r.strip("/"))
    md = []
    for f in a.from_md:
        if Path(f).exists():
            md += gh_repos(Path(f).read_text(errors="ignore"))
    md = list(dict.fromkeys(md))
    repos += md[-a.max:] if len(md) > a.max else md
    repos = list(dict.fromkeys(r for r in repos if "/" in r))
    if not repos:
        sys.exit("没有找到要分析的 GitHub 仓库")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cache = Path(a.cache) if a.cache else out / "bases"
    cache.mkdir(parents=True, exist_ok=True)
    import threading
    bases, locks = {}, defaultdict(threading.Lock)
    results = []
    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        futs = {ex.submit(analyze, r, out, cache, bases, locks): r for r in repos}
        for f in cf.as_completed(futs):
            r = futs[f]
            try:
                res = f.result()
            except Exception as e:  # noqa: BLE001
                res = {"repo": r, "ok": False, "error": str(e)[:300]}
            results.append(res)
            print(f"{'ok ' if res.get('ok') else 'ERR'} {r}", file=sys.stderr)
            if not a.keep:
                for d in (out / "repos").glob(r.replace("/", "__") + "*"):
                    shutil.rmtree(d, ignore_errors=True)
    order = {r: i for i, r in enumerate(repos)}
    results.sort(key=lambda x: order.get(x["repo"], 0))
    (out / "lineage.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "lineage.md").write_text(report(results), encoding="utf-8")
    print(f"写入 {out / 'lineage.md'} 和 {out / 'lineage.json'}（{sum(r.get('ok', False) for r in results)}/{len(results)} 个仓库成功）")


if __name__ == "__main__":
    main()
