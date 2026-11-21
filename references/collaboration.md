# 分工与自动流程：推理方负责方法推理与审查，Claude Code 负责完成任务

推理和执行分开做：推理方（默认 Codex，也可以是流程单独调起的 Claude 进程，见第 8 节；下文的 Codex 都指推理方）找问题、设计方法、设计实验、审查结果、写稿；Claude Code 改代码、跑实验、记录结果、出图，并推进整个流程。审查结果的一方没有亲手做实验，审查才是独立的。

用户只和 Claude Code 对话。流程由 `scripts/pipeline.py` 管理：它记住当前阶段，告诉 Claude Code 这一步由谁做、具体做什么；遇到推理或审查类阶段，Claude Code 用 `codex exec`（Codex 的非交互模式，不经过 MCP）交给 Codex，推理方是 Claude 时用 `pipeline.py reason` 交给一个单独的 Claude 进程。两个工具不共享对话，所有交接都通过论文仓库 `research/` 下的文件完成。

## 1. 分工

| 环节 | Codex | Claude Code |
|---|---|---|
| 找问题、查新（A） | 负责 | 跑分层诊断实验 |
| 看最接近工作的代码怎么拼、怎么改 | 读代码构成报告，判断它们的真实改动落在哪一环、本文要避开什么 | 用 code_lineage.py 拉取代码、生成报告，只陈述代码事实 |
| 关口审查（B） | 负责 | — |
| 方法卡（C） | 写七要素中除代码修改位置以外的部分，以及因果链、本质差异、一般性 | 读代码库后填写代码修改位置；实现中发现方法写不下去，记进偏差记录，不自行改方法 |
| 实验设计（D） | 写实验职责表 | 按表实现和运行；要加表外实验，先记进偏差记录 |
| 实验执行 | — | 负责：改代码、跑实验、记录每次运行、出图（G、H） |
| 结果审查 | 负责：核对结果是否支撑对应 claim，检查协议一致、测试集纪律、多种子统计 | 提供原始结果文件和复现命令 |
| 写作（E）、改稿（F）、回应信 | 负责：先写提纲，再写稿、改稿 | 建文献池、量范文和门槛、生成写作画像、出图、跑文稿和图表检查脚本、核对参考文献 |
| 推进流程 | — | 负责：运行 pipeline.py，执行每一步，在检查点停下问用户 |

## 2. 交接文件

都放在论文仓库的 `research/` 下，用 git 提交，提交信息写明阶段名。

