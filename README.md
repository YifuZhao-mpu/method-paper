# method-paper

方法创新型论文的选题、方法设计、实验设计与写作改稿 skill。覆盖机器学习、计算机视觉、三维重建、LiDAR/毫米波雷达、机器人学习。

## 安装

要和 ARIS 一起用，有两种装法：分开装（本 skill 加官方 aris 插件，ARIS 可以只在需要的项目里启用），或者装内置 ARIS 的 method-paper 插件。对比和步骤见 `references/aris.md` 第 1 节。下面是单独安装这个 skill 的方法。

把整个 `method-paper` 文件夹放到下面任一位置（文件夹里必须直接有 SKILL.md）：

- Codex：`~/.agents/skills/method-paper/`（所有项目可用），或项目里的 `.agents/skills/method-paper/`
- Claude Code：`~/.claude/skills/method-paper/`，或项目里的 `.claude/skills/method-paper/`
- Claude 应用：上传本文件夹打包成的 zip（自动流程需要本机上的 Claude Code 和 Codex，在应用里不可用，其余模式可用）

脚本依赖：Python 3.8+、numpy、matplotlib 3.6+、Pillow；可选 PyMuPDF（`pip install pymupdf`），用于读取 PDF 图中的字号。图中字体优先装 Times New Roman 和 SimSun（TrueType）。

## 自动流程（推荐）

1. 论文仓库要是 git 仓库。把 `assets/AGENTS.md` 和 `assets/CLAUDE.md` 拷到仓库根目录，在 CLAUDE.md 顶部填好项目信息；把 `assets/claude-settings.json` 拷成 `.claude/settings.json`（Claude Code 思考强度 xhigh，并预先允许 codex 和脚本命令）。
2. 确认同一台机器上 `codex` 已安装并登录（`codex exec '回复 OK'` 能返回）。skill 只需装给 Claude Code，流程生成的提示会把 skill 目录的绝对路径告诉 Codex。
3. 在仓库根目录运行 `python <skill 目录>/scripts/pipeline.py init`。每条 Codex 命令默认带 xhigh 思考强度，当前 Codex 模型不支持时用 `pipeline.py set --codex-effort high`。
4. 打开 Claude Code，说"开始"。之后它按阶段自动推进：推理与审查交给 Codex，实验和出图自己做，在选候选、选方法、批准最终评测三处停下来问你；关口或审查连续不通过、某一步反复失败时也会停下来。
5. 随时可以说"现在到哪了"（`pipeline.py status`）、"跳到写稿"（`goto draft`）或"暂停"。Codex 每次的推理与审查原文都留在 `research/codex/` 里。

从旧版升级：替换整个 skill 文件夹；论文仓库里重新拷一份 `assets/AGENTS.md`，`CLAUDE.md` 保留顶部的项目信息、其余换成新模板。已有的 `research/pipeline.json` 可以接着用：旧记录里通过关口一的机会会自动登记为锚点，旧版每轮从 1 编号的候选方法下一轮起改为接着往后编。

和 ARIS 配合时，研究和初稿交给 ARIS，ARIS 的 `/paper-writing` 跑完后在项目根目录运行 `pipeline.py init --polish`，只做收尾（检查和重画图、Codex 按发布会原则最小改动、检查与改稿），最后交回 ARIS 重跑审计。这种用法不拷贝 AGENTS.md 和 CLAUDE.md，详见 `references/aris.md`。

## 单独使用

直接说需求即可，例如：

- "我做双臂 VLA，帮我从共同假设出发找几个别人没发现的问题"
- "这个 idea 过一下三道关口：……"
- "这个锚点卡在找方法，按算子出一轮候选，先初筛再写方法卡"
- "把这个模块写成方法卡，代码在 ./third_party/openpi"
- "按发布会原则检查这篇稿子的防御性写作，只做最小改动"（附 .tex / .docx）
- "全文通体检查，顺便查所有图表字号"
- "这几张曲线图重画，文字不要压线"（附原始 CSV）
- "按方法节画算法框架图，样例图用 data/samples 里的"

## 目录

- `SKILL.md`：红线、三道关口、模式路由
- `references/`：探索、方法卡、实验、写作改稿、图表规范
- `scripts/paper_lint.py`：文稿初筛（疑问句、自我削弱、过程叙述、改动前后混写、文件名、列表、引用顺序、摘要）
- `scripts/figure_table_check.py`：图表实际字号、缩放、Type 3 字体、表格字号统一
- `scripts/paper_plot.py`：按版心出图、字体与正文一致、图例避让、遮挡检查
- `scripts/framework_figure.py`：JSON 规格生成色块分区的算法框架图
- `assets/framework_example.json`：框架图规格示例
- `references/writing-patterns.md`：2021–2026 年顶会论文（987 篇核心语料、7256 篇全量、77 篇全文）的写作规律
- `scripts/pipeline.py`：自动流程（阶段、负责方、Codex 命令、校验、路由、检查点）
- `scripts/code_lineage.py`、`references/code-patterns.md`：拉取相关论文的开源代码，分析它基于哪些代码库、改了哪些文件、改了多少；以及对 300 多个顶会论文仓库统计出的拼接和改动规律
- `scripts/corpus/`：建语料并统计写作规律，可为自己的方向和目标会议重算
- `references/method-design.md`、`scripts/corpus/ops.py`、`assets/method_ops_stats.json`：方法设计的做法（四类机会、十二种设计算子、一轮候选、热启动初筛、细化与叠加），以及这些机会和算子在 6230 篇接收论文里的比例
- `scripts/funnel.py`：统计一个项目在各关口花了多少轮、初筛怎么结束、时间花在哪
- `references/aris.md`：和 ARIS 配合时的分工、顺序、冻结规则和规则冲突的处理
- `references/collaboration.md`、`assets/AGENTS.md`、`assets/CLAUDE.md`：Codex 负责方法推理与审查、Claude 负责完成任务的分工和交接规则；两个模板拷到论文仓库根目录即可，之后只和 Claude Code 对话，它会用 codex exec 调用 Codex
