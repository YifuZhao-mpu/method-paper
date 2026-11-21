#!/usr/bin/env python3
"""Automated method-paper pipeline: a reasoner (Codex, or a separate headless Claude process) reasons and reviews;
Claude Code executes.

Run from the paper repository root (the git repository holding CLAUDE.md, AGENTS.md and research/):

    python pipeline.py init [--autonomy checkpoints|auto] [--paper-dir paper] [--main-tex main.tex]
                            [--reasoner codex|claude] [--claude-model MODEL] [--claude-effort xhigh]
                            [--codex-effort xhigh] [--codex-model MODEL]
                            [--start STAGE] [--gate2-passed REASON] [--force]
    python pipeline.py init --polish [--paper-dir paper] [--main-tex main.tex] [--start profile|figures|lint|polish]
                                            polish a draft written by ARIS (references/aris.md):
                                            profile → figures → lint → polish → lint ⇄ revise → handback
    python pipeline.py next [--json]        what to do now: who does it, the exact codex command or the task
    python pipeline.py done [--skip] [--note TEXT] [--by subagent]
                                            validate this stage's outputs and advance
    python pipeline.py retry                re-run the current Codex stage with a fresh output file
    python pipeline.py reason               with --reasoner claude: run the current reasoning stage with a separate
                                            headless Claude process (what `next` gives as the command)
    python pipeline.py decide VALUE         answer a user checkpoint ("C2,C4", "2", "back 理由", "yes", "no", "stop")
    python pipeline.py fail [--note TEXT]   record that this stage failed (pauses after two failures)
    python pipeline.py goto STAGE [--reason TEXT] [--seed TEXT] [--anchor C12] [--gate2-passed REASON]
                                            jump to a stage (user decision); resets the round counters.
                                            --reason carries the author's conclusion (goto problems --reason drops
                                            the anchor; with a writing stage it is the author's instruction for the
                                            outline, draft and revision); --seed adds an opportunity to the next round;
                                            --anchor (goto methods) continues on another anchor that passed gate one
    python pipeline.py set [--reasoner claude] [--claude-model M] [--claude-effort max]
                           [--codex-effort high] [--codex-model M] [--autonomy auto] [--codex-sandbox none]
                           [--screen-n 4] [--screen-per 2] [--screen-total 8] [--screen-setting TEXT]
                                            change settings in place (--codex-sandbox none: Codex without its own
                                            sandbox, for containers where bubblewrap cannot start)
    python pipeline.py status               progress, decisions and history

State lives in research/pipeline.json. Every Codex run writes its final message to a new file
research/codex/<stage>-<n>.md whose last line is a machine-readable line such as
"PIPELINE: gate1=pass candidate=C2". `done` reads only that line of that file, then saves the content as the
handoff file (gates.md is appended to, the others are replaced) and decides where to go next.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
R = Path("research")
STATE = R / "pipeline.json"
CS = "research/code_survey/lineage.md"

ENUM, LIST, ID, FREE = "enum", "list", "id", "free"

# owner: codex | claude | user. out: handoff file. pl: PIPELINE keys and their types. formats: lines Codex copies.
STAGES = [
    {"id": "problems", "owner": "codex", "title": "列机会候选（四类）", "out": "problem_cards.md", "web": True,
     "pl": {"candidates": (LIST,), "recommend": (LIST,)},
     "formats": ["PIPELINE: candidates={first},C?,C? recommend=C?,C?"],
     "note": "candidates 依次列出本轮全部候选编号，recommend 写推荐优先验证的 2 个，都用英文逗号分隔。"},
    {"id": "cp_candidates", "owner": "user", "title": "选择要验证的候选"},
    {"id": "diagnose", "owner": "claude", "title": "量证据和提升空间", "runs": True, "skip_with_note": True},
    {"id": "gate1", "owner": "codex", "title": "关口一判定", "out": "gates.md", "append": True, "web": True,
     "pl": {"gate1": (ENUM, {"pass", "fail"}), "candidate": (ID,)},
     "formats": ["PIPELINE: gate1=pass candidate=C?", "PIPELINE: gate1=fail"],
     "note": "有候选通过时写 pass，candidate 写最值得做的那一个；没有候选通过时写 fail。"},
    {"id": "code_survey", "owner": "claude", "title": "拉取最接近工作的开源代码并分析构成", "skippable": True},
    {"id": "methods", "owner": "codex", "title": "出一组候选方法（按算子）", "out": "method_candidates.md", "web": True,
     "pl": {"options": (LIST,), "recommend": (ID,), "back": (ENUM, {"problems"}), "reason": (FREE,)},
     "formats": ["PIPELINE: options={first_option},?,?,?,?,? recommend=?",
                 "PIPELINE: back=problems reason=一句话说明哪个实验否定了提升空间"],
     "note": ("options 按你的排序依次列出本轮全部候选编号（整数，从 {first_option} 起），排在前面的先初筛；recommend 写排第一的。"
              "只有锚点的提升空间被实验否定时（阳性对照在不同设置里反复不成立，或上限实验显示空间小于冻结的最小效果）才写 "
              "back=problems；方法失败本身不是理由。写了 back=problems 流程会停下来请作者决定是否放弃锚点。")},
    {"id": "screen", "owner": "claude", "title": "候选初筛（小规模并行跑）", "runs": True, "skippable": True},
    {"id": "cp_method", "owner": "user", "title": "选择方法"},
    {"id": "method_card", "owner": "codex", "title": "写方法卡", "out": "method_card.md",
     "pl": {"method_card": (ENUM, {"ready"})}, "formats": ["PIPELINE: method_card=ready"]},
    {"id": "code_location", "owner": "claude", "title": "读代码填写修改位置"},
    {"id": "gate2", "owner": "codex", "title": "关口二判定", "out": "gates.md", "append": True,
     "pl": {"gate2": (ENUM, {"pass", "conditional", "revise", "reposition", "fail"}), "reason": (FREE,)},
     "formats": ["PIPELINE: gate2=pass", "PIPELINE: gate2=revise",
                 "PIPELINE: gate2=reposition reason=一句话说明要往哪个方向补强",
                 "PIPELINE: gate2=fail reason=一句话说明为什么要换方法"],
     "note": ("pass：完整性清单全部满足、贡献判断接收理由占优。revise：方法值得保留，方法卡有可补齐的缺项。reposition（补强）：方法"
              "有效、不在非创新清单，但和最接近工作的差距不够撑起一篇论文，保留它并回去出加强方向的候选。fail：只用于撞车（有具体"
              "出处，同一评测、同一计算环节、没有本质差异）、属于非创新清单、或初筛增益被对照复现。")},
    {"id": "plan", "owner": "codex", "title": "实验职责表", "out": "experiment_plan.md",
     "pl": {"plan": (ENUM, {"ready"})}, "formats": ["PIPELINE: plan=ready"]},
    {"id": "run", "owner": "claude", "title": "实现并运行实验（不含最终报告）", "runs": True},
    {"id": "review", "owner": "codex", "title": "结果审查", "out": "review.md",
     "pl": {"review": (ENUM, {"pass", "more", "fail"}), "card": (ENUM, {"keep", "revise"}),
            "conditions": (ENUM, {"met", "pending", "unmet"}), "back": (ENUM, {"methods", "problems"}),
            "reason": (FREE,)},
     "formats": ["PIPELINE: review=pass", "PIPELINE: review=more card=keep", "PIPELINE: review=more card=revise",
                 "PIPELINE: review=fail reason=一句话说明哪条核心 claim 不成立"],
     "note": ("pass：证据足以写论文。more：需要补实验或修实现；card=revise 表示方法卡要按审查结论修订（之后重新过关口二），"
              "否则写 card=keep。fail：核心 claim 不成立。")},
    {"id": "cp_final", "owner": "user", "title": "批准在测试集上跑最终报告"},
    {"id": "final_eval", "owner": "claude", "title": "最终报告评测", "runs": True},
    {"id": "profile", "owner": "claude", "title": "写作画像、范文门槛和文献池", "skippable": True},
    {"id": "outline", "owner": "codex", "title": "论文提纲：故事、引言分段、相关工作和主张—证据对应", "out": "outline.md",
     "web": True, "pl": {"outline": (ENUM, {"ready", "weak"}), "reason": (FREE,)},
     "formats": ["PIPELINE: outline=ready", "PIPELINE: outline=weak reason=一句话说明主结果为什么撑不起本文的主张"],
     "note": ("ready：现有结果撑得起提纲里的主张。weak：主结果小于冻结的最小效果、落在种子波动之内，或者去掉继承的部件后本文"
              "新增的部分几乎没有收益；流程会停下来请作者决定补实验、换主张还是照此写，提纲照常写完。")},
    {"id": "figures", "owner": "claude", "title": "动机图、数据图、框架图和效果图", "skippable": True},
    {"id": "draft", "owner": "codex", "title": "写论文初稿", "out": "draft_notes.md", "write": True,
     "pl": {"draft": (ENUM, {"ready"})}, "formats": ["PIPELINE: draft=ready"]},
    {"id": "polish", "owner": "codex", "title": "收尾改稿（ARIS 稿件）", "out": "polish_notes.md", "write": True,
     "pl": {"polish": (ENUM, {"done"})}, "formats": ["PIPELINE: polish=done"]},
    {"id": "lint", "owner": "claude", "title": "文稿与图表检查"},
    {"id": "revise", "owner": "codex", "title": "按检查结果改稿", "out": "revision_notes.md", "write": True,
     "pl": {"revise": (ENUM, {"done"})}, "formats": ["PIPELINE: revise=done"]},
    {"id": "handback", "owner": "claude", "title": "交回 ARIS 复审", "skippable": True},
    {"id": "done", "owner": "none", "title": "完成"},
]
IDX = {s["id"]: i for i, s in enumerate(STAGES)}
# polish mode (init --polish): ARIS did the research and the draft; method-paper only finishes the paper
POLISH_STAGES = ["profile", "figures", "lint", "polish", "revise", "handback", "done"]
POLISH_START = ["profile", "figures", "lint", "polish"]
AUDITS = ("PAPER_CLAIM_AUDIT.json", "CITATION_AUDIT.json")  # ARIS audit records that pin the audited files by hash


def polish_mode(st):
    return st.get("mode") == "polish"


def self_cmd():
    """How to call this script: the plugin's bin/ wrapper when it is on PATH and points here, else python + path."""
    w = shutil.which("method-paper-pipeline")
    if w and (Path(w).resolve().parent.parent / "skills" / "method-paper" / "scripts" / "pipeline.py").resolve() \
            == Path(__file__).resolve():
        return "method-paper-pipeline"
    return f"python {Path(__file__)}"

REVIEW_FINAL_FORMATS = ["PIPELINE: review=pass", "PIPELINE: review=fail reason=一句话说明哪条核心 claim 不成立"]
REVIEW_FINAL_NOTE = ("已经补了 {n} 轮实验，这一轮必须下结论，不再要求补实验：有证据支持的 claim 保留，不支持的按发布会原则的优先级"
                     "删掉或缩小范围，写清缩小后的 claim，然后判 pass；连核心 claim 都不成立才判 fail。")
PROBE_REVIEW_FORMATS = ["PIPELINE: review=pass conditions=met", "PIPELINE: review=more card=keep conditions=pending",
                        "PIPELINE: review=more card=revise conditions=pending",
                        "PIPELINE: review=fail conditions=unmet back=methods reason=一句话说明预测怎样落空",
                        "PIPELINE: review=fail conditions=unmet back=problems reason=一句话说明为什么失效机制本身站不住"]
PROBE_FINAL_FORMATS = [PROBE_REVIEW_FORMATS[0]] + PROBE_REVIEW_FORMATS[3:]
PROBE_MAX_PENDING = 2
GATE2_NOPILOT_FORMATS = ["PIPELINE: gate2=pass", "PIPELINE: gate2=conditional", "PIPELINE: gate2=revise",
                         "PIPELINE: gate2=reposition reason=一句话说明要往哪个方向补强",
                         "PIPELINE: gate2=fail reason=一句话说明为什么要换方法"]
GATE2_NOPILOT_NOTE = ("本方法没有初筛证据，所以 pass 要求机制预测已有足够证据（文献或已有实验）；预测还没有实验证据时判 "
                      "conditional（有条件通过），先做最小验证实验。其余结论的含义同下：revise：方法卡有可补齐的缺项。reposition"
                      "（补强）：方法值得保留，但和最接近工作的差距不够，回去出加强方向的候选。fail：撞车、属于非创新清单，或机制与"
                      "要解决的不足无关。")
PROBE_REVIEW_NOTE = ("先看对照：阳性对照不成立（基线在探针设置里没有出现不足）时，结论不是方法无效，而是探针没测到不足，"
                     "能改探针设置就判 pending 并写清怎么改，在任何可行设置里都复现不出不足就判 unmet back=problems。"
                     "conditions=met：最小验证实验按事先的判据支持机制预测，接下来写完整职责表。pending：结果模糊或实现有问题，"
                     "需要补一轮探针（card=revise 表示方法卡要先修订）。unmet：预测落空；back=methods 表示锚点的提升空间仍然成立、"
                     "换一个方法，back=problems 表示阳性对照或上限也不成立、提升空间站不住（流程会停下来请作者决定是否放弃锚点）。")
PROBE_FINAL_NOTE = ("机制探针已经审过 {n} 轮，这一轮必须在 met 和 unmet 之间给出结论，不再判 pending，也不建议再补性能实验。"
                    "审查意见里把每项预测分成“被有效证据否定”“缺少有效证据”“得到支持”三类：证据不可恢复或运行作废的，算缺少"
                    "有效证据；没有得到支持的预测一律按未支持处理。" + PROBE_REVIEW_NOTE)

COMMON = ("按 method-paper skill 工作（已安装时即 $method-paper；skill 目录是 {skill}，先读其中的 SKILL.md，下文 references/ 指 "
          "{skill}/references/）。你是本仓库的方法推理与审查方，先读 {root}/AGENTS.md；项目信息见 {root}/CLAUDE.md 顶部的"
          "“项目信息”。下文 research/ 指 {root}/research/。只依据仓库里的文件和你检索到的文献工作，不修改实验结果文件。"
          "\n审查范围：只报告真实存在、会影响结论的问题，明确写出哪些是对的，不凑问题；不提议哈希或指纹绑定、兼容层、开关框架"
          "之类的防御性机制，除非能指出当前确实出错的文件或运行。审计脚本、测试隔离、归档这类工程问题列进“工程缺口”交给实验方"
          "修，它们只降低证据的可信度，不让科学结论停在待定。research/findings.md 如果存在，是已被证据否定的方向记录，先读它，"
          "不要让新方案落回这些做法。新颖性、影响力这类判断用来排序和写风险；淘汰候选只看非创新清单、预算和实验结果。")
POLISH_COMMON = ("按 method-paper skill 工作（已安装时即 $method-paper；skill 目录是 {skill}，先读其中的 SKILL.md 和 references/aris.md，"
                 "下文 references/ 指 {skill}/references/）。这个项目的研究、实验和初稿由 ARIS 完成，你只负责收尾：表达层面的审读和"
                 "改稿。项目根目录是 {root}；下文 research/ 指 {root}/research/，是 method-paper 的收尾记录，和 ARIS 的文件互不干扰。")
ARIS_FREEZE = ("\n冻结（ARIS 的审计以这些为准，收尾不能动）：数字和实验设置；claim 的范围，ARIS 的改进日志或审计收窄过的说法不能再"
               "放宽；引用，不改 \\cite 的键和 .bib，觉得引用不当的列入“需作者决策”，交给 ARIS 的 /citation-audit；表格里的结果行，"
               "不利结果留在表里，只调整正文的叙述重心，不把它当比赛项目。事实依据依次是 {root}/NARRATIVE_REPORT.md、"
               "{root}/PAPER_PLAN.md、{root}/PAPER_ACCEPTANCE_CONTRACT.md，以及论文目录下的 PAPER_IMPROVEMENT_LOG.md 和 "
               "PAPER_CLAIM_AUDIT.md（存在的才读）。只改论文目录里的 .tex 文件，不改图、.bib 和 ARIS 的审计文件。")