| 文件 | 谁写 | 内容 |
|---|---|---|
| `pipeline.json` | pipeline.py | 当前阶段、轮次、用户的决定、历史记录 |
| `prompts/` | pipeline.py | 每个 Codex 阶段的完整提示 |
| `codex/` | Codex（经 `-o`） | 每次 Codex 运行的最终回复，一次一个文件（如 `gate1-2.md`），不覆盖，是全部推理与审查的原始记录 |
| `problem_cards.md` | Codex | 崩溃场景候选和问题卡 |
| `gates.md` | pipeline.py 汇总 | 三关判定记录：每次关口判定通过校验后，pipeline.py 把 Codex 的本次判定追加进来，Codex 不必重抄历史 |
| `method_candidates.md` | Codex | 从锚点出的一组候选方法（按排序列出，编号跨轮次接着编），每个附算子、插入点、先例、预计回收的提升空间和初筛方案 |
| `screen.md` | Claude Code | 候选初筛结果表：候选、指标、基线（同步训练过的）、候选结果、上限、回收比例、达到判据（是 / 接近 / 否 / 对照无效）、运行编号 |
| `method_card.md` | Codex 写主体，Claude Code 填代码修改位置 | 方法卡 |
| `experiment_plan.md` | Codex | 实验职责表；Claude Code 只改"状态"列 |
| `runs.md` | Claude Code | 每次运行一行：运行编号、对应职责表编号、代码提交号、配置、命令、数据划分、随机种子、结果文件、状态 |
| `results/` | Claude Code | 原始结果（csv、json），只追加，不覆盖、不手改 |
| `deviations.md` | Claude Code | 实现与方法卡或实验表不一致的地方、原因、对结论的影响；由 Codex 决定改方法卡还是改实现 |
| `review.md` | Codex | 结果审查：哪些 claim 成立、哪些证据不足、下一轮要做什么 |
| `code_survey/` | Claude Code | 最接近工作的代码构成报告（lineage.md、lineage.json）：基于哪些代码库、改了哪些文件、改了多少、新写了多少 |
| `writing_profile/` | Claude Code | 目标会议和子领域的写作画像（writing-patterns.md 第 12 节）；`venue_norms.json` 是目标期刊的篇幅和文献门槛，`exemplars/`、`exemplars.md` 是范文的篇幅和结构（第 13 节） |
| `refs_pool.json`、`refs_pool.md`、`refs_pool.bib` | Claude Code（lit_pool.py 生成） | 文献池：取自 Crossref 和 arXiv 的已核实条目，按目标期刊、技术路线分组；写稿只从这里和论文目录已核实的 .bib 引用 |
| `outline.md` | Codex | 论文提纲：故事和主结果、继承与贡献、引言六段、动机图、相关工作各小节、主张—证据对应表、篇幅与文献目标（writing.md 第 4.1 节） |
| `advisor_notes.md` | 作者 | 导师、合作者或审稿人的意见（可选）；提纲、初稿和改稿逐条落实，并列出每条的处理 |
| `refs_check.md` | Claude Code（lit_pool.py check） | 稿件 .bib 逐条核对的结果：不一致、查不到的条目 |
| `lint_report.md` | Claude Code | paper_lint（含结构与篇幅）、figure_table_check 和参考文献核对的输出 |
| `evidence_precheck.md` | pipeline.py | 每次结果审查前自动生成：每条运行的结果文件在不在、是否作废或证据不可恢复 |
| `findings.md` | pipeline.py 汇总 | 否定记录（只追加）：关口未通过、换方法、机制预测落空、用户跳转时的原因和出处；之后所有 Codex 阶段先读它，新方案不落回这些做法 |
| `draft_notes.md`、`revision_notes.md`、`polish_notes.md` | Codex | 写稿、改稿和收尾（ARIS 稿件）的修改摘要、需作者决策的问题 |

Codex 每次运行的回复写进 `codex/<阶段>-<次数>.md`，最后一行是机器可读的 `PIPELINE: ...` 行（例如 `PIPELINE: gate1=pass candidate=C2`）。pipeline.py 只读这个新文件的最后一行，校验取值后才把内容存成上表的交接文件（problem_cards.md、method_card.md 等被替换，gates.md 被追加），所以旧记录里的 PIPELINE 行不会被误读。

## 3. 规则

1. **各守其位。** Claude Code 不改方法设计和实验职责表的内容，不改写 Codex 的结论；Codex 不跑实验，不改 `results/` 和 `runs.md`。
2. **关口二判"通过"前不实现完整方法。** 在这之前 Claude Code 只做关口一的诊断实验；关口二判"有条件通过"时，只实现检验机制预测所需的最小版本，跑机制探针。
3. **测试集纪律。** 调参和模型选择只看验证集；测试集只在用户批准后的最终报告阶段评测一次，并在 `runs.md` 注明。
4. **数字可追溯。** 写进论文的每个数字都能追溯到 `runs.md` 的某一行和 `results/` 里的原始文件。审查时 Codex 逐个核对，追溯不到的数字不进正文。
5. **运行编号只用于内部。** `runs.md` 的编号、文件名、提交号都不进论文正文（红线 5）。
6. **偏差先记录再继续。** 为了跑通做的任何改动（换损失权重、改连接位置、加训练技巧），都先记进 `deviations.md`。这类改动未经 Codex 审查，不算方法的一部分。
7. **不带偏审查。** 调用 Codex 时只给文件路径和任务，不附上 Claude Code 自己对结果的解读。Claude Code 不同意 Codex 的结论时报告给用户，由用户决定是否 `retry` 重跑或 `goto` 到别的阶段；Claude Code 不手工改写或补写 Codex 的输出，包括 PIPELINE 行。
8. **测试集误用要作废。** 最终报告批准之前，`runs.md` 的数据划分一栏出现测试集时 `done` 不放行；把这些行的状态改成"作废（误用测试集）"，改用验证集重跑，作废的结果不参与任何决定。
9. **事实和判断分开。** 脚本和 Claude Code 只陈述能机械核对的事实：文件在不在、运行是否结束、数值是多少、哪些运行作废；"有效""机制成立""claim 被支持"这类判断只由 Codex 下。审计脚本输出科学结论，就是在替审查方下判断。
10. **审查只管真问题。** Codex 只报告真实存在、会影响结论的问题，不提议哈希绑定、兼容层、开关框架之类的防御性机制；审计脚本、测试隔离、归档这类工程问题列进"工程缺口"交给 Claude Code 修，只降低证据的可信度，不让科学结论停在待定。

