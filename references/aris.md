# 和 ARIS 配合：ARIS 做研究和初稿，method-paper 收尾

ARIS（wanshuiyin/Auto-claude-code-research-in-sleep）覆盖从找 idea 到出 PDF 的全流程。它和 method-paper 都带状态机和审查循环，不能同时管同一个项目的研究阶段。配合方式固定为：研究、实验和初稿交给 ARIS；method-paper 只在最后按发布会原则和用户的文风规则收尾；收尾后把稿子交回 ARIS 重跑审计。

## 目录
1. 安装方式：分开装或合并装
2. 分工
3. 顺序和命令
4. 收尾不能动的东西
5. 两边规则不一致时
6. 不要混用的部分
7. 调用方式和 MCP
8. 把进行中的 method-paper 项目交给 ARIS

## 1. 安装方式：分开装或合并装

| | 分开装：method-paper skill + 官方 aris 插件 | 合并装：method-paper 插件（内置 ARIS） |
|---|---|---|
| ARIS 的命令名 | `/aris:<技能>` | `/method-paper:<技能>` |
| 更新 ARIS | `claude plugin update aris@aris`，跟着上游走 | 自己运行插件里的 `scripts/sync_aris.py`，等于维护一份分叉 |
| ARIS 的版本 | 安装时的版本，不 update 就不变 | 固定在上次同步的提交 |
| 技能描述的固定开销（约 1.35 万 token） | 只在启用了 ARIS 的项目里有 | 插件启用的地方都有 |
| 重叠请求的分流 | 不需要 ARIS 的项目不启用它；两个都启用的项目，在 CLAUDE.md 里写一句分工 | ARIS 的 23 个重叠技能的描述前面带一句路由说明 |
| 一次性设置 | `/aris:setup` | `/method-paper:setup` |

两种装法下，ARIS 的审查都要 MCP，method-paper 都不要。下文写技能的短名，按装法补上前缀；用 ARIS 自己的安装脚本装的不带前缀。

**分开装的步骤**

1. method-paper：把 method-paper 文件夹放到 `~/.claude/skills/method-paper`（所有项目可用）。
2. 添加 ARIS 的插件市场：`claude plugin marketplace add wanshuiyin/Auto-claude-code-research-in-sleep`。
3. 按项目装 ARIS：在要用 ARIS 的项目根目录运行 `claude plugin install aris@aris --scope local`（只对你自己生效，写进 `.claude/settings.local.json`；要和合作者共用就换成 `--scope project`）。想在所有项目里都能用就换成 `--scope user`，然后在不需要它的项目里运行 `claude plugin disable aris@aris --scope local`。
4. 在一个启用了 ARIS 的会话里运行一次 `/aris:setup`，然后重启 Claude Code。它把审查用的 `codex` MCP 服务注册在用户级，所有会话都会加载这个服务。只想让它出现在 ARIS 项目里，就改成按项目注册：先 `claude mcp remove codex -s user`，再在 ARIS 项目根目录运行 `claude mcp add codex -s local -- python3 "$(cat ~/.aris/repo)/mcp-servers/codex-exec/server.py"`。
5. 两个都启用的项目，在项目的 CLAUDE.md 末尾追加一句分工（追加，不要覆盖 ARIS 写的内容）：
   - method-paper 做研究的项目：`本项目的研究流程、写作和改稿用 method-paper。ARIS 的技能只在用户点名时用，或者用来做 method-paper 没有的事：数字和引用审计、编译、文献检索工具等。`（assets/CLAUDE.md 模板里已经有这一句）
   - ARIS 做研究的项目：`本项目的研究、实验和初稿由 ARIS 负责。初稿写完后的收尾和改稿用 method-paper 的收尾模式，收尾之后不再跑 ARIS 的 auto-paper-improvement-loop；用户说改稿、润色时也用 method-paper。`

**合并装**见 method-paper 插件根目录的 README.md：一个插件里同时有两套技能，装完运行一次 `/method-paper:setup`，用 `scripts/sync_aris.py` 更新 ARIS。

## 2. 分工

| 阶段 | 谁负责 | 用什么 |
|---|---|---|
| 找 idea、小规模试验、查新 | ARIS | `idea-discovery`（含 pilot 和新颖性检查）或 `research-pipeline` |
| 实现、跑实验、审查循环 | ARIS | `experiment-bridge`、`auto-review-loop`、`result-to-claim` |
| 初稿 | ARIS | `paper-writing` 第 1–5 阶段（计划、出图、LaTeX、编译、改进循环） |
| 收尾 | method-paper | `pipeline.py init --polish`：写作画像 → 图的检查和重画 → 检查脚本 → Codex 按发布会原则收尾 → 检查 ⇄ 改稿 |
| 收尾后复审 | ARIS | `paper-claim-audit`、`citation-audit`、`paper-compile`；投稿前加 `integrity-forensics` 和 `verify_paper_audits.sh` |