PROMPTS = {
    "problems": (
        "任务：按 references/explore.md 第 0 步（四类机会见 references/method-design.md 第 2 节）列出 5–8 个候选机会，编号从 {first} "
        "开始依次往后编。先写明本项目的基线（开源代码地址）和主评测（目标会议认可的 benchmark，机器人方向可以是仿真闭环或真机），"
        "所有候选都在这个基线、这个评测上量；自建的诊断设置只用来找原因。候选覆盖至少两类机会：条件崩溃与泛化、去掉特权输入或"
        "监督（逐项检查基线在评测里用到、但部署时拿不到或很贵的真值、标定、已知位姿、标签、仿真特权信息）、效率、统一或简化、新能力"
        "或新设定。每个候选写：机会类型；触发条件或依赖的特权输入；基线在评测上的表现；证据来源（可核对的出处，联网检索，检索不到"
        "的标“待核实”）；失效的计算环节（只是假设）；在主评测上怎样量证据和提升空间（数据集、分层变量或特权替代、判据）。再按"
        "“重要性 × 可验证性 × 能推出新计算”排序，推荐 2 个优先验证。{seed}{anchors}{exclude}",
        "完整的问题卡（会保存为 research/problem_cards.md）"),
    "gate1": (
        "任务：读 {cards_file} 中被选中的候选（{chosen}），以及 research/runs.md 和其中引用的 research/results/ "
        "原始结果，按 references/explore.md 第 3 节逐个判定机会是否成立：证据是否在主评测上量出来（按机会类型：分层误差、特权输入"
        "与现实替代的差距、质量与代价曲线等）；提升空间是否足够（不小于论文打算主张的最小效果；写明冻结的最小效果、指标和数值，"
        "之后的初筛判据和机制预测都按它折算；没有量过或空间不够，判 fail 并写明要在哪个设置里重量）。失效的计算环节只是假设，"
        "不要求已经证明。联网检索最近 12–18 个月的工作，列出最接近的 3–5 篇及差异，每篇写出开源代码地址（github.com/作者/仓库"
        " 的完整形式，没有开源的注明“未开源”），下一阶段会拉取这些代码分析构成。只有一篇具体的工作在同一评测上、用同类计算已经"
        "解决了同一个机会、效果相当时才算撞车；其余相近的工作写进差异和风险。通过的候选会成为锚点，之后方法没奏效也不放弃它。"
        "research/gates.md 里的已有记录可以参考。{diag}",
        "本次判定：日期、三关判定表（只填关口一）、依据、下一步。不必重复 gates.md 的已有记录，流程会把本次判定追加进去"),
    "methods": (
        "任务：读 research/gates.md 中已通过关口一的锚点（{passed}），按 references/method-design.md 第 3–5 节出一组候选方法："
        "6–8 个，编号用整数、从 {first_option} 起；覆盖至少 3 种方法设计算子、基线流程里至少 2 处不同的插入点；至少一个用本方向"
        "接收论文里常见的算子，至少一个用本方向少见、但在相邻方向奏效过的算子。每个候选都是在选定基线代码上的改动，实验方一天内"
        "能做出最小版本。每个写清：服务的锚点和机会类型、算子、一句话机制、插在哪里（写到文件）、最接近的先例（同类做法在相邻问题"
        "或相邻领域奏效的出处，联网检索）、与最近 12–18 个月最接近工作的差异、预计在目标子集回收多少提升空间（关口一冻结的最小"
        "效果和量出的空间见 research/gates.md）、初筛方案：能热启动就从基线权重出发并让基线同步训练同样的步数作对照，只用验证集，"
        "报目标子集和全体验证集，两个种子或两个场景，每个不超过 {screen_per} GPU 小时，只调候选自己新加的超参（最多两个、每个最多三个"
        "取值），以及事先写死的判据：是（目标子集提升不小于最小效果的一半，全体验证集下降不超过最小效果的四分之一）、接近（方向对，"
        "提升在最小效果的 20%–50%）、否、对照无效（基线在这个设置里没有出现不足）。给全部候选排序并写风险：“可能已有人做过”“可能"
        "不够新”写进风险，不作为淘汰理由；只有属于非创新清单（references/method-card.md 第 3 节）或超出预算的不列入。实验方会并行"
        "初筛排在前面的 {screen_n} 个（合计不超过 {screen_total} GPU 小时）。{screen_setting}{refine}{reposition}{synthesis}{code_survey}{feedback}",
        "完整的候选方法文档（会保存为 research/method_candidates.md）"),
    "method_card": (
        "任务：把方案 {method} 写成完整方法卡（references/method-card.md 的七要素和其余必写项：初筛证据、组件结构、最多三条可观测的"
        "机制预测、本质差异、一般性）。方案的描述在 research/method_candidates.md；它是从较早的候选改来的时，原候选在 research/codex/"
        " 下较早的 methods 输出里。“代码修改位置”单独成一节（用 markdown 标题），内容写“待实验方填写”，由 Claude Code 读代码后"
        "填写。{reposition_card}{screen_card}{code_survey_card}",
        "完整的方法卡（会保存为 research/method_card.md）"),
    "method_card_revision": (
        "任务：按 research/review.md 的审查结论和 research/deviations.md 中被接受的偏差，修订 research/method_card.md：只改审查"
        "要求改的部分，其余原样保留；“代码修改位置”一节原样保留，实验方会按修订后的方法卡更新它。修订后会重新过关口二。",
        "完整的新版方法卡（会保存为 research/method_card.md）"),
    "gate2": (
        "任务：按 references/method-card.md 第 3、4 节判定 research/method_card.md（含实验方填写的代码修改位置；也读 "
        "research/deviations.md 和 research/screen.md）。{pilot_status}A 完整性清单逐项写满足或不满足及依据：七要素齐全一致；初筛证据"
        "（达到事先写的判据，并且同步训练的基线、等容量对照或去掉关键项复现不了这个增益）；机制预测最多三条、都是评测里能直接看到的"
        "结果；与最接近工作的本质差异落在具体计算环节并有出处；不属于非创新清单。B 贡献判断按 NeurIPS、CVPR 审稿指南的口径列接收"
        "理由和拒稿理由并权衡：已有技术的新组合、去掉特权输入、跨领域迁移并做了非平凡改动、用更简单或更精确的计算替换出错环节，"
        "只要理由讲清楚就算新颖；“太简单”“事后看来显然”“没有先例”不是理由；“已经有人做过”必须给出具体论文或代码和重合的计算环节；"
        "适用面窄只缩小主张范围。按 references/method-design.md 第 8 节校准：先找 2–3 篇目标会议近两年、同方向、改动规模相近的"
        "接收论文作对照，按同样标准它们也过不了就说明判得过严。{code_survey_gate2}{rejudge}方法有效但差距不够时判 reposition（补强）"
        "并写清往哪个方向补强，不要判 fail。每条不满足项写明补到什么程度算通过；写不出通过条件的意见放进风险清单。审稿人可能的质疑"
        "列成风险清单（能否化解、交给哪个实验或哪句主张范围），风险清单不决定本次结论。结论直接写，不软化。判换方法时写一节“以后"
        "不要再试”。",
        "本次判定：日期、完整性清单、贡献判断、结论（通过 / 修订 / 补强 / 换方法，没有初筛证据时还可以是有条件通过）、缺项及通过"
        "条件、风险清单、下一步。不必重复 gates.md 的已有记录，流程会把本次判定追加进去"),
    "method_card_gate2_revision": (
        "任务：按关口二判定 {gate2_file} 列出的缺项修订 research/method_card.md：逐项补齐到判定写明的通过条件，没被指出的部分"
        "原样保留；“代码修改位置”一节原样保留，需要实验方调整的写在该节末尾“需实验方更新：……”。修订后会复判关口二。",
        "完整的新版方法卡（会保存为 research/method_card.md）"),
    "plan": (
        "任务：按 references/experiments.md，根据 research/method_card.md 写实验职责表：每个实验写明支撑的 claim、论证职责、"
        "数据集与协议、预期现象、状态（新行为“待做”）。必须包含主实验、逐模块消融、等容量对照、机制开关、按关口一失效条件分层、"
        "鲁棒性、效率；在测试集上的最终评测单独成行，状态写“最终报告（待批准）”。research/experiment_plan.md 已存在时在它的基础上"
        "更新，已有行的“状态”列原样保留。{review_more}{probe_plan}{plan_note}",
        "完整的实验职责表（会保存为 research/experiment_plan.md）"),
    "review": (
        "任务：只依据 research/method_card.md、research/experiment_plan.md、research/runs.md、research/results/ 和 "
        "research/deviations.md 审查：每条 claim 是否被结果支撑（逐个数字追溯到 runs.md 的行和原始文件）、协议是否一致、"
        "测试集纪律、多种子统计、区分性实验是否证明了机制。逐条处理 deviations：写明接受（方法卡需要相应修订）还是要求改实现。"
        "需要补的实验写成新的职责表行建议，流程会交给职责表阶段写进 experiment_plan.md。research/evidence_precheck.md 是流程"
        "自动生成的证据预检，标为缺失、作废或不可恢复的运行不能支持任何结论。结论为否定（fail 或 unmet）时，写一节“以后不要再试”："
        "被否定的做法和依据，供下一轮找方法时避开。{probe_review}",
        "完整的审查意见（会保存为 research/review.md），末尾写“交给：……；要做：……”"),
    "outline": (
        "任务：动笔之前先定下全文的组织，写成论文提纲（只写提纲，不写正文）。写法见 references/writing.md 第 4 节，篇幅和文献"
        "数量的门槛见 research/writing_profile/venue_norms.json（没有就用 references/writing-patterns.md 第 13 节的默认值），范文的"
        "结构见 research/writing_profile/exemplars.md（如果存在，只参考结构，不摘抄原文）。依据：research/method_card.md（方法）、"
        "research/experiment_plan.md、research/runs.md 和 research/results/（已有的实验和数字）、research/gates.md（最接近的工作和"
        "本质差异）、research/refs_pool.md（已核实的文献池；不存在时在“仍缺的材料”里写明，不凭记忆列文献）。{advisor}{writing_note}"
        "\n提纲依次写九项：\n"
        "1. 一句话故事和主结果：本文解决什么问题、凭什么想法、最强的一个数字（带运行编号和比较对象）。主结果小于方法卡冻结的"
        "最小效果、落在种子波动之内，或只在个别设置成立时，如实写出，并在 PIPELINE 行写 outline=weak：结果撑不起主张时，组织"
        "表达救不了。\n"
        "2. 继承与贡献：哪些收益来自继承的已有部件（基线、已有的流程或编码器），哪些来自本文新增的部分；列出能把两者分开的"
        "对照（哪张表哪一行、差值多少）。引言和实验都按这个划分写，本文只对新增部分提出主张。\n"
        "3. 引言六段，每段写出要点和要引用的文献键：P1 研究背景，两三句内落到本文的具体问题；P2 现有研究的思路，按路线讲清"
        "各自怎么做、解决了什么；P3 存在的普遍问题，指出技术上的缺口并解释它为什么出现；P4 本文的核心想法，独立成段，写关键"
        "观察和它为什么能补上这个缺口；P5 方法与实验概述，每一步为什么需要，少写细节，给出主结果；P6 三条贡献，条目式，逐条"
        "递进、互不重复，每条注明对应方法的哪一部分和哪一组实验。\n"
        "4. 动机图：引言里的一张图，对比现有做法和本文的想法；写出每个面板画什么、读者一眼要看到的区别。\n"
        "5. 相关工作：3–4 个小节，每条相邻的技术路线一节。每节列出要覆盖的文献键（从文献池取，按发展脉络排序，每篇一句话的"
        "定位）、这条路线内部的递进关系，最后落到它留下的、本文要解决的问题。目标期刊近两年的相关论文要用上。池里缺的重要"
        "文献列在“需补入文献池”（题目加 DOI 或 arXiv 编号，检索核实过再列，核实不了的注明“未核实”），实验方下一阶段补入。\n"
        "6. 主张—证据对应表：| 主张 | 对应贡献 | 回答的研究问题 | 实验（表、图、运行编号） | 结论一句话 |，C1 起编号。实验一节"
        "按主张分小节；不支撑任何主张的实验（实现细节、编码细节、负面对照）写明移到附录还是补充材料。\n"
        "7. 方法一节的小节划分：每小节对应引言里的哪个问题，以及为什么这样设计（一两句）。\n"
        "8. 篇幅和文献目标：引言、相关工作的词数和段数，参考文献总数、近两年条数、目标期刊条数；逐小节列出计划引用的条数，"
        "合计达到目标。\n"
        "9. 讨论与局限：有实质内容（适用条件、失败情形的分析）才单独成节，否则写明并入实验分析还是结论。",
        "完整的提纲（会保存为 research/outline.md），末尾列“仍缺的材料”和“需作者决策”"),
    "draft": (
        "任务：{outline_ref}，以及 references/writing.md 和 references/writing-patterns.md（research/writing_profile/ "
        "下有目标期刊或会议的画像和门槛时按它校准），在论文目录 {paper_abs} 中撰写或更新初稿（主文件 {tex}）：摘要、引言、相关"
        "工作、方法、实验、结论。{advisor}{writing_note}\n"
        "引言按提纲的六段写：背景、现有研究的思路、存在的普遍问题、本文的核心想法（独立成段）、方法与实验概述、三条贡献；贡献"
        "点写成条目式的三条，每条对应方法的一部分和一组实验；引言引用动机图。相关工作按提纲的小节写，每条路线讲清代表工作各自"
        "做了什么、彼此怎样递进，每小节最后落到它留下的问题；不把多篇文献压成一句带过。方法每个小节先用一两句说明它解决引言里"
        "的哪个问题、为什么这样设计，再给定义和公式。实验按提纲的主张分小节，每小节依次写：这个实验回答什么问题、设置、结果、"
        "这个结果说明什么；继承的部件带来的收益和本文新增部分的收益分开报告；提纲标为附录或补充材料的实验不进正文。不设只有"
        "几句话的讨论一节。\n"
        "引用只用已核实的条目：从 research/refs_pool.bib 把要引的条目复制进论文目录的 .bib（引用键不变，已有同键条目时不重复），"
        "或用论文目录 .bib 里已有的条目；文献池里没有的文献不凭记忆写，列进“仍缺的材料”。引用一篇文献前读过它在 "
        "research/refs_pool.md 里的摘要或题目，确认它确实属于所在的那条路线。篇幅和参考文献数达到提纲第 8 项的目标。\n"
        "方法以 research/method_card.md 为准；数字只能来自 research/runs.md 和 research/results/，图使用论文目录 figs/ 中已生成的图；"
        "遵守发布会原则和诚信边界，正文不出现文件名。",
        "修改摘要、各节词数和参考文献数、仍缺的材料和需作者决策的问题"),
    "revise": (
        "任务：读 research/lint_report.md（文稿与图表检查结果），按 references/writing.md 的改稿流程修改论文目录 {paper_abs} 中的"
        "稿件：逐处处理，最小改动，不改数据和事实；删除结果、新增事实一律列入“需作者决策”。{advisor}{writing_note}\n"
        "报告里“结构与篇幅”一类的问题不适用最小改动，按 research/outline.md（如果存在）和 references/writing.md 第 4 节补写：引言补齐"
        "六段、贡献点改成条目式的三条；相关工作按路线补全代表工作和近两年的工作，写出递进关系和每条路线留下的问题；参考"
        "文献从 research/refs_pool.bib 补到门槛以上（条目复制进论文目录的 .bib，引用键不变），只引读过摘要、确实相关的条目，"
        "文献池不够时把缺的路线列进“需作者决策”，不凭记忆补文献，也不为凑数引用不相关的论文；过短的讨论并入实验分析或结论。"
        "报告里“参考文献核对”一节：不一致的条目（DOI 或 arXiv 编号对应的是另一篇论文）对着 research/refs_pool.bib 改正，池里"
        "没有的列进“需作者决策”；年份不同和查不到的条目不擅自删改，列进“需作者决策”请作者对着原文核对。",
        "问题表（编号、位置、原文、类型、改法、理由）和“需作者决策”清单"),
    "polish": (
        "任务：论文目录 {paper_abs} 里是 ARIS 写好并经过它的改进循环的稿件（主文件 {tex}，各节通常在 sections/ 下）。按发布会"
        "原则和 references/writing.md 的改稿流程通读全文，逐处找出问题并直接改稿：最小改动，保持原有用词习惯和段落结构，不整段"
        "重写，没问题的地方不动。重点看：叙事是否围绕本文最强的优势，按“问题为何重要 → 现有方法为何不够 → 本文提供什么 → 证据"
        "如何支持”展开；正文里的自我削弱表达、疑问句和文件名；正文的条目式列表改成整段叙述（引言里的贡献点可以保留）；每个实验"
        "段落是否承担明确的论证职责；术语和符号前后一致。research/lint_report.md（检查脚本的结果）如果存在，其中每一处都要处理"
        "或写明不改的理由；其中“结构与篇幅”一类的问题，不新增引用、不改 claim 就能解决的（引言分段、贡献点改成条目式的三条、"
        "过短的讨论并入其他节）直接改，需要新增文献或补写大段内容的列入“需作者决策”，交回 ARIS 处理。research/writing_profile/ 下有目标会议的写作画像时按它校准，没有就用 references/writing-patterns.md。",
        "问题表（编号、位置、原文、类型、改法、理由）、改动过的文件清单和“需作者决策”清单"),
}