## 4. 自动流程的阶段

| 阶段 | 负责 | 产出 | 之后 |
|---|---|---|---|
| problems 列机会候选 | Codex（联网） | problem_cards.md | 先写明基线和主评测，候选覆盖至少两类机会（条件崩溃、去掉特权输入、效率、统一简化、新能力）；已有锚点会列在提示里；`goto problems --seed '<机会>'` 可以指定一个必须评估的候选；之后用户选要验证的候选 |
| cp_candidates | 用户 | 选择 | — |
| diagnose 量证据与提升空间 | Claude | runs.md、results/ | 按机会类型在主评测上量：分层误差、特权输入与现实替代的差距、质量与代价曲线；只靠文献就能判定时可 `done --skip --note <原因>` |
| gate1 关口一 | Codex（联网） | gates.md | 列出最接近的 3–5 篇及其代码地址；通过 → 成为锚点，拉取代码（这个锚点已有代码构成报告时直接去 methods）；不通过 → 换新的候选重找，编号接着往后编（连续 3 轮不通过暂停）。复核轮（初筛对照无效或 `goto diagnose/gate1 --reason`）只判当前锚点：确认 → methods；没确认 → 暂停，锚点保留，由作者决定 |
| code_survey 最接近工作的代码构成 | Claude | code_survey/lineage.md | 之后 Codex 找方法、写方法卡、判关口二时都会读它；没有开源代码或网络不通可跳过 |
| methods 出一组候选方法 | Codex（联网） | method_candidates.md | 6–8 个候选，覆盖至少 3 种设计算子和 2 处插入点，按排序列出（method-design.md）；编号接着往后编；Codex 认为锚点的提升空间被实验否定时写 back=problems，流程暂停请作者决定 |
| screen 候选初筛 | Claude | screen.md、runs.md | 排在前面的 4 个并行小规模跑（热启动、基线同步训练作对照；默认每个 ≤ 2 GPU 小时、合计 ≤ 8，`set --screen-n/--screen-per/--screen-total` 可改，`set --screen-setting '<设置>'` 把初筛固定在能复现不足的设置里）；有"是" → 推荐回收比例最高的，另外的"是"可作支撑组件；只有"接近" → 细化一轮（每个周期最多两轮）；全部"否" → 一个周期用完，换算子出下一轮（一个锚点最多三个周期，之后暂停）；阳性对照全部不成立 → 回关口一复核提升空间（同一个锚点第二次时直接暂停）；补强轮的候选都没达标 → 暂停，不算用掉周期；没有 GPU 可跳过 |
| cp_method | 用户 | 选择 | 自动模式下直接采用初筛胜者 |
| method_card 方法卡 | Codex | method_card.md | — |
| code_location 代码修改位置 | Claude | method_card.md | — |
| gate2 关口二 | Codex | gates.md | 判的是初筛里有效的方法。通过 → 实验设计；修订 → 修订方法卡 → code_location → 复判（只核对缺项，最多 3 轮）；补强 → 被判的方法卡存为 method_card_base-N.md 作主体，回 methods 出"主体加一个加强"的候选 → 以主体为对照初筛 → 把加强并进主体重写方法卡 → 复判（最多 2 次）；换方法 → 带着原因出下一轮候选；没有初筛证据时还可以判有条件通过 → 机制探针（plan → run → review 只做最小验证实验） |
| plan 实验职责表 | Codex | experiment_plan.md | 审查要求补实验后也回到这里，由 Codex 把补充实验写成新行 |
| run 实现并运行 | Claude | runs.md、results/ | — |
| review 结果审查 | Codex | review.md | 机制探针轮：met → 写完整职责表；pending → 再补一轮探针（最多两轮，之后必须在 met 和 unmet 之间下结论）；unmet → back=methods 换方法，back=problems（阳性对照或上限也不成立）→ 暂停请作者决定是否放弃锚点。正常轮：补满 3 轮后的那一轮只能判 pass 或 fail（不支持的 claim 删掉或缩小后判 pass）；通过 → 请用户批准最终评测；要补且方法卡不变（`more card=keep`）→ plan → run；要补且方法卡要改（`more card=revise`）→ method_card 修订 → code_location → gate2 → plan → run；最多补 3 轮；核心 claim 不成立 → 暂停 |
| cp_final | 用户 | 批准 | 这一项总是要问 |
| final_eval 最终报告 | Claude | runs.md | — |
| profile 写作画像、范文门槛和文献池 | Claude | refs_pool.md、refs_pool.bib、writing_profile/ | 要有文献池，加上 venue_norms.json 或 stats.json 之一；Crossref 和 arXiv 都连不上时 `done --skip --note` |
| outline 论文提纲 | Codex（只读，可检索） | outline.md | ready → 出图；weak（现有结果撑不起主张）→ 暂停，由作者决定 `goto plan --reason <要补的实验>`、`goto outline --reason <新的主张>` 或 `goto figures` 照此写。收尾模式没有这个阶段 |
| figures 动机图、数据图、框架图和效果图 | Claude | 论文目录 figs/ | 可跳过；顺带把提纲“需补入文献池”的文献加进文献池 |
| draft 写初稿 | Codex（论文目录可写） | 稿件、draft_notes.md | 按提纲写；只引用文献池和已核实的条目 |
| polish 收尾改稿（只在收尾模式） | Codex（论文目录可写） | 稿件、polish_notes.md | 按发布会原则通读 ARIS 的稿件并最小改动，一并处理 lint 报告；数字、claim 范围、引用和结果行冻结；之后回到 lint |
| lint 文稿与图表检查 | Claude | lint_report.md、refs_check.md | 期刊稿的结构与篇幅不达标（引言、相关工作偏短，参考文献不到门槛，讨论过短）和核对不上的参考文献都算高级别问题。没有高级别问题 → 完成（收尾模式是 handback）；有 → 改稿（最多 2 轮，之后仍有就暂停）。收尾模式里第一次检查之后总是先交给 polish |
| revise 改稿 | Codex（论文目录可写） | 稿件、revision_notes.md | 最小改动；结构与篇幅的问题按提纲补写，文献从文献池补。总是回到 lint 重新检查 |
| handback 交回 ARIS 复审（只在收尾模式） | Claude | 论文目录的 ARIS 审计文件 | 重跑 `/paper-claim-audit`、`/citation-audit`、`/paper-compile`，校验两个审计文件是收尾之后生成的；跑不了时 `done --skip --note`，结束时会提醒投稿前补跑 |

