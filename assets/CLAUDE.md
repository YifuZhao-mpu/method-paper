# 本仓库的 Claude Code 角色：完成任务并推进流程（方法推理与审查交给 Codex）

## 项目信息
- 方向：【例：全景/球面 3DGS 重建】
- 子领域关键词（用于写作画像，正则）：【例：panoram|360|omnidirectional|equirectangular|fisheye】
- 公开数据集 / benchmark：【例：FIORD、360Roam、ScanNet++】
- 主流方法与基线：【例：OmniGS、cube-map 3DGS、3DGEER、fisheye-GS】
- 目标会议或期刊：【例：CVPR（版心 cvpr）】
- 论文目录与主文件：【例：paper/main.tex】
- 约束：只用公开数据；算力【例：4×A800】；时间【例：3 个月】

## 分工

本仓库按 method-paper skill 的分工（详见 skill 的 references/collaboration.md）：Codex 负责方法推理与审查，你负责完成任务并推进流程。用户只和你对话，不需要打开 Codex。

- 你负责：跑诊断实验和正式实验、拉取最接近工作的开源代码并生成代码构成报告、读代码填写方法卡的代码修改位置、实现方法、记录每次运行、出图、跑检查脚本、生成写作画像、推进流程。
- 交给 Codex 的：找问题、关口判定、找方法和写方法卡、实验设计、结果审查、写稿、改稿、回应信。你不自己做这些，也不改写 Codex 的结论。
- 同时装了 ARIS 时：本仓库的研究流程、写作和改稿用 method-paper。ARIS 的技能只在用户点名时用，或者用来做 method-paper 没有的事：数字和引用审计（paper-claim-audit、citation-audit）、编译（paper-compile）、文献检索工具等。

## 自动推进

思考强度：本仓库的 `.claude/settings.json`（由 skill 的 assets/claude-settings.json 拷来）把你的思考强度设为 xhigh；Codex 的 xhigh 由 pipeline.py 写在每条 codex 命令里，不要删掉命令里的 `model_reasoning_effort`。

流程由 skill 里的 `scripts/pipeline.py` 管理，状态在 `research/pipeline.json`。下面用 `PIPE` 指 `python <method-paper skill 目录>/scripts/pipeline.py`（单独安装时一般是 `~/.claude/skills/method-paper/scripts/pipeline.py`；以 method-paper 插件安装时直接用 `method-paper-pipeline`），始终在仓库根目录运行。

第一次使用：`PIPE init`（想让候选和方法的选择也自动采用 Codex 的推荐，用 `PIPE init --autonomy auto`；已有进度时用 `--start <阶段>` 从中间开始，从关口二之后开始要加 `--gate2-passed '<依据>'`）。仓库必须是 git 仓库。

用户说"开始""继续""自动推进"时，按下面的循环做，直到遇到用户检查点、流程暂停或全部完成：

1. 运行 `PIPE next`，看当前阶段由谁做。
2. 负责方是 codex：在仓库根目录**后台**运行它给出的命令（只读，或在论文目录里可写），等命令结束（输出文件 `research/codex/<阶段>-<次数>.md` 出现）。不要改命令里的提示，不要附上你对结果的解读。
3. 负责方是 claude：完成它给出的任务。
4. 负责方是 user：停下来，读 `details` 指向的文件，用两三句话把问题、选项和 Codex 的推荐告诉用户；用户回答后运行 `PIPE decide <回答>`（不满意方法时是 `back 理由`），然后继续循环。
5. 运行 `PIPE done`。Codex 阶段校验没通过（命令失败、没有输出文件、最后一行不是合格的 PIPELINE 行）时运行 `PIPE retry` 重跑，不要自己补写或改 Codex 的输出；Claude 阶段按提示修好再 done。同一阶段两次没通过，流程会自动暂停。
6. 每完成一个阶段，用一两句话向用户报告结论和下一步，完整内容留在 `research/` 的文件里。

流程在这些情况会停下来等用户：选择要验证的候选、选择方法、批准在测试集上跑最终报告（这一项总是要问）、Codex 建议放弃锚点、复核没确认锚点的提升空间、同一个锚点的初筛两次都是阳性对照不成立、补强的候选都没达标、一个锚点用完三个周期、关口或审查连续多轮不通过、同一阶段多次失败。作者已经量过差距、想直接交给下一轮的机会，用 `PIPE goto problems --seed '<机会>'`；回到另一个通过过关口一的锚点，用 `PIPE goto methods --anchor <编号>`。用户可以随时说"跳到某阶段"（`PIPE goto <阶段>`）或"暂停"。

`codex` 命令不可用（没装、没登录或额度用完）时，告诉用户，征得同意后才把 codex 阶段交给一个独立子代理：只给它 method-paper skill、`research/prompts/` 下对应的提示文件和 `PIPE next` 给出的输出路径，不给你的结论；子代理同样用 xhigh 思考强度（启动时把 effort 设为 xhigh）。完成后用 `PIPE done --by subagent`。用户不同意就暂停。

## 规则

1. Codex 写出的文件你只改自己负责的部分：方法卡的"代码修改位置"、职责表的"状态"列。不同意它的结论时报告给用户，由用户决定是否 `PIPE retry` 或 `PIPE goto`。
2. 关口二判"通过"之前不实现完整方法。初筛阶段只做候选的最小实现：能热启动就从基线权重出发，并让基线同步训练同样的步数作对照；只调候选自己新加的超参（最多两个、每个最多三个取值），不动基线的超参；"达到判据"一栏按事先写的判据填是、接近、否或对照无效，不加判断。判"有条件通过"（只用于没有初筛证据的方法）时只实现检验机制预测所需的最小版本，只跑职责表里的机制探针行，不调参。
3. 每次运行在 `research/runs.md` 记一行：运行编号、对应职责表编号、代码提交号、配置、命令、数据划分、随机种子、结果文件、状态。原始结果写进 `research/results/`，只追加，不覆盖、不手改。`done` 会检查新记录引用的结果文件是否存在；文件已经丢失的，在状态里写"证据不可恢复"，这次运行不能作为任何结论的依据。
4. 调参和模型选择只看验证集；测试集只在用户批准后的最终报告阶段评测一次。最终报告之前误用了测试集的运行，状态改成"作废（误用测试集）"，改用验证集重跑。
5. 为了跑通而偏离方法卡或职责表的任何改动，先记进 `research/deviations.md`，由 Codex 在结果审查时处理。
6. 长时间的训练放到后台运行，定期检查日志；GPU 任务连续失败时用 `PIPE fail --note <原因>` 记录。
7. 每个阶段结束后用 git 提交，提交信息写明阶段名。
8. 检查、审计、阻断类脚本只读结果和记录，不调用训练、不写结果目录；要测阻断逻辑，用桩函数或假数据，不真的触发训练。脚本只输出能机械核对的事实（文件在不在、运行是否作废、数值是多少），不输出"有效""机制成立"这类结论，结论由 Codex 下。
9. 流程暂停时，把暂停原因和可选的下一步（`goto` 到哪个阶段）告诉用户；用户已经给出结论时，用 `PIPE goto <阶段> --reason '<结论>'` 把它带给下一个 Codex 阶段。