CLAUDE_TASKS = {
    "diagnose": ("{recheck}在主评测上为选中的候选（{chosen}）量关口一的证据和提升空间，按 {cards_file} 写的方案和机会类型："
                 "条件崩溃类跑分层误差、受控扰动或失败样例，再量失效代价（失效条件下比正常条件差多少）和上限（把失效去掉后基线能到"
                 "多少，例如真值标定的输入、理想投影；做不到时用最接近的版本并写明）；去掉特权输入类，同一设置下分别用特权输入和现实"
                 "替代跑基线；效率类测质量与代价曲线。只用现有方法，不实现新方法。每次运行在 research/runs.md 记一行，原始结果写进 "
                 "research/results/。只靠文献证据就能判定时，可以 `done --skip --note <原因>` 跳过。"),
    "code_survey": ("拉取最近一次关口一判定列出的最接近工作的开源代码，分析每个仓库基于哪些代码库、改了哪些文件、改了多少：\n"
                    "  python {skill}/scripts/code_lineage.py --from-md {gate1_file} --out research/code_survey\n"
                    "报告写在 research/code_survey/lineage.md，只陈述代码事实，不加你的判断；判断留给 Codex。判定里没有可用的"
                    "代码地址或网络不通时用 `done --skip --note <原因>`。"),
    "screen": ("{screen_control}{screen_setting_task}按 research/method_candidates.md 里各候选的初筛方案，并行初筛排在前面的候选 {screen_list}（每个不超过 {screen_per} GPU "
               "小时，合计不超过 {screen_total}；估算会超出就缩小规模）：只做最小实现，只用验证集；能热启动就从基线权重出发，并让基线同步"
               "训练同样的步数作对照；同时跑上限和阳性对照；只调候选自己新加的超参（最多两个、每个最多三个取值），不动基线的超参。每次"
               "运行在 research/runs.md 记一行（职责表编号写 S-候选号），原始结果写进 research/results/。最后把结果写成 "
               "research/screen.md 的表格（覆盖旧文件），表头固定为：\n"
               "| 候选 | 指标 | 基线 | 候选结果 | 上限 | 回收比例 | 达到判据 | 运行编号 |\n"
               "候选一栏只写编号（如 2）；基线一栏用同步训练过的基线；回收比例 =（候选结果 − 基线）/（上限 − 基线），按指标方向算；"
               "达到判据一栏按初筛方案事先写的判据填“是”“接近”或“否”，基线在这个设置里没有出现不足（阳性对照不成立）时填“对照无效”。"
               "只填数字和事实，不加你的判断。没有 GPU、数据或时间时用 `done --skip --note <原因>`，流程会按 Codex 的推荐继续。"),
    "code_location": ("读代码库，在 research/method_card.md 的“代码修改位置”一节写出要改的文件（带扩展名的路径）、类或函数和改动"
                      "类型（新增、替换、插入调用），删掉“待实验方填写”；只改这一节。方法卡刚按审查或关口二修订过时，这一节末尾"
                      "如果有“需实验方更新：……”，按要求调整并删掉这句；没有就核对后直接 done。{cs_hint}{skill}/references/code-patterns.md "
                      "第 4 节有各基础代码库的常见改动位置。方法卡写不下去的地方记进 research/deviations.md。"),
    "run": ("{probe_run}按 research/experiment_plan.md 实现方法并运行除“最终报告”以外的全部实验（{todo}）。每次运行在 research/runs.md 记一行，"
            "原始结果写进 research/results/，完成后更新职责表的“状态”列；调参和模型选择只用验证集，数据划分一栏不能是测试集；"
            "偏离方法卡或职责表的改动先记进 research/deviations.md。上一轮审查意见见 research/review.md（如有）。"),
    "final_eval": ("用已定好的配置在测试集上跑职责表中标为“最终报告”的实验，只跑一次；在 research/runs.md 新增的这些行的状态里写"
                   "“最终报告”，并把职责表中对应行的状态改为“已完成”。"),
    "profile": ("为写作做三项准备（目标期刊或会议、子领域见 CLAUDE.md 的项目信息；细节见 {skill}/references/writing.md 第 4.1 节）。\n"
                "一、文献池（全部取自 Crossref 和 arXiv 的元数据，写进 research/refs_pool.md 和 refs_pool.bib）：\n"
                "  python {skill}/scripts/lit_pool.py find \"<目标期刊全名>\"        # 查 ISSN；目标是会议时跳过这一条和 venue\n"
                "  python {skill}/scripts/lit_pool.py venue --issn <ISSN> --query \"<主题词>\" --from <去年> --must \"<主题正则>\" --tag \"<期刊简称>\"\n"
                "  python {skill}/scripts/lit_pool.py topic --query \"<一条技术路线的关键词>\" --tag \"<路线名>\"   # 每条相邻路线各跑一次\n"
                "  python {skill}/scripts/lit_pool.py arxiv --query \"<主题词>\"\n"
                "  python {skill}/scripts/lit_pool.py doi <基线、数据集、指标和最接近工作的 DOI>\n"
                "技术路线取自 research/gates.md 列出的最接近工作和 research/method_card.md；论文目录已有 .bib 时先 "
                "`lit_pool.py import <.bib>` 再 `lit_pool.py check <.bib> --add`。每条路线不少于 8 条，合计不少于参考文献门槛的 1.5 倍"
                "（期刊约 60 条）；读一遍 refs_pool.md，明显不相关的用 `lit_pool.py drop <键>` 去掉。\n"
                "二、范文和门槛：从文献池“目标期刊”一组挑 2–3 篇同主题的近两年论文，PDF 放 research/writing_profile/exemplars/（正式版"
                "下载不到时用它的 arXiv 版本；都拿不到就不做范文，norms 不带 --exemplars），逐篇转成文本（pdftotext 不加 -layout，或用 "
                "PyMuPDF）再量：\n"
                "  pdftotext <pdf> <txt> && python {skill}/scripts/paper_lint.py <txt> --stats > research/writing_profile/exemplars/<名字>.json\n"
                "  python {skill}/scripts/lit_pool.py norms --issn <ISSN> --query \"<主题词>\" --must \"<主题正则>\" --venue \"<简称>\" "
                "--venue-type journal|conference --target-regex \"<期刊名的正则，同时覆盖全名和缩写>\" --exemplars research/writing_profile/exemplars/*.json\n"
                "（正则例：Circuits and Systems for Video Technology|Circuits Syst\\.? Video Technol|TCSVT）\n"
                "再把每篇范文的引言每段讲什么、相关工作分哪几个小节、各节词数和参考文献数记进 research/writing_profile/exemplars.md，"
                "只记结构和数字，不摘抄原文。目标是会议时 norms 不带 --issn。\n"
                "三、写作画像（目标是会议时做；期刊可省）：\n  python {skill}/scripts/corpus/build_corpus.py --out research/writing_profile "
                "--fields \"<子领域名>=<正则>\" --venues <目标会议及相近会议> --target 600\n  python {skill}/scripts/corpus/extract.py "
                "--dir research/writing_profile\n  python {skill}/scripts/corpus/stats.py --dir research/writing_profile\n"
                "完成的标志是 research/refs_pool.md，加上 venue_norms.json 或 stats.json 之一。Crossref 和 arXiv 都连不上时用 "
                "`done --skip --note <原因>`，写作时只能引用论文目录已有的文献，门槛用 references/writing-patterns.md 第 13 节的默认值。"),
    "figures": ("按 research/outline.md 准备全文的图，存到 {paper}/figs/：\n"
                "动机图：按提纲第 4 项，对比现有做法和本文的想法，放在引言；用 {skill}/scripts/framework_figure.py 画（色块分区，"
                "不放结果数字），或用提纲指定的真实样例拼图。\n"
                "数据图：用 {skill}/scripts/paper_plot.py 从 research/results/ 的原始数据画，按目标期刊或会议的版心宽度，只画提纲"
                "主张—证据对应表里进正文的实验。\n"
                "算法框架图：用 {skill}/scripts/framework_figure.py 按 research/method_card.md 画（色块分区、底部机理说明、不放结果）。\n"
                "效果图：任务有可视化输出时按 {skill}/references/figures.md 第 7 节出一张，帧或样例按事先写明的规则选；3D 检测用 "
                "{skill}/scripts/qualfig.py。\n"
                "每张图看 PNG 预览，修到遮挡检查通过。另外，提纲“需补入文献池”一节列出的文献，用 `python {skill}/scripts/lit_pool.py "
                "doi <DOI>` 或 `arxiv --query <题目>` 加进文献池，查不到的记进 research/deviations.md。"),
    "lint": ("运行下面的命令，把输出合并写进 research/lint_report.md（覆盖旧报告）：\n  python {skill}/scripts/paper_lint.py "
             "{paper}/{tex}\n  python {skill}/scripts/figure_table_check.py {paper}/{tex} --venue <目标会议或期刊，如 cvpr、ieee、neurips>\n"
             "  python {skill}/scripts/lit_pool.py check {paper}/<稿件的 .bib> --out research/refs_check.md    # 连不上 Crossref 时写明没有核对\n"
             "paper_lint 按文档类判断期刊稿还是会议稿，research/writing_profile/venue_norms.json 存在时用其中的门槛和目标期刊；"
             "两者都没有时加 `--venue-type journal|conference` 和 `--target-venue \"<期刊全名的正则>\"`；RA-L、SPL 这类限 8 页以内的"
             "快报用 IEEEtran 的期刊模板，但篇幅按会议稿，加 `--venue-type conference`。refs_check.md 的“参考文献核对”一节原样"
             "并入报告（不改级别）。"),
    "handback": ("收尾改动会让 ARIS 的审计记录过期（它按文件哈希核对稿件）。在本项目里依次运行 ARIS 的 paper-claim-audit 和 "
                 "citation-audit 技能，参数都是 \"{paper}/\"（装的是 method-paper 插件时命令是 /method-paper:paper-claim-audit，官方 aris "
                 "插件是 /aris:paper-claim-audit，用脚本安装的是 /paper-claim-audit，其余技能同理）；论文有定理，或者标题和摘要里有普适性"
                 "的说法时，再跑 kill-argument；最后跑 paper-compile。审计要求改数字或替换引用时按 ARIS 的流程处理（替换和删除引用要用户"
                 "确认），改完把改动告诉用户。准备投稿时还要按 ARIS paper-writing 第 5.9 和 6.0 节跑 integrity-forensics 和 "
                 "verify_paper_audits.sh。没有装 ARIS，或者 ARIS 的审稿通道（默认是 codex MCP，插件里用 /method-paper:setup 配置；也可以换成 claude-review，见 references/aris.md 第 7 节）没配好时，"
                 "用 `done --skip --note <原因>`。"),
}
# polish mode: the facts live in ARIS's files, not in research/
CLAUDE_TASKS_POLISH = {
    "lint": ("运行下面两条命令，把输出合并写进 research/lint_report.md（覆盖旧报告）：\n  python {skill}/scripts/paper_lint.py "
             "{paper}/{tex} --structure-severity 中\n  python {skill}/scripts/figure_table_check.py {paper}/{tex} --venue "
             "<目标会议或期刊，如 cvpr、ieee、neurips>\n引用由 ARIS 的 citation-audit 核对，这里不重复。"),
    "profile": ("为目标会议和本文子领域生成写作画像（子领域和目标会议看 PAPER_PLAN.md 或 NARRATIVE_REPORT.md）：\n  python {skill}/scripts/"
                "corpus/build_corpus.py --out research/writing_profile --fields \"<子领域名>=<正则>\" --venues <目标会议及相近会议> "
                "--target 600\n  python {skill}/scripts/corpus/extract.py --dir research/writing_profile\n  python {skill}/scripts/corpus/"
                "stats.py --dir research/writing_profile\n网络不通或样本太少时用 `done --skip`，收尾时改用 references/writing-patterns.md。"),
    "figures": ("检查 ARIS 生成的图：\n  python {skill}/scripts/figure_table_check.py {paper}/{tex} --venue <目标会议，如 cvpr、ieee、neurips>\n"
                "字号和正文差得多、文字压住线条或内容的数据图，用 {skill}/scripts/paper_plot.py 从同一份原始数据（results/ 等）按目标会议"
                "版心宽度重画；算法框架图不符合色块分区、底部写各区域机理、不放结果这几条时，用 {skill}/scripts/framework_figure.py 按 "
                "NARRATIVE_REPORT.md 和 PAPER_PLAN.md 的方法描述重画。重画的图覆盖原文件、文件名不变（.tex 里的引用不用改），不改图里"
                "的数据；每张图看 PNG 预览，修到遮挡检查通过。所有图都合格时用 `done --skip --note <检查结果>`。"),
}

CHECKPOINTS = {
    "cp_candidates": "选择要做诊断验证的候选（逗号分隔，如 C2,C4），或回复 stop 暂停。",
    "cp_method": "选择要采用的方法编号（如 2）；都不满意时回复 back 加理由（如“back 三个方案都只是换损失”）让 Codex 重新找；stop 暂停。",
    "cp_final": "所有实验与审查已通过。是否批准在测试集上跑一次最终报告（yes / no）？",
}


class Invalid(Exception):
    pass


def now():
    return dt.datetime.now().isoformat(timespec="seconds")


def load():
    if not STATE.exists():
        sys.exit("还没有初始化：先在论文仓库根目录运行 python pipeline.py init")
    st = json.loads(STATE.read_text(encoding="utf-8"))
    d = st.setdefault("decisions", {})
    # state files from before anchors and pilot evidence were recorded
    if "anchors" not in d and d.get("passed_candidate"):
        d["anchors"] = [{"id": d["passed_candidate"], "since": "旧记录"}]
    note = str(d.get("screen_note", ""))
    if "screen_positive" not in d and note.startswith("初筛达到判据"):
        items = note.split("：", 1)[-1].split("、")
        d["screen_positive"] = [m.group(1) for m in (re.match(r"\s*(\d+)", x) for x in items) if m]
    return st


def save(st):
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")


def log(st, stage, event, note=""):
    st["history"].append({"time": now(), "stage": stage, "event": event, "note": note})


# ----------------------------------------------------------------------------- runs.md
def runs_rows():
    """Table rows of research/runs.md (header and separator excluded) and the column names."""
    p = R / "runs.md"
    if not p.exists():
        return [], []
    rows = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip().startswith("|")]
    header = []
    if rows and not re.match(r"^\|[\s\-:|]+\|$", rows[0]):
        header = [c.strip() for c in rows[0].strip("|").split("|")]
    body = [r for r in rows[1:] if not re.match(r"^\|[\s\-:|]+\|$", r)] if header else rows
    return body, header


def cells(row):
    return [c.strip() for c in row.strip().strip("|").split("|")]


TEST_SPLIT = re.compile(r"\btest\b|测试", re.I)


# ----------------------------------------------------------------------------- PIPELINE line
FENCE = re.compile(r"^\s*(```|~~~)")


def last_line(path):
    lines = [l.strip() for l in path.read_text(encoding="utf-8").splitlines()]
    lines = [l for l in lines if l and not FENCE.match(l)]
    return lines[-1] if lines else ""


def parse_pipeline(line):
    """Parse 'PIPELINE: k=v k=v reason=free text'. Tolerates markdown decoration and a full-width colon."""
    s = re.sub(r"^[\s`*>_#\-]+", "", line)
    s = re.sub(r"[\s`*_]+$", "", s)
    m = re.match(r"^PIPELINE\s*[:：]\s*(.*)$", s)
    if not m:
        return None
    body = re.sub(r"\s*,\s*", ",", m.group(1).replace("，", ","))
    out = {}
    rm = re.search(r"\breason\s*=\s*(.*)$", body)
    if rm:
        out["reason"] = rm.group(1).strip()
        body = body[:rm.start()]
    for k, v in re.findall(r"(\w+)\s*=\s*([A-Za-z0-9_,.\-]+)", body):
        out[k] = v.strip(",.")
    return out