自主程度：`init --autonomy checkpoints`（默认）在选候选、选方法、批准最终评测三处停下问用户；`--autonomy auto` 在前两处直接采用 Codex 的推荐，最终评测仍然要问。选方法时回答 `back 理由`，Codex 会拿到被否决的方案和理由重新找。另外，关口或审查连续多轮不通过、同一阶段两次校验失败时，流程自动暂停，等用户决定（`goto` 到某阶段，或修好后 `done`）；`goto diagnose --reason '<疑点>'` 是复核：保留当前锚点，重新量失效代价和上限，再由 Codex 重判关口一。`goto` 会把各项未通过轮次清零；加 `--reason '<结论>'` 时，这个结论会交给下一个 Codex 阶段并记进否定记录，例如 `goto methods --reason '机制探针：五项预测全部未得到支持'`；`goto problems --reason '<依据>'` 表示作者放弃当前锚点。`goto problems --seed '<机会>'` 把作者已经量过差距的机会交给下一轮候选。写作阶段（profile 及之后）的 `--reason` 是作者对写作的要求，交给提纲、初稿和改稿，不记进否定记录，例如 `goto outline --reason '主结果换成同码率下的存储节省'`；`goto plan --reason '<要补的实验>'` 把要补的实验交给职责表。已经写完初稿的项目要按新的组织重写时，`goto profile` 从备料开始重走写作阶段。通过关口一的机会都留作锚点，`goto methods --anchor <编号>` 换到另一个锚点继续出候选（方法失败的次数按锚点分别记）；当前锚点被放弃后直接 `goto methods`，会接着用最近的那个锚点。`goto` 回到写方法卡之前的阶段时，关口二的结论、修订和探针状态一并清掉；回到找方法之前或越过关口二时，细化和补强的状态也清掉。`python <skill 目录>/scripts/funnel.py` 统计一个项目在各关口花了多少轮、初筛怎么结束、时间花在哪，并按规则给出提示。