method-paper 在这个组合里只做 ARIS 没有的部分：发布会原则的叙事审读和最小改动改稿；用户的正文文风规则（不用疑问句、整段叙述、不出现文件名、参考文献按引用顺序、不混写改动前后的数据）；按目标会议语料校准的写作规律；插入后图表的实际字号和遮挡检查；按版心重画数据图；色块分区、底部写机理的算法框架图。

## 3. 顺序和命令

1. ARIS 跑到初稿：`paper-writing "NARRATIVE_REPORT.md" — venue: CVPR`（分开装时输入 `/aris:paper-writing ...`）。默认的 assurance 是 draft，跑完改进循环和它自己的审计就结束。
2. 收尾前 `git commit` 一次，之后每处改动都能用 `git diff` 查看。在项目根目录运行：
   ```bash
   python ~/.claude/skills/method-paper/scripts/pipeline.py init --polish   # 合并装时是 method-paper-pipeline init --polish
                                                                          # 稿件不在 paper/main.tex 时加 --paper-dir / --main-tex
   ```
   然后对 Claude Code 说"开始收尾"，它按 `next` / `done` 循环推进：profile（网络不通可跳过）→ figures（图都合格可跳过）→ lint → polish（Codex 在论文目录可写运行）→ lint，高级别问题还在就 revise ⇄ lint（最多 2 轮）→ handback。
3. handback：在同一个项目里依次运行 ARIS 的 `paper-claim-audit "paper/"`、`citation-audit "paper/"`（有定理，或者标题、摘要里有普适性的说法时加 `kill-argument "paper/"`），最后 `paper-compile "paper/"`。投稿前按 ARIS paper-writing 第 5.9、6.0 节跑 `integrity-forensics` 和 `verify_paper_audits.sh paper/ --assurance submission`。

复审必须放在收尾之后。ARIS 的审计记录（`PAPER_CLAIM_AUDIT.json`、`CITATION_AUDIT.json` 等）存着被审文件的 SHA256，提交检查会重新计算哈希，收尾改过的 `.tex` 都会被判为过期；诚信审计的 `fresh` 检查也要求它之后没有稿件文件再改动。所以 handback 阶段只认收尾之后重新生成的这两个审计文件；确实跑不了（没装 ARIS、审查用的 MCP 没配好）时用 `done --skip --note <原因>`，流程结束时会提醒投稿前补跑。

想少跑一轮审计时（`citation-audit` 要逐条联网核对，比较慢），可以用 `paper-writing ... — auto proceed: false`，在第 5 阶段（改进循环）结束的检查点停下，做完收尾再让 ARIS 从第 5.5 阶段继续。这样审计只跑一次，但两套流程要在同一个会话里接力，会话中途被压缩时容易接错，所以默认用上面的顺序。

收尾之后不要再跑 ARIS 的 `auto-paper-improvement-loop`：它按自己的写作规则和审稿意见改稿，会把收尾改动改回去。还要再改时回到收尾模式（`pipeline.py goto polish`）。

## 4. 收尾不能动的东西

ARIS 的审计以下面这些为准，收尾只改表达。pipeline.py 生成的 polish 和 revise 提示里都带着这几条：

- 数字和实验设置。
- claim 的范围：ARIS 的改进循环和 claim 审计收窄过的说法不能再放宽。发布会原则管的是怎么讲，不是把收窄的主张讲回去。
- 引用：不改 `\cite` 的键和 `.bib`；觉得引用不当的列入"需作者决策"，交给 `citation-audit`。
- 表格里的结果行：不利结果留在表里，正文只调整叙述重心，不把它当比赛项目。删结果按 writing.md 第 2 节的诚信边界交作者决定，而且会被 ARIS 的诚信审计查出来。
- 图里的数据：重画只改版式（字号、版心宽度、图例位置），文件名不变；`paper/figures/` 是项目 `figures/` 的副本而不是链接时，两处都替换。

事实依据依次是项目根目录的 `NARRATIVE_REPORT.md`、`PAPER_PLAN.md`、`PAPER_ACCEPTANCE_CONTRACT.md`，以及论文目录下的 `PAPER_IMPROVEMENT_LOG.md` 和 `PAPER_CLAIM_AUDIT.md`。