def check_pipeline(stage, pl):
    """Normalise the parsed PIPELINE values against the stage spec; raise Invalid on a malformed line."""
    spec, res = stage["pl"], {}
    for k, (kind, *rest) in spec.items():
        if k not in pl:
            continue
        v = pl[k]
        if kind == ENUM:
            v = v.lower()
            if v not in rest[0]:
                raise Invalid(f"PIPELINE 行里 {k}={pl[k]} 不是允许的值（{'/'.join(sorted(rest[0]))}）")
        elif kind == LIST:
            v = [x.strip().upper() for x in v.split(",") if x.strip()]
            if not v:
                raise Invalid(f"PIPELINE 行里 {k} 为空")
        elif kind == ID:
            v = v.upper()
        res[k] = v
    sid = stage["id"]
    need = {"problems": ["candidates", "recommend"], "gate1": ["gate1"], "methods": ["options", "recommend"],
            "gate2": ["gate2"], "review": ["review"]}.get(sid, [k for k in spec if spec[k][0] == ENUM])
    if sid == "methods" and res.get("back") == "problems":
        need = []
    elif sid == "methods" and "options" in res:
        need = ["options", "recommend"]
    if sid == "gate1" and res.get("gate1") == "pass":
        need = need + ["candidate"]
    if sid == "review" and res.get("review") == "more":
        need = need + ["card"]
    missing = [k for k in need if k not in res]
    if missing:
        raise Invalid(f"PIPELINE 行缺少 {','.join(missing)}")
    if sid == "problems":
        bad = [x for x in res["candidates"] + res["recommend"] if not re.fullmatch(r"C\d+", x)]
        if bad:
            raise Invalid(f"候选编号格式不对：{bad}（应为 C1、C2……）")
        if not set(res["recommend"]) <= set(res["candidates"]):
            raise Invalid("recommend 里有不在 candidates 中的编号")
    if sid == "methods" and "options" in res and "recommend" in res and res["recommend"] not in res["options"]:
        raise Invalid("recommend 不在 options 中")
    if sid == "gate1" and res.get("gate1") == "pass" and not re.fullmatch(r"C\d+", res["candidate"]):
        raise Invalid(f"candidate={res['candidate']} 不是候选编号")
    return res


def check_numbering(st, res):
    """New problem candidates must continue the numbering so earlier, rejected candidates keep their IDs."""
    top = st["decisions"].get("max_candidate", 0)
    old = [x for x in res["candidates"] if int(x[1:]) <= top]
    if old:
        raise Invalid(f"候选编号 {old} 和前几轮重复；本轮应从 C{top + 1} 开始编号")


# ----------------------------------------------------------------------------- prompts and commands
def root():
    return Path.cwd().resolve()


def paper_abs(st):
    return (root() / st["paper_dir"]).resolve()


def run_file(st, sid):
    return R / "codex" / f"{sid}-{st['codex_runs'].get(sid, 1)}.md"


def has_cs(st):
    """A code survey exists and belongs to the failure mechanism that passed gate one most recently."""
    d = st["decisions"]
    return (R / "code_survey" / "lineage.md").exists() and d.get("code_survey_for") == d.get("passed_candidate")


def first_option(st):
    """Method candidates are numbered on from earlier rounds so a number always means one candidate."""
    d = st["decisions"]
    nums = [int(x) for x in d.get("method_options", []) if str(x).isdigit()]
    return max([d.get("max_option", 0)] + nums) + 1


ROUND_FLAGS = ("refine", "reposition", "stack")


def clear_round_flags(d):
    """Forget per-round method-search state (refinement, strengthening) when the author or a new anchor resets the search."""
    for k in ROUND_FLAGS:
        d.pop(k, None)
    d["refine_rounds"] = 0


def is_recheck(d):
    """Gate one is re-checking the current anchor's headroom (after a screen without a working control, or goto --reason)."""
    return bool(d.get("headroom_doubt") and d.get("passed_candidate"))


def gate1_chosen(st):
    """Candidates gate one judges: the anchor itself on a headroom re-check, else the ones the author picked."""
    d = st["decisions"]
    return [d["passed_candidate"]] if is_recheck(d) else d.get("candidates", [])


CYCLE_CAUSES = {"screen": "初筛未达标", "gate2": "关口二判换方法", "probe": "机制预测落空"}


def reset_cycles(rnd):
    rnd["method"] = 0
    for k in CYCLE_CAUSES:
        rnd.pop(f"method_{k}", None)


def use_cycle(st, cause):
    """Count one used method cycle on the current anchor; return the pause message once the anchor's cycles are used up."""
    d, rnd, lim = st["decisions"], st["rounds"], st["limits"]
    d["method_failures"] = d.get("method_failures", 0) + 1
    rnd["method"] = rnd.get("method", 0) + 1
    rnd[f"method_{cause}"] = rnd.get(f"method_{cause}", 0) + 1
    if rnd["method"] < lim["method_rounds"]:
        return None
    parts = "、".join(f"{v} {rnd[f'method_{k}']} 次" for k, v in CYCLE_CAUSES.items() if rnd.get(f"method_{k}"))
    return (f"锚点 {d.get('passed_candidate') or ''} 已用完 {rnd['method']} 个方法周期（{parts}）。下一步由作者决定：换一类算子再来"
            "一个周期（goto methods）、换到提升空间更大的评测设置（goto diagnose --reason '<设置>'）、换锚点或重新探索"
            f"（goto problems）{other_anchors(d)}。可以先让 Codex 读否定记录写一份转向备忘")


def anchor_entry(d, aid):
    return next((x for x in d.get("anchors", []) if x["id"] == aid), {})


def switch_anchor(st, new, info=None):
    """Make `new` the current anchor. Method failures are kept per anchor; per-round search state restarts on a change."""
    d = st["decisions"]
    old = d.get("passed_candidate")
    anchors = d.setdefault("anchors", [])
    for x in anchors:
        if x["id"] == old:
            x["failures"] = d.get("method_failures", 0)
    if new not in [x["id"] for x in anchors]:
        anchors.append({"id": new, "since": now()[:10]})
    anchor_entry(d, new).update(info or {})
    d["passed_candidate"] = new
    if new != old:
        clear_round_flags(d)
        d["method_failures"] = next((x.get("failures", 0) for x in anchors if x["id"] == new), 0)
        for k in ("gate2_reason", "method_feedback", "rejected_methods", "mechanism_rejected"):
            d.pop(k, None)
        reset_cycles(st["rounds"])
        for k in ("gate2_revise", "reposition"):
            st["rounds"][k] = 0


def other_anchors(d):
    ids = [x["id"] for x in d.get("anchors", []) if x["id"] != d.get("passed_candidate")]
    return f"；换到另一个锚点（{'、'.join(ids)}）用 goto methods --anchor <编号>" if ids else ""


def stack_candidates(st):
    """Other candidates that met the criterion in the latest screen, when the chosen method met it too."""
    d = st["decisions"]
    return [x for x in d.get("screen_positive", []) if x != d.get("method")] if has_pilot(st) else []


def base_text(d):
    """How prompts name the method kept as the base of a strengthening round."""
    rp = d.get("reposition") or {}
    name = f"方案 {rp['method']}" if rp.get("method") else "当前方法"
    card = rp.get("card") or "research/codex/ 下最近一次 method_card 输出"
    gate = rp.get("gate2") or d.get("gate2_last_file") or "research/gates.md 最近一次关口二判定"
    return rp, name, card, gate


def has_pilot(st):
    """The chosen method met its pre-registered criterion in the latest screen."""
    d = st["decisions"]
    return bool(d.get("method")) and d.get("method") in d.get("screen_positive", [])


def screen_list(st):
    n = st.get("screen", {}).get("n", 4)
    opts = st["decisions"].get("method_options", [])
    return "、".join(opts[:n]) if opts else f"前 {n} 个"


def cards_file(d):
    """Where the cards gate one and the diagnosis read: the anchor's own problems run on a re-check."""
    if is_recheck(d):
        return anchor_entry(d, d["passed_candidate"]).get("card") or "research/codex/ 下较早的 problems 输出（编号对得上的那一份）"
    return "research/problem_cards.md"


def reposition_text(d):
    rp, name, card, gate = base_text(d)
    if not rp:
        return ""
    if d.get("refine"):
        return f"本轮仍属补强：主体是{name}（方法卡见 {card}），改进版仍然写成主体加一个加强，初筛对照仍是主体本身。"
    return (f"这是补强轮：关口二认为{name}有效，但和最接近工作的差距不够（{rp.get('reason')}，见 {gate}），主体的方法卡见 {card}。"
            "本轮不必凑满 6–8 个：保留它作为主体，给出 2–4 个加强方向，每个写成一个可初筛的候选（主体加这个加强），算子要和主体"
            "不同，例如叠加支撑组件、换到去掉特权输入的设定、把近似换成精确计算；初筛对照是主体本身。")


def fmt(text, st):
    d = st["decisions"]
    tried = d.get("tried_candidates", [])
    fb = []
    if d.get("gate2_reason"):
        fb.append(f"上一轮候选被否定的原因：{d['gate2_reason']}；新的候选要针对这个问题。")
    if d.get("method_feedback") is not None:
        fb.append(f"用户否决了上一轮的方案（见 {d.get('rejected_methods', 'research/codex/ 下较早的 methods 输出')}）"
                  f"{('，理由：' + d['method_feedback']) if d['method_feedback'] else ''}；新的候选要和它们不同。")
    diag = ""
    if d.get("diagnose_skipped"):
        diag = f"本轮没有做诊断实验（{d['diagnose_skipped']}），只能依据文献证据判定，证据不足时判 fail。"
    if is_recheck(d):
        a = d["passed_candidate"]
        src = "（research/screen.md）" if d["headroom_doubt"].startswith("初筛") else ""
        last = anchor_entry(d, a).get("gate1") or "research/gates.md"
        diag += (f"这是复核：{d['headroom_doubt']}{src}。只判定锚点 {a}（问题卡见 {cards_file(d)}，上次关口一判定见 {last}）："
                 f"按提升空间重新判定，空间够就写 gate1=pass candidate={a}，不够就判 fail。")
    elif d.get("headroom_doubt"):
        diag += f"作者的疑点：{d['headroom_doubt']}。"
    return text.format(
        common=(POLISH_COMMON if polish_mode(st) else COMMON).format(skill=SKILL_DIR, root=root()), skill=SKILL_DIR, root=root(),
        paper=st["paper_dir"], paper_abs=paper_abs(st), tex=st["main_tex"],
        chosen=",".join(gate1_chosen(st)) or "见 problem_cards.md 的推荐",
        passed=d.get("passed_candidate") or "见 gates.md 最近一次关口一判定", method=d.get("method") or "推荐方案",
        first=f"C{d.get('max_candidate', 0) + 1}", first_option=first_option(st),
        exclude=((f"已经验证过、未通过的候选：{','.join(tried)}（见 research/codex/ 下较早的 problems 输出和 research/gates.md），"
                  "不要重复这些机会，换新的。" if tried else "")
                 + (f"作者放弃了上一个锚点（{d['mechanism_rejected']}，详见 research/review.md 和 research/findings.md）；新的候选要能"
                    "解释这组否定结果。" if d.get("mechanism_rejected") else "")),
        seed=(f"作者指定了一个候选，编号用 C{d.get('max_candidate', 0) + 1}，必须收录、按同样格式写成问题卡并认真评估（不受"
              f"“不要重复”的限制），并作为推荐之一，除非证据明确不支持：{d['seed']}" if d.get("seed") else ""),
        anchors=(("已通过关口一的锚点：" + "、".join(a["id"] for a in d["anchors"]) + "（见 research/gates.md）。它们的提升空间量过、"
                  "仍然有效，新一轮不要重复这些机会。") if d.get("anchors") else ""),
        refine=(("这是细化轮，本轮不必凑满 6–8 个：上一轮初筛里接近判据的候选是 " + "、".join(d["refine"]) + "（见 research/screen.md）。针对每个给出"
                 "最多两个改进，改的是计算而不是超参（换插入点、和另一个算子组合、修掉初筛暴露的问题），再补最多两个新候选；"
                 "改进版用新编号，写明是从哪个候选改来的。") if d.get("refine") else ""),
        reposition=reposition_text(d),
        reposition_card=(f"这是补强后的方法：主体是{base_text(d)[1]}，原方法卡见 {base_text(d)[2]}（关口二的意见见 {base_text(d)[3]}）。"
                         "把选中候选的加强并进主体，写成一张完整的方法卡，本质差异一节写清加强之后和最接近工作的差距。"
                         if d.get("reposition") else ""),
        screen_control=(f"这是补强轮：初筛的对照是主体{base_text(d)[1]}（原方法卡见 {base_text(d)[2]}，有初筛里的最小实现就沿用），"
                        "同样热启动、同样步数；表格的“基线”一栏填主体的结果，回收比例按主体算。" if d.get("reposition") else ""),
        screen_list=screen_list(st), cards_file=cards_file(d),
        screen_setting=((f"作者指定了初筛设置：{st['screen']['setting']}。每个候选的初筛方案都在这个设置里做，并先在这个设置里确认"
                         "基线确实出现不足（阳性对照）。") if st.get("screen", {}).get("setting") else ""),
        screen_setting_task=((f"初筛在作者指定的设置里做：{st['screen']['setting']}。") if st.get("screen", {}).get("setting") else ""),
        pilot_status=((f"本方法（方案 {d.get('method')}）在初筛中达到了判据，数字见 research/screen.md。")
                      if has_pilot(st) else
                      "本方法没有初筛证据（初筛被跳过，或选的不是初筛达标的候选），机制预测还没有实验证据时最多判有条件通过。"),
        feedback="".join(fb), diag=diag,
        review_more=("research/review.md 要求补的实验写成新行（状态“待做”），要求修实现的写进对应行的备注。"
                     if d.get("review_more") else ""),
        gate1_file=run_file(st, "gate1"),
        recheck=(f"这是复核：{d['headroom_doubt']}。重点是在之后评测方法的那个设置里量失效代价和上限，已有的分层结果可以沿用。"
                 if d.get("headroom_doubt") else ""),
        screen_n=st.get("screen", {}).get("n", 4), screen_per=st.get("screen", {}).get("per", 2),
        screen_total=st.get("screen", {}).get("total", 8),
        screen_card=(("初筛结果见 research/screen.md：方法卡写进初筛的数字和对照，沿用初筛里的最小实现、阳性对照和上限，机制预测的"
                      "量级要和初筛结果对得上。" if d.get("screened", (R / "screen.md").exists()) else "")
                     + (("初筛里另外达到判据的候选 " + "、".join(stack_candidates(st)) + " 可以写成支撑组件（每个组件都要有消融），"
                         "正式实验用消融确认增益能叠加；它们和本方案算子相同、改的是同一处时只取一个。") if stack_candidates(st) else "")),
        synthesis=(f"这是同一锚点下第 {d.get('method_failures', 0) + 1} 个周期。先读否定记录和历次初筛，综合回答三件事：锚点在评测"
                   "设置里还剩多少提升空间（引用上限实验）；哪几类算子、哪几处插入点已经试过；新候选凭什么不会重蹈覆辙。只有提升空间"
                   "被实验否定时才写 back=problems（流程会停下来请作者决定）。" if d.get("method_failures", 0) >= 2 else ""),
        gate2_file=run_file(st, "gate2"),
        rejudge=(f"这是修订后的复判：只核对上一次判定（{d.get('gate2_last_file')}）列出的缺项是否补齐、方法卡改动处是否引入新"
                 "问题，不新增上次没提出、方法卡也没改动的意见。" if d.get("gate2_last_verdict") == "revise" else ""),
        probe_plan=("关口二是有条件通过：本轮职责表只写检验机制预测所需的最小实验（references/experiments.md 第 3.1 节的机制"
                    "探针）：失效子集、验证集；阳性对照（基线在这个设置里确实出现失效，损失不小于冻结的最小效果）、上限和等容量对照"
                    "先于方法行；每行事先写明预测、支持和落空的判据，以及中止规则（第一批种子后置信区间上界低于最小效果就停）；"
                    "其余实验等预测得到支持后再写。" if d.get("gate2_conditional") else ""),
        probe_review=("本轮是关口二有条件通过后的机制探针：先按职责表事先写下的判据，判断最小验证实验是否支持方法卡里的机制预测，"
                      "判据不能事后修改。" if d.get("gate2_conditional") else ""),
        probe_run=("关口二是有条件通过：只实现检验机制预测所需的最小版本，只跑职责表里的机制探针行，不调参；先跑阳性对照和上限，"
                   "阳性对照不成立就停下记录，不再跑方法行；按职责表的中止规则，达到无效中止条件就停掉其余运行并在 runs.md 注明。"
                   if d.get("gate2_conditional") else ""),
        cs_hint=(f"{CS} 记录了最接近工作在同一类代码库里改了哪些文件，可作为定位参考；" if has_cs(st) else ""),
        code_survey=(f"先读 {CS}（最接近工作的代码构成：基于哪个代码库、改了哪些文件、各改了多少行、新写了多少）和 references/"
                     "code-patterns.md，弄清这些工作的真实改动落在计算流程的哪一环；新方法不能只是在同一位置做同类改动（例如同样"
                     "只改损失或致密化规则），候选之间也要落在不同环节。" if has_cs(st) else ""),
        code_survey_card=(f"连接方式一节对照 {CS} 和 references/code-patterns.md：写清新机制接在基础代码库计算流程的哪一处、"
                          "替换还是插入，以及怎样做成可以插进多个基线的模块。" if has_cs(st) else ""),
        code_survey_gate2=(f"本质差异要在代码层面也成立：对照 {CS}，如果本方法的代码修改位置和改动类型与某个最接近工作基本相同，"
                           "要求方法卡写明机制上的差异（修订）；写不出差异才判换方法。" if has_cs(st) else ""),
        todo="状态不是“已完成”和“最终报告”的行",
        advisor=("research/advisor_notes.md 是导师、合作者或审稿人的意见，逐条落实，并在输出末尾列“意见处理表”（意见、处理方式、"
                 "位置）。" if (R / "advisor_notes.md").exists() else ""),
        writing_note=(f"作者的要求：{d['writing_note']}。" if d.get("writing_note") else ""),
        outline_ref=("按 research/outline.md 的提纲" if (R / "outline.md").exists() else
                     "research/outline.md 还没有（提纲阶段没有跑过）：先按 references/writing.md 第 4.1 节在输出的开头列出九项提纲，"
                     "再按它写稿；写稿依据这份提纲"),
        plan_note=((f"作者要求补实验：{d['plan_note']}。" + ("research/outline.md 第 1、2、6 项写了现有结果撑不起哪些主张，" if (R / "outline.md").exists() else "")
                    + "把要补的实验写成新行（状态“待做”），已完成的行不动。") if d.get("plan_note") else ""))