防止原地打转的几条规则（参考了 ARIS、autoresearch、AI Scientist 等自动科研流程的做法）：判断只排序、不否决，淘汰候选只看非创新清单、预算和实验结果；一轮出一组候选、用初筛排序，接近的细化而不是丢掉；每类循环都有轮数上限，到上限后下一轮必须下结论，不能再判待定；连续两轮没有新证据就换方向，换的是算子或插入点，不是在同一框架里继续调参；量过提升空间的锚点不因方法失败而放弃；否定结果写进否定记录，之后的候选必须避开；工具问题只降低证据可信度，不阻断科学结论。

已经有进度的项目用 `init --start <阶段>` 从中间开始，例如已有方法卡时 `--start gate2`。从关口二之后的阶段开始（例如只想写稿的 `--start draft`）会越过关口二，必须加 `--gate2-passed '<依据>'` 说明为什么关口二已经通过，这条依据记进流程记录。

研究和初稿已经由 ARIS 完成时用 `init --polish`（收尾模式）：只跑 profile → figures → lint → polish → lint ⇄ revise → handback，不建 runs.md、gates.md 这些研究阶段的文件，也不需要本节第 5 步拷贝 CLAUDE.md、AGENTS.md。顺序和规则见 [aris.md](aris.md)。

## 5. 项目设置

1. 论文仓库是一个 git 仓库（`codex exec` 默认只在 git 仓库里运行）。把 skill 里的 `assets/AGENTS.md` 和 `assets/CLAUDE.md` 拷到仓库根目录（Codex 读 AGENTS.md，Claude Code 读 CLAUDE.md），在 CLAUDE.md 顶部填好项目信息。
2. 只用 Claude 模型时跳过这一步，第 3 步的 init 加 `--reasoner claude`（第 8 节）。否则 Codex 装在运行 Claude Code 的同一台机器上并已登录，也就是有 GPU 和数据的那台；先在终端跑一次 `codex exec '回复 OK'` 确认能用。pipeline.py 生成的提示里写了 skill 目录的绝对路径，Codex 直接读那里的文件，所以不必再为 Codex 单独安装一份 skill（单独请 Codex 做事、不走流程时才需要）。
3. 在仓库根目录运行 `python <skill 目录>/scripts/pipeline.py init`（以 method-paper 插件安装时是 `method-paper-pipeline init`）。
4. 把 `assets/claude-settings.json` 拷成仓库的 `.claude/settings.json`（路径按 skill 实际安装位置改）。它做两件事：`"effortLevel": "xhigh"` 让 Claude Code 在这个仓库里用 xhigh 思考强度（项目设置里的这个键对所有模型生效，也优先于用户设置里按模型保存的强度；但环境变量 `CLAUDE_CODE_EFFORT_LEVEL`、启动参数 `--effort` 和会话里的 `/effort` 会覆盖它，而 `/effort xhigh` 会把 xhigh 存成这个模型在所有项目里的默认值）；`permissions` 预先允许 `codex exec`、pipeline.py 和 code_lineage.py，免得每次询问。
5. Codex 的思考强度由 pipeline.py 在每条命令里带上 `-c 'model_reasoning_effort="xhigh"'`，默认就是 xhigh；能用的强度取决于 Codex 的模型；当前模型不支持 xhigh 时，用 `pipeline.py set --codex-effort high` 改低一档，用 `set --codex-model <模型>` 指定模型，流程进度不受影响。不走流程、单独调用 Codex 时，可以在 `~/.codex/config.toml` 里写 `model_reasoning_effort = "xhigh"`。
6. 之后对 Claude Code 说"开始"或"继续"，它按 CLAUDE.md 里的循环自动推进。