## 5. 两边规则不一致时

ARIS 的写作原则里也有一条"发布会"式的原则（Launch Mindset：先讲最强的贡献和关键证据，限制集中写，正文不放防御性声明），方向和发布会原则一致。分歧集中在下面几处：

| 事项 | ARIS 的做法 | 收尾时怎么处理 |
|---|---|---|
| 贡献点 | 引言写 2–4 条贡献点 | 保留（paper_lint 的列表规则对引言贡献点不报）；正文其他地方的条目式列表改成整段 |
| 限制 | 集中写一节，2–4 条实质限制 | 会议要求的就保留这一节；散落在正文和结论里的自我削弱移进这一节或删掉，结论不加新的自我否定 |
| 不利结果 | 留在表里，有依据时解释为权衡，否则中性陈述 | 一致；收尾只改叙述重心 |
| 参考文献顺序 | 按会议模板 | 用户要求数字制按引用顺序时，改 `\bibliographystyle` 属于导言区改动，可以做；handback 照常重跑 citation-audit 和 compile |
| 框架图 | `figure-spec` 等生成的结构图 | 不符合色块分区、底部机理说明、不放结果时，用 framework_figure.py 重画，文件名不变 |
| 风格参考 | `— style-ref:` 只借参考论文的结构 | 收尾以发布会原则和用户的文风规则为准 |
| 三道关口 | 由 `idea-discovery` 的新颖性检查、`auto-review-loop`、`result-to-claim` 负责 | 收尾不重判、不拦流程；用户要时按 F 模式给一张三关判定表，只作参考 |

## 6. 不要混用的部分

- 两套技能的分工：用户点名 ARIS，或者在 ARIS 的工作流里，用 ARIS；没点名的找问题、查新、关口、方法卡、实验设计与执行、写作改稿、出图、回应审稿，由 method-paper 处理；method-paper 没有的能力直接用 ARIS。分开装时这条分工靠第 1 节的按项目启用和 CLAUDE.md 那一句；合并装时，ARIS 的 23 个重叠技能的描述前面有一句以"[ARIS]"开头的路由说明（`sync_aris.py` 同步时自动加，`--no-routing` 可以不加）。
- 两种装法只选一种：用 method-paper 插件时，不要再装官方 aris 插件，也不要再留单独装的 method-paper（`~/.claude/skills/method-paper`）；分开装时反过来。否则同样的技能会出现两份，两个 setup 还会争同一个 `codex` 注册和 `~/.aris/repo`。
- 不在 ARIS 项目里跑 method-paper 研究阶段的流程（problems 到 review）。两套状态机会各自给出关口和审查结论，互相覆盖。收尾模式的 `goto` 只能在收尾阶段之间跳。
- 不把 method-paper 的 `assets/CLAUDE.md`、`assets/AGENTS.md` 拷进 ARIS 项目。ARIS 的项目配置也写在 CLAUDE.md 里，覆盖会让它丢失配置；收尾模式的提示自带全部规则，不依赖这两个文件，CLAUDE.md 里只追加第 1 节那一句分工。`assets/claude-settings.json` 里的 `effortLevel` 和允许列表可以合并进项目已有的 `.claude/settings.json`，不要整个覆盖。
- 收尾记录写在 `research/`（`pipeline.json`、`polish_notes.md`、`lint_report.md`、`revision_notes.md`、`codex/`）。ARIS 不用这个目录，两边文件互不干扰。

## 7. 调用方式和 MCP