def probe_reviews(st):
    """Reviews finished since the last gate-two verdict (rounds of the current mechanism probe); a verdict that paused the
    flow and was then re-run with retry does not count."""
    n, superseded = 0, False
    for h in reversed(st["history"]):
        if h["stage"] == "gate2" and h["event"] in ("done", "assumed-pass"):
            break
        if h["event"] == "start":
            superseded = False
        elif h["stage"] == "review" and h["event"] == "retry":
            superseded = True
        elif h["stage"] == "review" and h["event"] == "done":
            n += 0 if superseded else 1
            superseded = False
    return n


def prompt_text(stage, st):
    key = stage["id"]
    d = st["decisions"]
    if key == "method_card" and d.get("gate2_revise"):
        key = "method_card_gate2_revision"
    elif key == "method_card" and d.get("card_revision"):
        key = "method_card_revision"
    task, output = PROMPTS[key]
    if polish_mode(st) and key in ("polish", "revise"):
        task += ARIS_FREEZE
    first = f"C{d.get('max_candidate', 0) + 1}"
    fl = stage["formats"]
    note = stage.get("note")
    if stage["id"] == "review" and d.get("gate2_conditional"):
        n = probe_reviews(st)
        fl, note = ((PROBE_FINAL_FORMATS, PROBE_FINAL_NOTE.format(n=n)) if n >= PROBE_MAX_PENDING
                    else (PROBE_REVIEW_FORMATS, PROBE_REVIEW_NOTE))
    elif stage["id"] == "review" and st["rounds"].get("review", 0) >= st["limits"]["review_rounds"]:
        fl, note = REVIEW_FINAL_FORMATS, REVIEW_FINAL_NOTE.format(n=st["rounds"]["review"])
    elif stage["id"] == "gate2" and not has_pilot(st):
        fl, note = GATE2_NOPILOT_FORMATS, GATE2_NOPILOT_NOTE
    fo = str(first_option(st))
    formats = "\n".join(f.replace("{first}", first).replace("{first_option}", fo) for f in fl)
    note = note.replace("{first_option}", fo) if note else note
    tail = (f"\n\n输出：{output}，作为你的最终回复。\n最终回复的最后一行必须是 PIPELINE 行：从下面选一行照抄，只把 ? 换成实际编号、"
            f"把中文说明换成实际理由，不加反引号、括注或其他文字。\n{formats}")
    if note:
        tail += "\n" + note
    return fmt("{common}\n\n" + task, st) + tail


def evidence_precheck():
    """Type-A facts about every recorded run, written for the reviewer: does the result file exist, is the run void."""
    rows, header = runs_rows()
    if not rows:
        return None
    file_col = next((i for i, h in enumerate(header) if "结果文件" in h or "result" in h.lower()), None)
    out = ["# 证据预检（流程自动生成，只陈述事实）", "",
           "结果文件缺失、标为作废或证据不可恢复的运行，不能支持任何结论；对应的预测或 claim 算缺少有效证据。", "",
           "| 运行 | 结果文件 | 状态 |", "|---|---|---|"]
    for r in rows:
        c = cells(r)
        refs = [x.strip("`") for x in re.split(r"[,;，；\s]+", c[file_col].strip("`"))] if file_col is not None and len(c) > file_col else []
        refs = [x for x in refs if x and x not in ("-", "—", "无")]
        if "作废" in r:
            status = "作废"
        elif "不可恢复" in r:
            status = "证据不可恢复"
        elif not refs:
            status = "未登记结果文件"
        else:
            miss = [x for x in refs if not any(list(b.glob(x)) if any(ch in x for ch in "*?[") else (b / x).exists()
                                               for b in (R, Path(".")))]
            status = "有效" if not miss else "结果文件缺失：" + "、".join(miss[:3])
        out.append(f"| {c[0] if c else '?'} | {', '.join(refs) or '—'} | {status} |")
    p = R / "evidence_precheck.md"
    p.write_text("\n".join(out) + "\n", encoding="utf-8")
    return p


GUARD = R / "codex_guard.json"
GUARD_SKIP_DIRS = ("research/codex/", "research/prompts/")
GUARD_SKIP_FILES = {"research/pipeline.json", "research/evidence_precheck.md", "research/codex_guard.json"}


def unsandboxed(st):
    """codex_sandbox=none: Codex runs with --dangerously-bypass-approvals-and-sandbox (for containers where its
    bubblewrap sandbox cannot start). The sandbox no longer keeps Codex from writing, so every Codex stage checks
    afterwards that research/ (and, for read-only stages, the paper folder) did not change."""
    return st.get("codex_sandbox") == "none"


def reasoner(st):
    """Who does the reasoning and review stages: codex (codex exec) or claude (a separate headless Claude process)."""
    return st.get("reasoner", "codex")


def guarded(st):
    """Stages whose reasoner is not kept from writing by an OS sandbox are checked afterwards for changes."""
    return unsandboxed(st) or reasoner(st) == "claude"


def guard_scan(st, stage):
    roots = [R]
    paper = Path(st["paper_dir"])
    if not stage.get("write") and paper.resolve() != Path(".").resolve():
        roots.append(paper)
    out = {}
    for base in roots:
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            rel = p.as_posix()
            if not p.is_file() or "/.git/" in f"/{rel}" or rel in GUARD_SKIP_FILES or rel.startswith(GUARD_SKIP_DIRS):
                continue
            s = p.stat()
            if s.st_size <= 20_000_000:
                out[rel] = hashlib.sha1(p.read_bytes()).hexdigest()
            else:
                out[rel] = f"{s.st_size}:{s.st_mtime_ns}"
    return out


def guard_start(st):
    stage = STAGES[IDX[st["stage"]]]
    if guarded(st) and stage["owner"] == "codex":
        GUARD.write_text(json.dumps({"stage": stage["id"], "files": guard_scan(st, stage)}), encoding="utf-8")


def guard_changes(st, stage):
    if not GUARD.exists():
        return []
    snap = json.loads(GUARD.read_text(encoding="utf-8"))
    if snap.get("stage") != stage["id"]:
        return []
    old, new = snap["files"], guard_scan(st, stage)
    return sorted([f"改动 {k}" for k in old.keys() & new.keys() if old[k] != new[k]]
                  + [f"新增 {k}" for k in new.keys() - old.keys()] + [f"删除 {k}" for k in old.keys() - new.keys()])


CLAUDE_ROLE = ("你是 method-paper 流程单独调起的推理与审查进程，也就是 AGENTS.md 里称为 Codex 的推理与审查方，不是执行方。"
               "仓库 CLAUDE.md 里写给执行方 Claude Code 的分工、自动推进循环和流程命令都不适用于你；SKILL.md 和参考文档里“交给 "
               "Codex”的事指的就是你，直接自己完成。不运行 pipeline.py，不跑实验，不改实验结果和记录。按用户消息里的任务工作，"
               "最终回复的最后一行必须是任务要求的 PIPELINE 行。")
CLAUDE_ROLE_WRITE = "这一阶段你可以修改当前目录（论文目录）里的稿件，不改别处的文件。"


def claude_argv(stage, st):
    """A reasoning stage run by a fresh headless Claude process with only the tools the stage needs: read-only stages
    get Read/Grep/Glob (plus web search where the stage searches literature); writing stages run inside the paper
    folder in acceptEdits mode, which keeps edits to that folder (no blanket Edit/Write allow rule)."""
    argv = ["claude", "-p", "--output-format", "text", "--no-session-persistence", "--strict-mcp-config"]
    if st.get("claude_model"):
        argv += ["--model", st["claude_model"]]
    if st.get("claude_effort", "xhigh") != "default":
        argv += ["--effort", st.get("claude_effort", "xhigh")]
    role = CLAUDE_ROLE
    if stage.get("write"):
        argv += ["--permission-mode", "acceptEdits", "--tools", "Read,Grep,Glob,Edit,Write", "--allowedTools", "Read Grep Glob",
                 "--disallowedTools", "Bash NotebookEdit WebSearch WebFetch"]
        role += CLAUDE_ROLE_WRITE
        cwd = paper_abs(st)
    else:
        tools = ["Read", "Grep", "Glob"] + (["WebSearch", "WebFetch"] if stage.get("web") else [])
        argv += ["--tools", ",".join(tools), "--allowedTools", " ".join(tools), "--disallowedTools", "Bash Edit Write NotebookEdit"]
        cwd = root()
    return argv + ["--append-system-prompt", role], cwd


def write_prompt(stage, st):
    if stage["id"] == "review":
        evidence_precheck()
    pfile = R / "prompts" / f"{stage['id']}.md"
    pfile.parent.mkdir(parents=True, exist_ok=True)
    pfile.write_text(prompt_text(stage, st), encoding="utf-8")
    return pfile


def codex_command(stage, st):
    """The command the executor runs for a reasoning stage (codex exec, or this script's `reason` for Claude)."""
    sid = stage["id"]
    pfile = write_prompt(stage, st)
    out = run_file(st, sid)
    out.parent.mkdir(parents=True, exist_ok=True)
    if reasoner(st) == "claude":
        return f"{self_cmd()} reason"
    q = shlex.quote
    opts = ""
    if st.get("codex_model"):
        opts += f"-m {q(st['codex_model'])} "
    if st.get("codex_effort", "xhigh") != "default":
        opts += f"-c 'model_reasoning_effort=\"{st.get('codex_effort', 'xhigh')}\"' "
    nosandbox = "--dangerously-bypass-approvals-and-sandbox " if unsandboxed(st) else ""
    if stage.get("write"):
        # write access only inside the paper folder; absolute paths so any nesting of the paper folder works
        return (f"cd {q(str(paper_abs(st)))} && codex exec {opts}{nosandbox or '--sandbox workspace-write '}"
                f"-o {q(str(root() / out))} - < {q(str(root() / pfile))}")
    if stage.get("web"):
        opts += "-c 'web_search=\"live\"' "
    return f"codex exec {opts}{nosandbox}-o {q(str(out))} - < {q(str(pfile))}"


# ----------------------------------------------------------------------------- actions
def action(st):
    sid = st["stage"]
    stage = STAGES[IDX[sid]]
    a = {"stage": sid, "title": stage["title"], "owner": stage["owner"], "rounds": st["rounds"]}
    if st.get("stopped"):
        a.update(owner="user", stop=True, message=f"流程已暂停：{st['stopped']}。请用户决定下一步（goto 某阶段，或修好后 done）。")
        return a
    d = st["decisions"]
    if sid == "done":
        a["message"] = "全部阶段完成。"
        if polish_mode(st) and d.get("handback_skipped"):
            a["message"] += (f"收尾后没有重跑 ARIS 的审计（{d['handback_skipped']}）：投稿前在 ARIS 里补跑 /paper-claim-audit 和 "
                             "/citation-audit，否则它的提交检查会把审计判为过期。")
        return a
    if stage["owner"] == "codex":
        a["command"] = codex_command(stage, st)
        a["output"] = str(run_file(st, sid))
        a["saved_as"] = str(R / stage["out"]) + ("（追加）" if stage.get("append") else "")
        a["message"] = ("在仓库根目录后台运行上面的命令，等命令结束（输出文件出现）后执行 `pipeline.py done`。"
                        "不要改提示，不要把你自己对结果的解读加进去。命令失败或输出不合格时用 `pipeline.py retry` 重跑。")
        if reasoner(st) == "claude":
            argv, cwd = claude_argv(stage, st)
            a["reasoner"] = "claude"
            a["runs"] = f"cd {shlex.quote(str(cwd))} && {shlex.join(argv)} < {R / 'prompts' / (sid + '.md')}"
            a["message"] = ("在仓库根目录后台运行上面的命令：它单独调起一个 Claude 进程做这一阶段的推理与审查（runs 一行是实际的 "
                            "claude 命令，只给它这一阶段要用的工具"
                            + ("，只能改论文目录里的稿件" if stage.get("write") else "，只读") + "），进程结束后输出文件才出现，"
                            "再执行 `pipeline.py done`。不要改提示，不要把你自己对结果的解读加进去；输出不合格时用 `pipeline.py retry` "
                            "重跑。运行期间你不要改 research/" + ("" if stage.get("write") else " 和论文目录") + "，done 时会核对。")
        elif unsandboxed(st):
            a["message"] += ("（无沙箱模式：Codex 没有写保护，运行期间你不要改 research/ 和论文目录；done 时会核对这些文件，"
                             "被改动就不通过。）")
    elif stage["owner"] == "claude":
        a["task"] = fmt((CLAUDE_TASKS_POLISH if polish_mode(st) else {}).get(sid, CLAUDE_TASKS[sid]), st)
        a["message"] = "由 Claude Code 完成，完成后执行 `pipeline.py done`" + ("（无法完成时可 `done --skip`）" if stage.get("skippable") else "")
    elif stage["owner"] == "user":
        a["question"] = CHECKPOINTS[sid]
        if sid == "cp_candidates":
            a["options"], a["recommended"] = ",".join(d.get("candidate_options", [])), ",".join(d.get("candidate_recommend", []))
            a["details"] = str(R / "problem_cards.md")
        if sid == "cp_method":
            a["options"], a["recommended"] = ",".join(d.get("method_options", [])), d.get("method_recommend", "")
            a["details"] = str(R / "method_candidates.md") + ("，" + str(R / "screen.md") if (R / "screen.md").exists() else "")
            if d.get("screen_note"):
                a["screen"] = d["screen_note"]
        if sid == "cp_final":
            a["details"] = str(R / "review.md")
        a["message"] = "停下来把问题、选项和 Codex 的推荐（详见 details 文件）告诉用户，等用户回答后执行 `pipeline.py decide <回答>`。"
    return a


def start_stage(st, sid, note=""):
    st["stage"] = sid
    st["stage_started"] = dt.datetime.now().timestamp()
    st["runs_at_start"] = len(runs_rows()[0])
    st["attempts"] = 0
    if STAGES[IDX[sid]]["owner"] == "codex":
        st["codex_runs"][sid] = st["codex_runs"].get(sid, 0) + 1
    guard_start(st)
    log(st, sid, "start", note)


def advance(st, nxt, note=""):
    start_stage(st, nxt, note)
    # auto mode: take Codex's recommendation at the candidate and method checkpoints (final evaluation always asks)
    if st["autonomy"] == "auto" and nxt in ("cp_candidates", "cp_method"):
        rec = action(st).get("recommended")
        if rec:
            try:
                decide_value(st, rec, auto=True)
                log(st, nxt, "auto-decide", rec)
            except Invalid as e:
                log(st, nxt, "auto-decide-failed", str(e))


def install_output(st, stage):
    """Save the validated Codex run as the handoff file."""
    src = run_file(st, stage["id"])
    dst = R / stage["out"]
    if stage.get("append"):
        head = f"\n\n## {stage['title']} · 第 {st['codex_runs'][stage['id']]} 次 · {now()}\n\n"
        with dst.open("a", encoding="utf-8") as fh:
            fh.write(head + src.read_text(encoding="utf-8").strip() + "\n")
    else:
        shutil.copyfile(src, dst)


HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*)$")
BOLD_LABEL = re.compile(r"^\s*\*\*[^*]{1,20}\*\*\s*[:：]?\s*$")


def _outside_fences(lines):
    """(index, line, inside_code_fence) for each line, so '# comment' lines in code blocks are not taken as headings."""
    fence = False
    for i, l in enumerate(lines):
        if re.match(r"^\s*(```|~~~)", l):
            fence = not fence
            yield i, l, True
            continue
        yield i, l, fence