## 6. Codex 是怎样被调用的

`pipeline.py next` 会把当前 Codex 阶段的完整提示写进 `research/prompts/<阶段>.md`，并给出要运行的命令，例如：

```bash
# 只读，需要检索文献的阶段加实时搜索；最终回复写进本次运行自己的文件
codex exec -c 'model_reasoning_effort="xhigh"' -c 'web_search="live"' -o research/codex/problems-1.md - < research/prompts/problems.md

# 写稿、改稿：进入论文目录用可写沙箱，Codex 只能改论文目录里的文件；路径都是绝对路径
cd /path/to/repo/paper && codex exec -c 'model_reasoning_effort="xhigh"' --sandbox workspace-write -o /path/to/repo/research/codex/draft-1.md - < /path/to/repo/research/prompts/draft.md
```

- 提示开头写明 skill 目录、仓库根目录和 AGENTS.md 的绝对路径，所以在论文子目录里运行也能找到它们。
- 默认只读沙箱：Codex 能读仓库、能检索，但改不了代码和结果文件。
- 一次审查可能跑十几分钟，超过命令的默认超时，Claude Code 放到后台运行，等命令结束再执行 `pipeline.py done`。`-o` 的文件在 Codex 结束时才写出，文件出现就说明这次运行完成了。
- 命令失败、输出文件没有出现、或者最后一行不是合格的 PIPELINE 行时，运行 `pipeline.py retry`：它换一个新的输出文件名（如 `problems-2.md`）并给出重跑命令。不要用 `codex exec resume` 去补一行，也不要手工补写。
- 不走自动流程、只想单独请 Codex 做一件事时，也可以手写命令，提示用单引号包起来（避免 shell 把 `$method-paper` 当成变量），例如 `codex exec -o research/codex/adhoc-review.md '使用 $method-paper。只依据 research/ 下的方法卡、职责表、runs.md 和 results/ 审查结果……'`。

## 7. Codex 不可用时

`codex` 没装、没登录或额度用完时，Claude Code 告诉用户，征得同意后才把 Codex 阶段交给一个独立子代理：只给它 method-paper skill、`research/prompts/<阶段>.md` 和 `pipeline.py next` 给出的输出文件路径，不给 Claude Code 自己的结论；完成后用 `pipeline.py done --by subagent`，流程记录里会写明这一阶段不是 Codex 做的。用户不同意就暂停，等 Codex 可用后继续。

**Codex 的沙箱起不来时**（Docker 容器里常见，报 `bwrap: No permissions to create a new namespace`）：Codex 读文件要靠执行命令，沙箱起不来它就一个文件也读不到，这时它交出的判定没有依据，不能用。根治是在宿主机或容器启动参数上放开 bubblewrap 需要的权限。用户同意让容器充当唯一的沙箱时，用 `pipeline.py set --codex-sandbox none`（新项目 `init --codex-sandbox none`）：之后每条 Codex 命令带 `--dangerously-bypass-approvals-and-sandbox`，写稿改稿也不再用 `--sandbox workspace-write`。Codex 因此失去写保护，流程在每个 Codex 阶段开始时记下 `research/`（只读阶段还有论文目录）的文件内容，`done` 时逐个核对，有变化就不通过，并列出改动的文件。Codex 运行期间 Claude Code 不改这些文件。改回去用 `set --codex-sandbox default`。ARIS 的 codex MCP 桥用 `scripts/codex_nosandbox.sh` 做同样的事，见 aris.md 第 7 节。