- ARIS 在 Claude Code 里的审查和审计调用全部走 MCP。codex-cli 0.154 去掉了 `codex mcp-server`，ARIS 改用自带的 codex-exec 桥，它是本地进程，底层仍是 `codex exec`。分开装运行一次 `/aris:setup`，合并装运行一次 `/method-paper:setup`，就会注册它；用 ARIS 安装脚本装的手动注册：`claude mcp add codex -s user -- python3 "<ARIS 克隆目录>/mcp-servers/codex-exec/server.py"`。之后重启 Claude Code。没注册时 ARIS 的审查会判 REVIEW_UNAVAILABLE。从 GitHub 装的插件每个版本放在不同的目录，旧目录 14 天后清理，所以更新 ARIS 之后先删掉 `~/.aris/repo`，再跑一次 setup：setup 会把 codex 服务改指到新目录，但 `~/.aris/repo` 只在缺失或指向的目录不存在时才重写。
- method-paper 的流程和收尾都直接调用 `codex exec`（推理方设为 claude 时是 `claude -p`），不需要 MCP；思考强度默认 xhigh，用 `pipeline.py set --codex-effort` 或 `--claude-effort` 修改。
- 容器里 Codex 的沙箱起不来（报 `bwrap: No permissions to create a new namespace`）时，ARIS 的审查也读不到文件。根治是在宿主机或容器启动参数上放开权限；用户同意让容器充当唯一的沙箱时，把 ARIS 的桥接指向 method-paper 的包装脚本，它把每次调用的沙箱设置改成 `danger-full-access`（`codex exec` 不弹审批，效果等同于 `--dangerously-bypass-approvals-and-sandbox`；续接对话的 `exec resume` 不收 `--sandbox`，所以改的是 `-c sandbox_mode=`）：
  ```bash
  claude mcp remove codex -s user
  claude mcp add codex -s user -e CODEX_BIN=<method-paper 目录>/scripts/codex_nosandbox.sh -- python3 "<ARIS 目录>/mcp-servers/codex-exec/server.py"
  ```
  重启 Claude Code 生效。method-paper 自己的流程用 `pipeline.py set --codex-sandbox none`（collaboration.md 第 7 节）。权限放开以后，按同样的方式重新注册、去掉 `-e CODEX_BIN=...`，恢复 Codex 自己的沙箱。
- ARIS 的默认审查模型是 gpt-6-astra，审计类调用用 xhigh 或 ultra。两边的 Codex 调用互不影响。
- **ARIS 的审稿也换成 Claude。** ARIS 支持 `claude-code` 审稿后端，走它自带的 claude-review 桥（底层是本机的 `claude -p`）。先注册一次，`CLAUDE_REVIEW_MODEL` 可以省略：
  ```bash
  claude mcp add claude-review -s user -e CLAUDE_REVIEW_MODEL=<模型> -- python3 "<ARIS 目录>/mcp-servers/claude-review/server.py"
  ```
  再在项目里写 `.aris/reviewer.json`：`{"backend": "claude-code"}`，或者单次调用时加 `— reviewer: claude-code`。ARIS 按模型家族记录审查的独立性：执行方和审稿方都是 Claude 时记为 same-family，结论只算 provisional（暂定），claims、citations、proof、attack 这四类语义审计只有跨家族的审查才能记为 accepted。最终报告照样生成，总体保证级别显示 provisional。method-paper 自己的流程只用 Claude 时见 collaboration.md 第 8 节。

## 8. 把进行中的 method-paper 项目交给 ARIS

已经用 method-paper 跑了一段（例如关口一通过、机制探针有否定结果），想换 ARIS 接着做研究时：

1. 写一份 `RESEARCH_BRIEF.md`（ARIS 的 `templates/RESEARCH_BRIEF_TEMPLATE.md`），交给 `idea-discovery` 或 `research-pipeline`。核心内容控制在 1–2 页、4–8k 字符：ARIS 下游技能读的 query_pack.md 上限是 8000 字符，简报太长会挤掉失败记录，长材料只引用路径。各节的来源：

   | RESEARCH_BRIEF 的一节 | 从 research/ 的哪里来 |
   |---|---|
   | Problem Statement | problem_cards.md 里通过关口一的候选；gates.md 最近一次关口一判定（失效机制、失效代价、上限、冻结的最小效果） |
   | What I already tried / What didn't work | findings.md 每一条；review.md 里"以后不要再试"一节；screen.md 的初筛结果 |
   | Existing Results | runs.md 里有效的验证集运行（基线、失效代价、上限）；作废和证据不可恢复的运行不写 |
   | Constraints | CLAUDE.md 项目信息里的算力、目标会议、时间 |
   | Non-Goals | 已被否定的机制类别；调参、加指标、加数据集不算方法创新 |

2. 否定结果同时记进 ARIS 的 research wiki，让它重新找 idea 时避开。先 `research-wiki init`；每个被否定的方向记一条 idea，outcome 写 negative（`research_wiki.py upsert_idea research-wiki --title ... --stage archived --outcome negative --thesis <机制> --risks <否定依据>`），对应实验用 `add_experiment research-wiki --slug ... --idea ... --verdict no --provenance <runs.md 的运行编号>` 挂上。outcome 为 negative 和 mixed 的 idea 会进 ARIS 重新找 idea 时的禁用清单。
3. 迁移之后 method-paper 的研究流程不再推进。等 ARIS 写出初稿，先把 `research/pipeline.json` 改名留档（例如 `pipeline-research.json`），再 `init --polish` 开始收尾。