def code_location_section(txt):
    """Text of the "代码修改位置" section of the method card.

    A markdown heading wins over a bold label, which wins over a table row: a status table at the top of the card
    often names the section without containing it. A heading's section runs to the next heading of the same or a
    higher level, so its subsections count."""
    lines = txt.splitlines()
    marked = list(_outside_fences(lines))
    for i, l, fenced in marked:
        m = None if fenced else HEADING.match(l)
        if m and "代码修改位置" in m.group(2):
            level, out = len(m.group(1)), []
            for _, x, xf in marked[i + 1:]:
                h = None if xf else HEADING.match(x)
                if h and len(h.group(1)) <= level:
                    break
                out.append(x)
            return "\n".join(out)
    for i, l, fenced in marked:
        if fenced or "代码修改位置" not in l or l.lstrip().startswith("|"):
            continue
        if l.lstrip().startswith("**") or len(l.strip()) <= 16:
            out = []
            for _, x, xf in marked[i + 1:]:
                if not xf and (HEADING.match(x) or BOLD_LABEL.match(x)):
                    break
                out.append(x)
            return "\n".join(out)
    for _, l, fenced in marked:
        if not fenced and "代码修改位置" in l and l.lstrip().startswith("|"):
            return l  # the whole section written as one table row
    return None


PATH_RE = re.compile(r"[\w\-./]+\.(py|pyx|cu|cuh|cpp|cc|cxx|c|h|hpp|yaml|yml|json|toml|sh|m|jl|rs|ts|js)\b")


def validate(st, stage):
    """Return the parsed PIPELINE values for Codex stages; raise Invalid if the stage's outputs are not complete."""
    started = st.get("stage_started", 0)
    sid = stage["id"]
    if stage["owner"] == "codex":
        p = run_file(st, sid)
        if not p.exists() or p.stat().st_size == 0:
            raise Invalid(f"没有找到 {p}：等命令结束再 done；命令已经失败时用 pipeline.py retry 重跑")
        pl = parse_pipeline(last_line(p))
        if pl is None:
            raise Invalid(f"{p} 的最后一行不是 PIPELINE 行；用 pipeline.py retry 重跑本阶段（不要手工补写）")
        res = check_pipeline(stage, pl)
        if sid == "problems":
            check_numbering(st, res)
        if sid == "review" and st["decisions"].get("gate2_conditional") and "conditions" not in res:
            raise Invalid("本轮是机制探针，PIPELINE 行缺少 conditions=met/pending/unmet")
        if sid == "gate2" and res.get("gate2") == "conditional" and has_pilot(st):
            raise Invalid("本方法已有初筛证据，关口二不再判有条件通过：在 pass / revise / reposition / fail 中选一个，用 retry 重跑")
        chosen = gate1_chosen(st)
        if sid == "gate1" and res.get("gate1") == "pass" and chosen and res["candidate"] not in chosen:
            raise Invalid(f"candidate={res['candidate']} 不在本轮要判定的候选 {chosen} 中")
        if sid == "methods" and "options" in res:
            if not all(x.isdigit() for x in res["options"]) or len(set(res["options"])) != len(res["options"]):
                raise Invalid(f"候选编号要用不重复的整数：{res['options']}")
            low = first_option(st)
            old = [x for x in res["options"] if int(x) < low]
            legacy = "max_option" not in st["decisions"]  # state from a version that numbered every round 1, 2, 3
            if old and not legacy:
                raise Invalid(f"候选编号 {old} 和前几轮重复；本轮应从 {low} 开始编号")
        if guarded(st):
            ch = guard_changes(st, stage)
            if ch:
                who = "Claude 推理进程" if reasoner(st) == "claude" else "Codex"
                raise Invalid(f"{'无沙箱模式下，' if reasoner(st) != 'claude' else ''}{who}运行期间这些文件有变化："
                              + "；".join(ch[:8]) + (f" 等 {len(ch)} 处" if len(ch) > 8 else "") + f"。{who}只应写出它的回复文件"
                              + ("和论文目录里的稿件" if stage.get("write") else "") + f"：用 git diff 核对，是{who}改的就用 "
                              "git checkout 恢复（恢复后内容一致即可通过），再 done 或 retry")
        return res
    if stage.get("runs"):
        rows, header = runs_rows()
        new = rows[st.get("runs_at_start", 0):]
        if not new:
            raise Invalid("research/runs.md 没有新增运行记录")
        split_col = next((i for i, h in enumerate(header) if "划分" in h or "split" in h.lower()), None)
        if sid == "final_eval":
            if not any("最终报告" in r for r in new):
                raise Invalid("本阶段新增的运行记录里没有标注“最终报告”的行")
        file_col = next((i for i, h in enumerate(header) if "结果文件" in h or "result" in h.lower()), None)
        if file_col is not None:
            missing = []
            for r in new:
                c = cells(r)
                if len(c) <= file_col or "作废" in r or "不可恢复" in r:
                    continue
                for ref in re.split(r"[,;，；\s]+", c[file_col].strip("`")):
                    ref = ref.strip("`")
                    if not ref or ref in ("-", "—", "无"):
                        continue
                    if not any(list(base.glob(ref)) if any(ch in ref for ch in "*?[") else (base / ref).exists()
                               for base in (R, Path("."))):
                        missing.append(f"{c[0]}: {ref}")
            if missing:
                raise Invalid("这些运行记录引用的结果文件不存在：" + "；".join(missing[:5]) + "。结果文件是证据，丢失后"
                              "这次运行不能作为依据：补回文件，或在状态里写“证据不可恢复”")
        if sid in ("run", "screen") and split_col is not None:
            bad = [r for r in new if len(cells(r)) > split_col and TEST_SPLIT.search(cells(r)[split_col])
                   and "作废" not in r]
            if bad:
                raise Invalid(f"最终报告批准之前有 {len(bad)} 条运行用了测试集；调参和选择只能用验证集，"
                              "这些结果不能用来做决定：把这几行的状态改成“作废（误用测试集）”，改用验证集重跑")
        if sid == "screen":
            p = R / "screen.md"
            if not p.exists() or p.stat().st_mtime < started:
                raise Invalid("本阶段没有写 research/screen.md")
            rows = parse_screen()
            if not rows:
                raise Invalid("research/screen.md 里没有可读的结果行（表头要有“候选”“回收比例”“达到判据”）")
            opts = st["decisions"].get("method_options") or []
            bad = [r["cand"] for r in rows if opts and r["cand"] not in opts]
            if bad:
                raise Invalid(f"初筛里的候选 {bad} 不在本轮候选 {opts} 中")
        return {}
    if sid == "code_location":
        p = R / "method_card.md"
        if not p.exists():
            raise Invalid("没有 research/method_card.md")
        sec = code_location_section(p.read_text(encoding="utf-8"))
        if sec is None:
            raise Invalid("方法卡里找不到“代码修改位置”一节（应是一个标题）")
        d = st["decisions"]
        revised = d.get("gate2_last_verdict") == "revise" or d.get("card_revision")
        # after a revision Codex keeps this section; Claude only has to act when it asks for an update
        if p.stat().st_mtime < started and not (revised and "需实验方更新" not in sec):
            raise Invalid("research/method_card.md 在本阶段没有更新")
        if "需实验方更新" in sec:
            raise Invalid("“代码修改位置”一节末尾还有“需实验方更新”，按要求调整后删掉这句")
        if "待实验方填写" in sec:
            raise Invalid("“代码修改位置”一节还留着“待实验方填写”")
        if not PATH_RE.search(sec):
            raise Invalid("“代码修改位置”一节里没有带扩展名的文件路径")
        return {}
    if sid == "code_survey":
        p = R / "code_survey" / "lineage.md"
        if not p.exists() or p.stat().st_mtime < started:
            raise Invalid("本阶段没有生成 research/code_survey/lineage.md")
        return {}
    if sid == "profile":
        if polish_mode(st):
            p = R / "writing_profile" / "stats.json"
            if not p.exists() or p.stat().st_mtime < started:
                raise Invalid("本阶段没有生成 research/writing_profile/stats.json")
            return {}
        pool = R / "refs_pool.md"
        prof = [p for p in (R / "writing_profile" / "venue_norms.json", R / "writing_profile" / "stats.json") if p.exists()]
        if not pool.exists():
            raise Invalid("没有文献池 research/refs_pool.md：用 lit_pool.py 建；Crossref 和 arXiv 都连不上时用 done --skip --note <原因>")
        if not prof:
            raise Invalid("research/writing_profile/ 下没有 venue_norms.json（lit_pool.py norms）或 stats.json（写作画像），至少要有一个")
        if all(p.stat().st_mtime < started for p in [pool] + prof):
            raise Invalid("文献池和写作画像都不是本阶段更新的；确实不需要更新时用 done --skip --note <原因>")
        return {}
    if sid == "figures":
        paper = Path(st["paper_dir"])
        dirs = {d.resolve(): d for d in (paper / "figs", paper / "figures", Path("figures"), R / "figs") if d.is_dir()}
        figs = [p for d in dirs.values() for p in d.rglob("*")
                if p.suffix.lower() in (".pdf", ".png", ".svg") and p.stat().st_mtime >= started]
        if not figs:
            raise Invalid("没有在本阶段生成新的图（论文目录的 figs/ 或 figures/、项目根目录的 figures/、research/figs/）")
        return {}
    if sid == "lint":
        p = R / "lint_report.md"
        if not p.exists() or p.stat().st_mtime < started:
            raise Invalid("本阶段没有生成 research/lint_report.md")
        return {}
    if sid == "handback":
        stale = [n for n in AUDITS if not (Path(st["paper_dir"]) / n).exists()
                 or (Path(st["paper_dir"]) / n).stat().st_mtime < started]
        if stale:
            raise Invalid(f"论文目录里的 {'、'.join(stale)} 不是本阶段重新生成的：收尾之后要重跑 ARIS 的对应审计；"
                          "确实跑不了时用 done --skip --note <原因>")
        return {}
    return {}


def high_count():
    p = R / "lint_report.md"
    if not p.exists():
        return 0
    return len(re.findall(r"^\|\s*高\s*\|", p.read_text(encoding="utf-8"), re.M))


def parse_screen():
    """Rows of research/screen.md: candidate id, recovered fraction of the headroom, and the pre-registered verdict."""
    p = R / "screen.md"
    if not p.exists():
        return []
    rows = [l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip().startswith("|")]
    if not rows:
        return []
    head = cells(rows[0])
    ic = next((i for i, h in enumerate(head) if "候选" in h and "结果" not in h), 0)
    ir = next((i for i, h in enumerate(head) if "回收" in h), None)
    io = next((i for i, h in enumerate(head) if "判据" in h), None)
    out = []
    for r in rows[1:]:
        if re.match(r"^\|[\s\-:|]+\|$", r):
            continue
        c = cells(r)
        m = re.search(r"\d+", c[ic]) if len(c) > ic else None
        if not m:
            continue
        ratio = None
        if ir is not None and len(c) > ir:
            rm = re.search(r"-?\d+(?:\.\d+)?", c[ir])
            if rm:
                ratio = float(rm.group(0)) / (100 if "%" in c[ir] else 1)
        v = c[io] if io is not None and len(c) > io else ""
        ok = ("对照无效" if "对照无效" in v else "接近" if "接近" in v
              else "是" if "是" in v and "否" not in v else "否")
        out.append({"cand": m.group(0), "ratio": ratio, "ok": ok})
    return out


def record_finding(st, stage, verdict, reason, src=None):
    """Append a negative result to research/findings.md, the ledger every later Codex stage reads before proposing."""
    p = R / "findings.md"
    if not p.exists():
        p.write_text("# 否定记录（只追加）\n\n已被证据否定的方向。找问题、找方法、写方法卡和判关口时先读这里，新方案不要落回这些做法；"
                     "细节见每条后面的文件里“以后不要再试”一节。\n\n", encoding="utf-8")
    src = src or str(run_file(st, stage)) if STAGES[IDX[stage]]["owner"] == "codex" else (src or "")
    with p.open("a", encoding="utf-8") as fh:
        fh.write(f"- {now()[:10]} · {stage} · {verdict} · {reason}" + (f" · 详见 {src}" if src else "") + "\n")