## 8. 只用 Claude 模型

不装 Codex、推理与审查也用 Claude 时：新项目 `pipeline.py init --reasoner claude`，已有项目 `pipeline.py set --reasoner claude`，进度不受影响；在 `~/.bashrc` 里写 `export METHOD_PAPER_REASONER=claude`，之后新项目默认就是 Claude。模型用 `--claude-model` 指定（`opus`、`fable` 这类别名或完整模型名，默认用 Claude Code 自己的设置），强度用 `--claude-effort`（默认 xhigh，可以设 max；`default` 表示不传，沿用 Claude Code 的设置），init 和 set 都能改。

这时 `pipeline.py next` 给出的命令是 `pipeline.py reason`，Claude Code 照样在仓库根目录后台运行它，结束后 `done`。`reason` 为当前阶段写好提示，单独起一个进程（`next` 的 runs 一行就是实际命令）：

```bash
# 只读阶段，在仓库根目录；需要检索文献的阶段工具里加 WebSearch、WebFetch
claude -p --output-format text --no-session-persistence --strict-mcp-config --effort xhigh \
  --tools Read,Grep,Glob --allowedTools 'Read Grep Glob' --disallowedTools 'Bash Edit Write NotebookEdit' \
  --append-system-prompt '<推理方的角色说明>' < research/prompts/gate2.md

# 写稿、改稿，在论文目录；acceptEdits 只放行当前目录里的修改
cd /path/to/repo/paper && claude -p ... --permission-mode acceptEdits --tools Read,Grep,Glob,Edit,Write \
  --allowedTools 'Read Grep Glob' --disallowedTools 'Bash NotebookEdit WebSearch WebFetch' ... < /path/to/repo/research/prompts/draft.md
```

- **独立。** 每个阶段都是新进程、全新上下文，只拿到提示和文件路径，看不到执行方的对话；`--no-session-persistence` 不留会话记录，`--strict-mcp-config` 不加载任何 MCP 服务。
- **权限。** 没有 Bash，跑不了流程命令和实验；只读阶段没有写文件的工具；写稿阶段只能改论文目录（不要给 `Edit`、`Write` 加不带路径的允许规则，那样会放行任何位置）。和无沙箱的 Codex 一样，`done` 时核对 `research/`（只读阶段还有论文目录）没有变化。
- **角色。** 仓库的 CLAUDE.md 是写给执行方的，推理进程也会读到它；系统提示和 CLAUDE.md 开头都写明，被流程调起的推理进程不按执行方的规则做事，按 AGENTS.md 和本次任务工作。
- **输出。** 回复写进 `research/codex/<阶段>-<次数>.md`，进程结束时才出现；报错信息在同名的 `.log` 里。命令失败或最后一行不是合格的 PIPELINE 行时同样用 `retry`。
- **版本。** 需要较新的 Claude Code（用到 `--tools`、`--effort`、`--strict-mcp-config`、`--no-session-persistence`）；日志里出现 unknown option 时先 `claude update`。执行方运行 `reason` 用的是 pipeline.py 的命令，`assets/claude-settings.json` 里已经预先允许。
- **代价。** 推理方和执行方同属 Claude，交叉检查不如换一家模型：同一类模型容易有同样的盲点。补救是推理方不看执行方的上下文、用比执行方更强的模型并开最高强度，例如执行方用 Sonnet、推理方 `--claude-model opus --claude-effort max`；更重要的是初筛和实验里的阳性对照、同步训练的基线和等容量对照，它们不依赖任何一方的判断。

ARIS 的审稿换成 Claude 见 aris.md 第 7 节。