def route_after(st, sid, pl):
    """Decide the next stage after a validated stage. Returns (next_stage or None, note)."""
    d, lim, rnd = st["decisions"], st["limits"], st["rounds"]
    if sid == "problems":
        d.pop("seed", None)
        d["candidate_options"], d["candidate_recommend"] = pl["candidates"], pl["recommend"]
        d["max_candidate"] = max([d.get("max_candidate", 0)] + [int(x[1:]) for x in pl["candidates"]])
        return "cp_candidates", ""
    if sid == "gate1":
        recheck = is_recheck(d)
        if pl["gate1"] == "pass":
            known = bool(anchor_entry(d, pl["candidate"]))
            switch_anchor(st, pl["candidate"], {"gate1": str(run_file(st, "gate1"))} if known else
                          {"card": str(run_file(st, "problems")), "gate1": str(run_file(st, "gate1"))})
            d.pop("headroom_doubt", None)
            rnd["problem"] = 0
            if has_cs(st):  # the anchor's code survey is already there (a re-check, or an anchor taken up again)
                return "methods", "复核确认了锚点的提升空间，沿用已有的代码构成报告" if recheck else "沿用这个锚点已有的代码构成报告"
            return "code_survey", ""
        anchor = d.get("passed_candidate")
        judged = gate1_chosen(st)
        if anchor and anchor in judged:  # the current anchor was judged again and failed: dropping it is the author's call
            others = [x for x in judged if x != anchor]
            d.setdefault("tried_candidates", []).extend(x for x in others if x not in d["tried_candidates"])
            record_finding(st, "gate1", "关口一复核没有确认提升空间", f"锚点 {anchor}：{d.get('headroom_doubt') or '重新判定未通过'}")
            return None, (f"关口一复核没有确认锚点 {anchor} 的提升空间（见 {run_file(st, 'gate1')}）。是否放弃由作者决定：放弃就用 "
                          "goto problems --reason '<依据>'；换一个评测设置重新量用 goto diagnose --reason '<设置>'；仍要在这个锚点上"
                          f"出候选用 goto methods{other_anchors(d)}")
        d.setdefault("tried_candidates", []).extend(x for x in judged if x not in d["tried_candidates"])
        record_finding(st, "gate1", "关口一未通过", f"候选 {','.join(judged) or '—'} 的证据或提升空间不足，或已被处理")
        rnd["problem"] += 1
        if rnd["problem"] >= lim["problem_rounds"]:
            return None, f"关口一已连续 {rnd['problem']} 轮未通过"
        return "problems", "关口一未通过，换候选重新找"
    if sid == "methods":
        d.pop("gate2_reason", None)  # the previous rejection was handed to this round; later rounds must not repeat it
        if pl.get("back") == "problems":  # a pending refinement or strengthening survives, so goto methods resumes it
            why = pl.get("reason") or "见候选方法文档"
            record_finding(st, "methods", "Codex 认为锚点的提升空间被否定", why)
            return None, (f"Codex 认为锚点 {d.get('passed_candidate') or ''} 的提升空间被实验否定：{why}。是否放弃这个锚点由作者"
                          "决定：放弃就用 goto problems --reason '<依据>'；先复核提升空间用 goto diagnose --reason '<疑点>'；"
                          f"不放弃、继续出候选用 goto methods{other_anchors(d)}")
        if not d.pop("refine", None) and not d.get("reposition"):
            d["refine_rounds"] = 0  # a fresh set of candidates gets its own refinement budget
        d["method_options"], d["method_recommend"] = pl["options"], pl["recommend"]
        d["max_option"] = max([d.get("max_option", 0)] + [int(x) for x in pl["options"] if x.isdigit()])
        d.pop("screen_note", None)
        return "screen", ""
    if sid == "screen":
        d.pop("stack", None)  # written by earlier versions; the stack is now worked out from screen_positive
        if d.pop("screen_skipped", None):
            d["screen_positive"], d["screened"] = [], False
            return "cp_method", "初筛跳过，按 Codex 的推荐选方法"
        d["screened"] = True
        rows = parse_screen()
        passed = sorted((r for r in rows if r["ok"] == "是"), key=lambda r: -(r["ratio"] if r["ratio"] is not None else -1))
        near = [r["cand"] for r in rows if r["ok"] == "接近"]
        d["screen_positive"] = [r["cand"] for r in passed]
        anchor = d.get("passed_candidate")
        if passed:
            d["method_recommend"] = passed[0]["cand"]
            d["refine_rounds"] = 0
            d["screen_note"] = "初筛达到判据：" + "、".join(f"{r['cand']}（回收 {r['ratio']:.0%}）" if r["ratio"] is not None
                                                       else r["cand"] for r in passed)
            return "cp_method", d["screen_note"]
        if rows and all(r["ok"] == "对照无效" for r in rows):
            cf = d.setdefault("control_failures", {})
            cf[anchor or "-"] = cf.get(anchor or "-", 0) + 1
            why = "初筛里阳性对照全部不成立：不足在代理设置里没有复现，需要重新核对提升空间"
            record_finding(st, "screen", "初筛阳性对照不成立", why, src="research/screen.md")
            if cf[anchor or "-"] >= 2 or not anchor:
                return None, (f"锚点 {anchor or ''} 的初筛已经 {cf[anchor or '-']} 次阳性对照全部不成立：不足在初筛的代理设置里复现"
                              "不出来，这不算方法失败。下一步由作者决定：换一个能复现不足的初筛设置（先 set --screen-setting "
                              "'<设置>'，再 goto methods）、复核提升空间（goto diagnose --reason '<疑点>'）、放弃锚点（goto problems "
                              "--reason '<依据>'）"
                              + other_anchors(d))
            d["headroom_doubt"] = why
            return "gate1", "初筛没有复现不足，回到关口一重新核对提升空间"
        if near and d.get("refine_rounds", 0) < lim.get("refine_rounds", 2):
            d["refine_rounds"] = d.get("refine_rounds", 0) + 1
            d["refine"] = near
            return "methods", f"初筛有接近判据的候选（{'、'.join(near)}），细化一轮（第 {d['refine_rounds']} 轮）"
        d["refine_rounds"] = 0
        if d.get("reposition"):  # only the strengthening failed: the base method that gate two found effective still stands
            rp, name = d["reposition"], base_text(d)[1]
            d["screen_positive"] = list(rp.get("positive", []))  # the base keeps its own pilot evidence
            d["screened"] = rp.get("screened", d["screened"])
            record_finding(st, "screen", "补强候选都没有达到判据", f"主体{name}保留，加强方向未奏效", src="research/screen.md")
            return None, (f"补强轮的候选都没有达到判据（见 research/screen.md），主体{name}本身仍然有效。下一步由作者决定：再出一轮"
                          "补强候选（goto methods）；不再补强、按主体现有的差距推进（goto plan --gate2-passed '<依据>'）；换方法"
                          "（goto methods --reason '<依据>'）")
        why = "初筛全部未达到事先写的判据（见 research/screen.md）"
        d["gate2_reason"] = why
        record_finding(st, "screen", "初筛全部未达到判据", why, src="research/screen.md")
        msg = use_cycle(st, "screen")
        if msg:
            return None, msg
        return "methods", "初筛全部未达标，换算子或插入点出下一轮候选"
    if sid == "method_card":
        d.pop("reposition", None)  # the strengthened method is one card now
        return "code_location", ""
    if sid == "gate2":
        v = pl["gate2"]
        d["gate2_last_verdict"], d["gate2_last_file"] = v, str(run_file(st, "gate2"))
        for k in ("card_revision", "gate2_revise", "gate2_conditional"):
            d.pop(k, None)
        rnd.setdefault("gate2_revise", 0)
        if v in ("pass", "conditional"):
            d["gate2_passed"] = True
            d.pop("gate2_reason", None)
            clear_round_flags(d)
            reset_cycles(rnd)
            rnd["gate2_revise"] = 0
            rnd["reposition"] = 0
            if v == "conditional":
                d["gate2_conditional"] = True
                return "plan", "关口二有条件通过：先做最小验证实验检验机制预测"
            return "plan", ""
        d["gate2_passed"] = False
        if v == "reposition":
            rnd["reposition"] = rnd.get("reposition", 0) + 1
            if rnd["reposition"] > lim.get("reposition_rounds", 2):
                return None, f"关口二已判补强 {lim.get('reposition_rounds', 2)} 次，请作者决定是否接受当前方法或换方法"
            card = R / "method_card.md"
            base = R / f"method_card_base-{st['codex_runs'].get('gate2', 1)}.md"
            if card.exists():  # the next method_card stage overwrites method_card.md; keep the judged card as the base
                shutil.copyfile(card, base)
            d.pop("refine", None)
            d["refine_rounds"] = 0
            d["reposition"] = {"method": d.get("method"), "reason": pl.get("reason") or "见 gates.md 最近一次关口二判定",
                               "card": str(base if card.exists() else run_file(st, "method_card")),
                               "gate2": str(run_file(st, "gate2")),
                               "positive": list(d.get("screen_positive", [])), "screened": d.get("screened")}
            log(st, "gate2", "reposition", d["reposition"]["reason"])
            return "methods", "关口二判补强：保留方法，出加强方向的候选"
        if v == "revise":
            rnd["gate2_revise"] += 1
            if rnd["gate2_revise"] > lim.get("gate2_revise_rounds", 3):
                return None, f"方法卡已按关口二修订 {lim.get('gate2_revise_rounds', 3)} 轮仍有缺项"
            d["gate2_revise"] = True
            return "method_card", "关口二要求修订方法卡（方法保留），修订后复判"
        d["gate2_reason"] = pl.get("reason") or "见 gates.md 最近一次关口二判定"
        record_finding(st, "gate2", "关口二判为换方法", d["gate2_reason"])
        clear_round_flags(d)
        rnd["gate2_revise"] = 0
        msg = use_cycle(st, "gate2")
        if msg:
            return None, msg
        return "methods", "关口二判为换方法，带着原因重新找方法"
    if sid == "plan":
        d.pop("plan_note", None)
        d.pop("review_more", None)
        return "run", ""
    if sid == "review":
        v = pl["review"]
        if d.get("gate2_conditional"):  # this round tested the mechanism prediction with a minimal probe
            c = pl.get("conditions")
            if c == "met":
                d.pop("gate2_conditional", None)
                d["gate2_confirmed"] = True
                rnd["review"] = 0
                return "plan", "最小验证实验支持机制预测，写完整实验职责表"
            if c == "unmet" or v == "fail":
                why = pl.get("reason") or "见 review.md"
                if pl.get("back") == "problems":  # controls failed too: the anchor's headroom is in doubt, the author decides
                    # gate2_conditional stays, so a retry of this review is still the probe review; any goto clears it
                    record_finding(st, "review", "机制探针认为提升空间站不住", why)
                    return None, (f"机制探针认为锚点 {d.get('passed_candidate') or ''} 的提升空间站不住：{why}。是否放弃锚点由作者"
                                  "决定：放弃就用 goto problems --reason '<依据>'；先复核提升空间用 goto diagnose --reason '<疑点>'；"
                                  f"继续换方法用 goto methods --reason '<依据>'{other_anchors(d)}")
                d.pop("gate2_conditional", None)
                d["gate2_passed"] = False
                d["gate2_reason"] = "最小验证实验没有支持机制预测：" + why
                record_finding(st, "review", "机制预测落空，换方法", why)
                msg = use_cycle(st, "probe")
                if msg:
                    return None, msg
                return "methods", "机制预测落空，带着探针结果重新找方法"
        if v == "pass":
            rnd["review"] = 0
            return "cp_final", ""
        if v == "more":
            rnd["review"] += 1
            if rnd["review"] > lim["review_rounds"]:
                if d.get("gate2_conditional"):
                    return None, (f"机制探针已补 {lim['review_rounds']} 轮仍判 pending。收尾后用 goto review 让 Codex 给出"
                                  "最终结论（met 或 unmet），不再补性能实验")
                return None, f"已补实验 {lim['review_rounds']} 轮，审查仍要求补充"
            d["review_more"] = True
            if pl.get("card") == "revise":
                d["card_revision"] = True
                return "method_card", "审查要求修订方法卡，之后重新过关口二"
            return "plan", "按审查意见把补充实验写进职责表"
        record_finding(st, "review", "核心 claim 不成立", pl.get("reason") or "见 review.md")
        return None, f"结果审查认为核心 claim 不成立：{pl.get('reason') or '见 review.md'}"
    if sid == "profile" and polish_mode(st):
        return "figures", ""
    if sid == "outline" and pl.get("outline") == "weak":
        why = pl.get("reason") or "见 research/outline.md 第 1、2 项"
        return None, (f"提纲认为现有结果撑不起本文的主张：{why}。组织表达解决不了这个问题，下一步由作者决定：补实验用 "
                      "`goto plan --reason <要补什么>`；换主张或换主结果用 `goto outline --reason <新的主张>`；确认照此写用 "
                      "`goto figures`")
    if sid == "figures" and polish_mode(st):
        return "lint", "先跑检查脚本，收尾改稿时一并处理"
    if sid in ("draft", "polish"):
        if sid == "polish":
            d["polished"] = True
        return "lint", ""
    if sid == "lint":
        h = high_count()
        if polish_mode(st) and not d.get("polished"):
            return "polish", "Codex 按发布会原则收尾，一并处理检查报告"
        if h == 0:
            return ("handback", "") if polish_mode(st) else ("done", "")
        if rnd["revise"] >= lim["revise_rounds"]:
            return None, f"已改稿 {rnd['revise']} 轮，检查报告里仍有 {h} 处高级别问题"
        rnd["revise"] += 1
        return "revise", f"检查报告里有 {h} 处高级别问题"
    if sid == "revise":
        return "lint", "改稿后重新检查"
    return STAGES[IDX[sid] + 1]["id"], ""


def decide_value(st, value, auto=False):
    """Apply a checkpoint answer. Raises Invalid (without changing the state) when the answer does not fit."""
    sid = st["stage"]
    d = st["decisions"]
    v = value.strip()
    if v.lower() == "stop":
        st["stopped"] = "用户选择暂停"
        return
    if sid == "cp_candidates":
        chosen = [x.strip().upper() for x in re.split(r"[,，\s]+", v) if x.strip()]
        opts = d.get("candidate_options", [])
        bad = [x for x in chosen if opts and x not in opts]
        if not chosen or bad:
            raise Invalid(f"候选 {bad or v} 不在本轮选项 {opts} 中")
        d["candidates"] = chosen
        d.pop("diagnose_skipped", None)
        d.pop("headroom_doubt", None)  # new candidates are judged afresh, not as a re-check
        advance(st, "diagnose", "auto" if auto else "")
    elif sid == "cp_method":
        if v.lower().startswith("back"):
            d["method_feedback"] = v[4:].strip(" ：:，,")
            d["rejected_methods"] = str(R / "codex" / f"methods-{st['codex_runs'].get('methods', 1)}.md")
            advance(st, "methods", "用户要求重新找方法")
            return
        opts = d.get("method_options", [])
        if opts and v.upper() not in opts:
            raise Invalid(f"方法 {v} 不在本轮选项 {opts} 中（或回复 back 加理由、stop）")
        d["method"] = v.upper()
        d.pop("method_feedback", None)
        for k in ("gate2_last_verdict", "gate2_revise", "card_revision", "review_more"):  # these belong to the previous method
            d.pop(k, None)
        st["rounds"]["review"] = 0
        advance(st, "method_card", "auto" if auto else "")
    elif sid == "cp_final":
        if v.lower() in ("yes", "y", "是", "批准", "同意"):
            d["final_eval"] = True
            advance(st, "final_eval")
        elif v.lower() in ("no", "n", "否", "不", "不批准"):
            st["stopped"] = "用户暂不批准最终评测"
        else:
            raise Invalid("请回答 yes 或 no")
    else:
        raise Invalid(f"当前阶段 {sid} 不是用户检查点")


# ----------------------------------------------------------------------------- commands
def cmd_init(a):
    if STATE.exists() and not a.force:
        sys.exit("research/pipeline.json 已存在；要重新开始用 init --force")
    start = a.start or ("profile" if a.polish else "problems")
    paper = Path(a.paper_dir)
    if a.polish:
        if start not in POLISH_START:
            sys.exit(f"收尾模式只能从 {', '.join(POLISH_START)} 开始")
        if not (paper / a.main_tex).exists():
            sys.exit(f"找不到 {paper / a.main_tex}：收尾模式改的是 ARIS 已经写好的稿件，先跑完 ARIS 的 /paper-writing，"
                     "或用 --paper-dir / --main-tex 指向稿件")
    elif start in ("polish", "handback"):
        sys.exit(f"{start} 只用于收尾 ARIS 写的稿件：用 init --polish")
    elif IDX[start] > IDX["gate2"] and not a.gate2_passed:
        sys.exit(f"从 {start} 开始会跳过关口二；关口二已经通过时用 --gate2-passed '<依据>' 说明（会记进流程记录），"
                 "否则从 gate2 或更早的阶段开始。")
    for sub in (("prompts", "codex") if a.polish else ("results", "prompts", "codex")):
        (R / sub).mkdir(parents=True, exist_ok=True)
    if not a.polish and not (R / "runs.md").exists():
        (R / "runs.md").write_text("# 运行记录（只追加）\n\n| 运行编号 | 职责表编号 | 代码提交号 | 配置 | 命令 | 数据划分 | 随机种子 | 结果文件 | 状态 |\n"
                                    "|---|---|---|---|---|---|---|---|---|\n", encoding="utf-8")
    if not a.polish and not (R / "deviations.md").exists():
        (R / "deviations.md").write_text("# 偏差记录\n\n| 日期 | 偏离了什么 | 原因 | 可能影响的结论 | 审查结论 |\n|---|---|---|---|---|\n",
                                          encoding="utf-8")
    if not a.polish and not (R / "gates.md").exists():
        (R / "gates.md").write_text("# 三道关口判定记录\n", encoding="utf-8")
    st = {"version": 2, "mode": "polish" if a.polish else "research", "autonomy": a.autonomy,
          "paper_dir": a.paper_dir, "main_tex": a.main_tex,
          "codex_effort": a.codex_effort, "codex_model": a.codex_model, "codex_sandbox": a.codex_sandbox,
          "reasoner": a.reasoner, "claude_model": a.claude_model, "claude_effort": a.claude_effort,
          "screen": {"n": a.screen_n, "per": a.screen_per, "total": a.screen_total},
          "limits": {"problem_rounds": 3, "method_rounds": 3, "gate2_revise_rounds": 3, "review_rounds": 3,
                     "revise_rounds": 2, "attempts": 2, "refine_rounds": 2, "reposition_rounds": 2},
          "rounds": {"problem": 0, "method": 0, "gate2_revise": 0, "review": 0, "revise": 0, "reposition": 0},
          "decisions": {}, "history": [],
          "codex_runs": {}, "stopped": None}
    if a.gate2_passed and not a.polish:
        st["decisions"].update(gate2_assumed=a.gate2_passed, gate2_passed=True)
        log(st, "gate2", "assumed-pass", a.gate2_passed)
    start_stage(st, start, "init" + ("（收尾模式：研究和初稿由 ARIS 完成）" if a.polish else ""))
    save(st)
    if not (Path(".git").exists() or any((p / ".git").exists() for p in Path.cwd().resolve().parents)):
        print("提示：这里不是 git 仓库。流程靠 git 回看每个阶段的改动，codex exec 也默认要求在 git 仓库里运行，先 git init。")
    if a.reasoner == "claude" and not shutil.which("claude"):
        print("提示：推理方设为 claude，但找不到 claude 命令；装好 Claude Code 再继续。")
    if a.polish:
        if not (paper / "PAPER_IMPROVEMENT_LOG.md").exists():
            print("提示：论文目录里没有 PAPER_IMPROVEMENT_LOG.md，ARIS 的改进循环（paper-writing 第 5 阶段）可能还没跑。先跑完它再收尾，"
                  "否则它之后会按自己的规则把收尾改动改回去。")
        print("提示：先 git commit 当前稿件，收尾的每处改动之后都能用 git diff 逐处查看。")
    print(f"已初始化 research/（{'收尾模式，' if a.polish else ''}自主程度：{a.autonomy}，起始阶段：{start}）。"
          f"下一步：{self_cmd()} next")


def cmd_next(a):
    st = load()
    act = action(st)
    if getattr(a, "json", False):
        print(json.dumps(act, ensure_ascii=False, indent=1))
        return
    r = st["rounds"]
    if polish_mode(st):
        print(f"阶段：{act['stage']}（{act['title']}）  负责：{act['owner']}  收尾模式，改稿轮次 {r['revise']}")
    else:
        print(f"阶段：{act['stage']}（{act['title']}）  负责：{act['owner']}  "
              f"未通过轮次：关口一 {r['problem']}、方法周期 {r['method']}、方法卡修订 {r.get('gate2_revise', 0)}、"
              f"补强 {r.get('reposition', 0)}、补实验 {r['review']}、改稿 {r['revise']}")
    for k in ("question", "options", "recommended", "screen", "details", "task", "command", "runs", "output", "saved_as"):
        if act.get(k):
            print(f"{k}: {act[k]}")
    print(act.get("message", ""))


def cmd_done(a):
    st = load()
    sid = st["stage"]
    stage = STAGES[IDX[sid]]
    if sid == "done":
        print("已经完成。")
        return
    if stage["owner"] == "user":
        sys.exit("这是用户检查点，用 decide 回答")
    pl = {}
    if a.skip:
        if stage.get("skip_with_note"):
            if not a.note:
                sys.exit(f"跳过 {sid} 需要用 --note 写明原因")
            if sid == "diagnose":
                st["decisions"]["diagnose_skipped"] = a.note
        elif not stage.get("skippable"):
            sys.exit(f"{sid} 不能跳过")
    else:
        try:
            pl = validate(st, stage)
        except Invalid as e:
            st["attempts"] = st.get("attempts", 0) + 1
            log(st, sid, "invalid", str(e))
            if st["attempts"] >= st["limits"]["attempts"]:
                st["stopped"] = f"{sid} 两次未通过校验：{e}"
            save(st)
            sys.exit(f"校验未通过：{e}")
        if stage["owner"] == "codex":
            install_output(st, stage)
        if sid == "diagnose":  # a diagnosis that was actually run replaces an earlier skip
            st["decisions"].pop("diagnose_skipped", None)
    if sid == "code_survey":
        if a.skip:
            old = R / "code_survey" / "lineage.md"
            if old.exists():  # an earlier round's report must not be read as this round's
                old.rename(old.with_name(f"lineage-{st['decisions'].get('code_survey_for') or 'old'}-{int(dt.datetime.now().timestamp())}.md"))
            st["decisions"].pop("code_survey_for", None)
        else:
            st["decisions"]["code_survey_for"] = st["decisions"].get("passed_candidate")
    if a.skip and sid == "screen":
        st["decisions"]["screen_skipped"] = True
    if sid == "handback":
        if a.skip:
            st["decisions"]["handback_skipped"] = a.note or "已跳过"
        else:
            st["decisions"].pop("handback_skipped", None)
    note = (a.note or "") + (f"（执行方：{a.by}）" if a.by else "")
    log(st, sid, "skip" if a.skip else "done", note)
    st["stopped"] = None  # a validated stage clears an earlier pause
    nxt, why = route_after(st, sid, pl)
    if nxt is None:
        st["stopped"] = why
        log(st, sid, "stop", why)
    else:
        advance(st, nxt, why)
    save(st)
    cmd_next(argparse.Namespace(json=a.json))


def cmd_reason(a):
    """reasoner=claude: run the current reasoning stage with a separate headless Claude process."""
    st = load()
    sid = st["stage"]
    stage = STAGES[IDX[sid]]
    if st.get("stopped"):
        sys.exit(f"流程已暂停：{st['stopped']}")
    if stage["owner"] != "codex":
        sys.exit(f"当前阶段 {sid} 不是推理与审查阶段，用 next 看该做什么")
    if reasoner(st) != "claude":
        sys.exit("推理方是 Codex：运行 next 给出的 codex exec 命令；要改用 Claude，先 set --reasoner claude")
    if not shutil.which("claude"):
        sys.exit("找不到 claude 命令：确认 Claude Code 已安装、在 PATH 上")
    pfile = write_prompt(stage, st)
    out = run_file(st, sid)
    out.parent.mkdir(parents=True, exist_ok=True)
    part, logf = out.with_name(out.name + ".part"), out.with_suffix(".log")
    argv, cwd = claude_argv(stage, st)
    with open(pfile, "rb") as fin, open(part, "wb") as fout, open(logf, "w", encoding="utf-8") as ferr:
        ferr.write(f"# cwd: {cwd}\n# {shlex.join(argv)}\n")
        ferr.flush()
        rc = subprocess.run(argv, cwd=cwd, stdin=fin, stdout=fout, stderr=ferr).returncode
    part.replace(out)  # the output file appears only once the run has finished
    if rc != 0 and "unknown option" in logf.read_text(encoding="utf-8", errors="replace"):
        print(f"提示：这个 Claude Code 版本不认识某个参数（见 {logf}），先运行 claude update 再 retry")
    print(f"Claude 推理进程已结束（返回码 {rc}），输出在 {out}；接着运行 done")
    if rc != 0:
        sys.exit(rc)


def cmd_retry(a):
    st = load()
    sid = st["stage"]
    if STAGES[IDX[sid]]["owner"] != "codex":
        sys.exit(f"{sid} 不是 Codex 阶段；Claude 阶段修好后直接 done")
    st["codex_runs"][sid] = st["codex_runs"].get(sid, 0) + 1
    st["attempts"] = 0
    st["stopped"] = None
    guard_start(st)
    log(st, sid, "retry", a.note or "")
    save(st)
    cmd_next(argparse.Namespace(json=False))


def cmd_decide(a):
    st = load()
    sid = st["stage"]
    if STAGES[IDX[sid]]["owner"] != "user":
        sys.exit(f"当前阶段 {sid} 不是用户检查点")
    st["stopped"] = None
    try:
        decide_value(st, a.value)
    except Invalid as e:
        sys.exit(f"没有记录这个回答：{e}")
    log(st, sid, "decision", a.value)
    save(st)
    cmd_next(argparse.Namespace(json=False))


def cmd_fail(a):
    st = load()
    st["attempts"] = st.get("attempts", 0) + 1
    log(st, st["stage"], "fail", a.note or "")
    if st["attempts"] >= st["limits"]["attempts"]:
        st["stopped"] = f"{st['stage']} 失败 {st['attempts']} 次：{a.note or ''}"
    save(st)
    cmd_next(argparse.Namespace(json=False))


def cmd_goto(a):
    st = load()
    if a.stage not in IDX:
        sys.exit(f"未知阶段 {a.stage}；可选：{', '.join(IDX)}")
    if polish_mode(st):
        if a.stage not in POLISH_STAGES:
            sys.exit(f"收尾模式只有这些阶段：{', '.join(POLISH_STAGES)}；研究阶段的问题回到 ARIS 处理")
    elif a.stage in ("polish", "handback"):
        sys.exit(f"{a.stage} 只用于收尾 ARIS 写的稿件（init --polish）；本流程写完初稿后用 goto draft 或 goto lint")
    elif IDX[a.stage] > IDX["gate2"] and not st["decisions"].get("gate2_passed") and not a.gate2_passed:
        sys.exit(f"跳到 {a.stage} 会越过关口二；关口二已经通过时加 --gate2-passed '<依据>'。")
    if a.gate2_passed:
        st["decisions"].update(gate2_assumed=a.gate2_passed, gate2_passed=True)
        log(st, "gate2", "assumed-pass", a.gate2_passed)
    d = st["decisions"]
    if a.seed:
        if a.stage != "problems":
            sys.exit("--seed 只用于 goto problems：把作者指定的机会交给下一轮候选")
        d["seed"] = a.seed
    if a.anchor:
        if a.stage != "methods":
            sys.exit("--anchor 只用于 goto methods：换到另一个已通过关口一的锚点继续出候选")
        aid = a.anchor.strip().upper()
        ids = [x["id"] for x in d.get("anchors", [])]
        if aid not in ids:
            sys.exit(f"{aid} 不是已通过关口一的锚点（现有：{'、'.join(ids) or '无'}）")
    switched = False
    if not polish_mode(st):
        target = IDX[a.stage]
        if target < IDX["method_card"]:  # back before the card: the card's revision state and gate-two result no longer apply
            for k in ("gate2_conditional", "gate2_revise", "card_revision", "gate2_last_verdict"):
                d.pop(k, None)
            if not a.gate2_passed:
                d["gate2_passed"] = False
        if target < IDX["methods"] or target > IDX["gate2"] or (a.reason and a.stage in ("methods", "cp_method")):
            clear_round_flags(d)  # refinement and strengthening belong to one method search
        if a.stage not in ("diagnose", "gate1"):
            d.pop("headroom_doubt", None)
        switched = bool(a.anchor) and a.anchor.strip().upper() != d.get("passed_candidate")
        if a.anchor:
            switch_anchor(st, a.anchor.strip().upper())
        elif a.stage == "methods" and not d.get("passed_candidate") and d.get("anchors"):
            switch_anchor(st, d["anchors"][-1]["id"])  # the current anchor was dropped; carry on with the latest remaining one
            switched = True
    writing = not polish_mode(st) and IDX[a.stage] >= IDX["profile"]
    if writing:   # a reason given with a writing stage is the author's instruction for the outline, the draft and the revision
        if a.reason:
            d["writing_note"] = a.reason
    else:
        d.pop("writing_note", None)
    if not polish_mode(st):
        if a.stage == "plan" and a.reason:   # e.g. after an outline judged weak: which experiments to add
            d["plan_note"] = a.reason
        else:
            d.pop("plan_note", None)
    if a.reason and not polish_mode(st) and not writing:  # carry the user's conclusion into the next Codex stage and the ledger
        if a.stage in ("problems", "cp_candidates"):  # the author drops the anchor
            cand = d.pop("passed_candidate", None)
            if cand and cand not in d.setdefault("tried_candidates", []):
                d["tried_candidates"].append(cand)
            d["anchors"] = [x for x in d.get("anchors", []) if x["id"] != cand]
            d["mechanism_rejected"] = f"{cand or '上一个失效机制'}：{a.reason}"
            d["method_failures"] = 0
        elif a.stage in ("diagnose", "gate1"):  # keep the mechanism, re-check its evidence and headroom
            d["headroom_doubt"] = a.reason
        elif a.stage in ("methods", "cp_method") and not switched:  # a reason given with a new anchor is about the old one
            d["gate2_reason"] = a.reason
            d["method_failures"] = d.get("method_failures", 0) + 1
        if not (a.stage == "plan" and IDX[st["stage"]] >= IDX["profile"]):  # experiments asked for while writing are not a negative result
            record_finding(st, st["stage"], f"用户决定跳到 {a.stage}", a.reason, src="research/review.md")
    st["stopped"] = None
    st["rounds"] = {k: 0 for k in st["rounds"]}  # a user decision starts the failure counts afresh
    advance(st, a.stage, "用户指定" + (f"：{a.reason}" if a.reason else ""))
    save(st)
    cmd_next(argparse.Namespace(json=False))


def cmd_set(a):
    st = load()
    changed = []
    for k in ("codex_effort", "codex_model", "autonomy", "codex_sandbox", "reasoner", "claude_model", "claude_effort"):
        v = getattr(a, k)
        if v is not None:
            st[k] = None if v == "" else v
            changed.append(f"{k}={v}")
    if a.codex_sandbox == "none" or a.reasoner == "claude":
        guard_start(st)  # the current reasoning stage, if any, is checked from now on
    for k in ("n", "per", "total", "setting"):
        v = getattr(a, f"screen_{k}")
        if v is not None:
            st.setdefault("screen", {"n": 4, "per": 2, "total": 8})[k] = v if v != "" else None
            changed.append(f"screen_{k}={v}")
    if a.screen_setting is not None:
        st["decisions"].pop("control_failures", None)  # a new screening setting gets a fresh chance to reproduce the deficiency
    if not changed:
        sys.exit("没有要改的设置（--reasoner / --claude-model / --claude-effort / --codex-effort / --codex-model / --autonomy / "
                 "--codex-sandbox / --screen-n / --screen-per / --screen-total / --screen-setting）")
    log(st, st["stage"], "set", " ".join(changed))
    save(st)
    print("已更新：" + "，".join(changed))


def cmd_status(a):
    st = load()
    if polish_mode(st):
        print(f"当前阶段：{st['stage']}  收尾模式（研究和初稿由 ARIS 完成）  改稿轮次：{st['rounds']['revise']}")
    else:
        print(f"当前阶段：{st['stage']}  自主程度：{st['autonomy']}  未通过轮次：{st['rounds']}")
    if reasoner(st) == "claude":
        print(f"推理与审查：单独调起的 Claude 进程（模型 {st.get('claude_model') or 'Claude Code 的默认设置'}，"
              f"强度 {st.get('claude_effort', 'xhigh')}）")
    else:
        print(f"推理与审查：Codex（模型 {st.get('codex_model') or 'Codex 的默认设置'}，强度 {st.get('codex_effort', 'xhigh')}）")
    if st.get("stopped"):
        print(f"已暂停：{st['stopped']}")
    print(f"决定：{json.dumps(st['decisions'], ensure_ascii=False)}")
    order = POLISH_STAGES if polish_mode(st) else [s["id"] for s in STAGES if s["id"] not in ("polish", "handback")]
    print("阶段顺序：" + " → ".join(f"[{s}]" if s == st["stage"] else s for s in order))
    for h in st["history"][-15:]:
        print(f"  {h['time']}  {h['stage']:<14} {h['event']:<18} {h.get('note', '')}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("--autonomy", choices=["checkpoints", "auto"], default="checkpoints")
    p.add_argument("--paper-dir", default="paper")
    p.add_argument("--codex-effort", default="xhigh", choices=["low", "medium", "high", "xhigh", "default"],
                   help="Codex reasoning effort for every Codex stage (default xhigh; 'default' leaves Codex's own setting)")
    p.add_argument("--codex-model", help="Codex model passed with -m (default: Codex's configured model)")
    p.add_argument("--codex-sandbox", choices=["default", "none"], default="default",
                   help="none: run Codex with --dangerously-bypass-approvals-and-sandbox (containers where its sandbox "
                        "cannot start); each Codex stage then checks that research/ was not changed")
    p.add_argument("--reasoner", choices=["codex", "claude"], default=None,
                   help="who does the reasoning and review stages (default codex, or $METHOD_PAPER_REASONER): codex exec, "
                        "or a separate headless Claude process (claude -p) started by `pipeline.py reason`")
    p.add_argument("--claude-model", help="model for the Claude reasoner, e.g. opus or a full model name (default: Claude Code's)")
    p.add_argument("--claude-effort", default="xhigh", choices=["low", "medium", "high", "xhigh", "max", "default"],
                   help="effort for the Claude reasoner (default xhigh; 'default' leaves Claude Code's own setting)")
    p.add_argument("--screen-n", type=int, default=4, help="candidates screened in parallel after each methods round")
    p.add_argument("--screen-per", type=float, default=2, help="GPU hours per screened candidate")
    p.add_argument("--screen-total", type=float, default=8, help="GPU hours for one screening round")
    p.add_argument("--main-tex", default="main.tex")
    p.add_argument("--polish", action="store_true",
                   help="polish mode: ARIS did the research and the draft; only finish the paper (references/aris.md)")
    p.add_argument("--start", choices=[s["id"] for s in STAGES if s["owner"] != "none"],
                   help="first stage (default problems; profile with --polish)")
    p.add_argument("--gate2-passed", help="required when starting after gate2: why gate two is already passed")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("next"); p.add_argument("--json", action="store_true")
    p = sub.add_parser("done"); p.add_argument("--skip", action="store_true"); p.add_argument("--note")
    p.add_argument("--by", help="who did a Codex stage when codex was unavailable, e.g. subagent")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("retry"); p.add_argument("--note")
    p = sub.add_parser("decide"); p.add_argument("value")
    p = sub.add_parser("fail"); p.add_argument("--note")
    p = sub.add_parser("goto"); p.add_argument("stage"); p.add_argument("--gate2-passed")
    p.add_argument("--reason", help="why (e.g. the probe's verdict); passed to the next Codex stage and logged in findings.md. "
                                    "With a writing stage (profile and later) it is the author's instruction for the outline, "
                                    "the draft and the revision")
    p.add_argument("--seed", help="with goto problems: an opportunity the author wants written up and evaluated in the next round")
    p.add_argument("--anchor", help="with goto methods: continue on another anchor (an opportunity that passed gate one), e.g. C12")
    sub.add_parser("reason", help="reasoner=claude: run the current reasoning stage with a separate headless Claude process")
    p = sub.add_parser("set", help="change settings without restarting the pipeline")
    p.add_argument("--reasoner", choices=["codex", "claude"])
    p.add_argument("--claude-model", help="'' to go back to Claude Code's default model")
    p.add_argument("--claude-effort", choices=["low", "medium", "high", "xhigh", "max", "default"])
    p.add_argument("--codex-effort", choices=["low", "medium", "high", "xhigh", "default"])
    p.add_argument("--codex-model", help="'' to go back to Codex's configured model")
    p.add_argument("--codex-sandbox", choices=["default", "none"])
    p.add_argument("--autonomy", choices=["checkpoints", "auto"])
    p.add_argument("--screen-n", type=int); p.add_argument("--screen-per", type=float)
    p.add_argument("--screen-total", type=float)
    p.add_argument("--screen-setting", help="where every screen runs (a subset or condition that shows the deficiency); '' clears it")
    sub.add_parser("status")
    a = ap.parse_args(argv)
    if a.cmd == "init" and a.reasoner is None:
        a.reasoner = os.environ.get("METHOD_PAPER_REASONER", "codex")
        if a.reasoner not in ("codex", "claude"):
            sys.exit(f"METHOD_PAPER_REASONER={a.reasoner} 不对，只能是 codex 或 claude")
    {"init": cmd_init, "next": cmd_next, "done": cmd_done, "retry": cmd_retry, "decide": cmd_decide, "reason": cmd_reason,
     "fail": cmd_fail, "goto": cmd_goto, "set": cmd_set, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
