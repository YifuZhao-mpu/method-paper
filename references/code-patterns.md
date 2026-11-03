# 顶会论文开源代码的拼接与改动规律

这份规律来自对 2021–2026 年顶会方法论文开源仓库的逐个分析：代码从哪些现成代码库来，改了原代码库的哪些文件、改了多少，新机制一般落在哪里。它只描述代码事实，用来帮助推理方判断最接近工作的真实改动、帮助实验方定位代码修改位置，不评价论文好坏。具体项目里最接近的几篇工作，用第 7 节的命令单独跑一份报告。

## 目录
1. 数据与口径
2. 总体：代码从哪里来
3. 各方向的基础代码库
4. 改动落在哪里
5. 实例
6. 对方法论文的用法
7. 为最接近工作跑一份报告

## 1. 数据与口径

- **样本来源**：与 writing-patterns.md 同一批会议（CVPR、ICCV、ECCV、NeurIPS、ICLR、CoRL、RSS、ICRA、IROS、3DV，2021–2026）。四个子领域的已接收方法论文共 5934 篇，其中论文列表的代码字段或摘要里直接给出 GitHub 仓库的有 887 篇（15%）。很多论文只在项目主页上放代码链接，这里没有统计。
- **抽样**：按子领域分层随机抽取 363 个仓库，全景方向全部 34 个，渲染、LiDAR、机器人方向各约 110 个。论文列表的代码字段主要覆盖 2024 年的会议，所以样本里 2024 年的论文占一半（177 篇），2021 年只有 6 篇，结论偏向近两年的代码习惯。
- **可用代码**：
  - 350 个仓库能克隆。其余 13 个里，有的已删除或设为私有，有的是空仓库，还有几个是数据源把仓库名里的下划线弄丢了，导致地址残缺。
  - 能克隆的仓库里有 42 个（12%）少于 3 个代码文件或不足 300 行，基本是"代码即将发布"的空仓库。
  - 下文统计基于其余 308 个仓库。
- **方法**：用 `scripts/code_lineage.py` 逐个分析仓库。
  - 来源：读 README 里的致谢和"基于某某代码"的声明（作者自己的其他仓库单独列出、不算外部来源），再看子模块和仓库自身代码的 Python 导入。
  - 拷入识别：按特征文件识别拷进仓库的已知代码库，内置 37 个，其中 22 个可以逐文件对比。
  - 版本匹配：在原代码库所有分支的历史里，找和拷入文件完全一致的文件最多的那批提交，再从中选其余差异最小、时间最早的一次。
  - 行数统计：改动行数是去掉注释和格式差异后的代码行差异（Python 先经 ast 规范化），只是重新格式化的文件算作未改。
- **局限**：
  - 只有内置的代码库能识别"拷入并修改"，其他来源只能靠 README 声明，所以"未识别"里既有从头写的，也有基于小众仓库的。README 里的致谢也会把数据集和工具库算进来。
  - "新写的代码"指不属于识别出的基础代码库、也不在第三方目录里的代码，会把从未识别仓库拷来的代码和评测脚本也算进去，只能看量级。
  - 经由中间仓库改来的（例如基于 Mip-Splatting 或 2DGS 再改），相对原版的改动包含中间仓库的改动。
  - 机器人方向的论文覆盖面广（操作、导航、足式运动、强化学习），共同基础本来就少。
  - 拷入同一代码库的仓库，多数方向只有个位数（3DGS 21 个，OpenPCDet 7 个，其余更少）；第 4 节的比例在样本小时只当作参考。
- **原始统计**：全部数字和 62 个拷入并修改的仓库的逐文件改动在 `assets/code_survey_stats.json`。

## 2. 总体：代码从哪里来

308 个有代码的仓库里：

- **四类来源**：21% 把某个已知代码库整份拷进来再改（下文称"拷入修改"），29% 把已知框架当依赖库调用，26% 只在 README 里写明基于某个仓库，24% 识别不出任何来源。
- **两个以上来源**：51% 的仓库有两个以上代码来源（拷入、依赖、README 声明的外部仓库合计；3DGS 和它自带的光栅化器算一个，作者自己的仓库不算），24% 一个都识别不出。常见的做法是一个基础代码库，加上一两个现成模块或工具库。
- **常见组合**：
  - mmdetection + mmdetection3d（19 个仓库）
  - mmdetection + spconv（7）
  - BEVFormer 插件 + mmdetection（5）
  - nerfstudio + tiny-cuda-nn（4）
  - robomimic + robosuite（4）
- **新写代码**：中位数约 7000 行（四分位 3200–14600），含评测和数据处理脚本，只看量级。

## 3. 各方向的基础代码库

| | 渲染（3DGS/NeRF，90） | LiDAR/BEV（94） | 机器人（96） | 全景（28） |
|---|---|---|---|---|
| 拷入修改 | 41% | 19% | 7% | 7% |
| 作为依赖调用 | 20% | 37% | 36% | 4% |
| 只在 README 声明 | 24% | 26% | 21% | 54% |
| 未识别 | 14% | 18% | 35% | 36% |
| 两个以上来源 | 59% | 57% | 44% | 29% |

各方向最常见的基础代码库：

- **渲染（3DGS/NeRF）**：
  - 拷入：3DGS 原版 20 个仓库，它的 CUDA 光栅化器 17 个，nerf-pytorch 5 个。
  - 作为依赖：tiny-cuda-nn 11 个，gsplat 和 nerfstudio 各 4 个。
- **LiDAR/BEV**：
  - 作为依赖：mmdetection 23 个，mmdetection3d 15 个，spconv 13 个，detectron2 6 个，MinkowskiEngine 5 个。
  - 拷入：OpenPCDet 7 个，mmdetection3d 4 个，BEVDet、BEVFormer、Pointcept 各 2 个。
- **机器人**：
  - 作为依赖：Isaac Gym/Lab 12 个，detectron2 8 个，habitat 6 个，robosuite 6 个，robomimic 5 个。
  - 拷入：diffusion_policy 3 个，ACT 2 个，legged_gym 与 rsl_rl、openpi、openvla 各 1 个。
- **全景**：各子任务各用各的仓库，几乎没有共同基础；用到 3DGS 时同样拷入原版和光栅化器。

读法：

- **渲染方向最集中**：22% 的仓库直接拷 3DGS 原版改，算上只拷光栅化器的是 28%，算上调用 gsplat 或光栅化器包的是 41%。
- **LiDAR 方向**以 open-mmlab 和 OpenPCDet 两套框架为主，多数当依赖用，新模块按框架的注册方式加进去。
- **机器人方向**三分之一看不出共同基础，常见的是在仿真器或框架之上自己写策略和数据流程，18 个仓库用到 ROS。

## 4. 改动落在哪里

### 4.1 3DGS（21 个仓库拷入原版）

匹配到的原版版本 18 个在 2023 年，3 个在 2024 年。原版 24 个代码文件里，中位数改了 11 个，38% 原样保留。原文件里中位数新增约 420 行、删掉约 120 行（新增的四分位 290–830 行），新文件大多放在 `utils/` 和 `scene/` 下。各文件被修改的比例和中位改动量（新增加删除）：

| 文件 | 修改比例 | 中位改动行数 | 常见改动 |
|---|---|---|---|
| `arguments/__init__.py` | 100% | 15 | 新超参和开关 |
| `gaussian_renderer/__init__.py` | 95% | 30 | 渲染函数：多输出深度、法向、特征，传新参数给光栅化器 |
| `scene/dataset_readers.py` | 90% | 112 | 新数据格式、相机模型、额外监督（深度、掩码） |
| `scene/__init__.py` | 90% | 23 | 场景加载入口 |
| `scene/gaussian_model.py` | 86% | 88 | 每个高斯的新属性、致密化和剪枝规则、优化器参数组 |
| `scene/cameras.py`、`utils/camera_utils.py` | 81% | 21、20 | 多数是给相机对象挂上额外的逐视角数据（掩码、单目深度等），少数改相机模型 |
| `utils/loss_utils.py` | 71% | 43 | 新损失和正则 |
| `train.py` | 62% | 124 | 训练循环：新损失、新监督、调度 |
| `render.py` | 62% | 79 | 评测时多输出的保存 |

**CUDA 光栅化器**：21 个拷入 3DGS 的仓库里有 13 个（62%）同时改了光栅化器；算上只拷光栅化器、训练流程自己写的，共 19 个仓库改过它。

- **最常改的文件**：`forward.cu`（89%，中位 46 行）、`rasterizer_impl.cu`（89%，30 行）、`rasterize_points.cu`（84%）和对应头文件。
- **改动最大的文件**：`backward.cu` 被改的比例略低（79%），但改动量最大（中位 119 行），新输出和新参数都要补梯度。
- **改动规模**：19 个仓库在光栅化器上新增的行数从 28 到 1832 不等，中位数 137，四分之一超过 750 行。

### 4.2 OpenPCDet（7 个仓库拷入）

原文件中位数 87% 不动。

- **新文件**：核心代码大多写在新文件里。中位数新增 20 个文件，放在 `pcdet/models` 下的 `dense_heads`、`detectors`、`roi_heads`、`backbones_3d` 和 `pcdet/utils` 下。7 个仓库里新文件的代码量都超过对原文件的改动，是后者的 1.7 到 27 倍，中位数 4 倍。
- **原文件里改得多的**：主要是损失（`pcdet/utils/loss_utils.py`，86%，中位 128 行）和数据增强（`data_augmentor.py`、`database_sampler.py`、`augmentor_utils.py`，71–86%，50–123 行）。
- **接线**：注册新模块（`dense_heads/__init__.py`）、在 `detector3d_template.py` 里构建、改 `tools/train.py`、`test.py`、`eval_utils.py`，几乎每个仓库都有，但只占对原文件新增行数的约 6%。
- **整体规模**：原文件里中位数新增约 950 行。

### 4.3 mmdetection3d 一族

样本里 5 个仓库直接拷入 mmdetection3d（4 个在 LiDAR 方向），另有 3 个基于 BEVDet、2 个基于 BEVFormer，15 个把 mmdetection3d 当依赖。

- **直接拷入的副本**：中位数 94% 的文件不动，改动集中在 `tools/train.py`、`mmdet3d/datasets/nuscenes_dataset.py`（各 80%）和 `pipelines/loading.py`（60%，中位 334 行，多帧、多传感器读入），新模块放在 `mmdet3d/models/` 的 `backbones`、`dense_heads`、`losses` 下。
- **基于 BEVDet 的**：全部改了 `view_transformer.py`（中位 279 行）、`loading.py`（331 行）、`transforms_3d.py`、`nuscenes_dataset.py` 和数据转换脚本。
- **基于 BEVFormer 的**：把 mmdetection3d 当依赖，自己的代码全放在 `projects/mmdet3d_plugin/` 插件目录里。
- **数据流程比模型改得多**：这一族里，数据读入和数据集文件的改动量常常和模型改动相当，甚至更大。

### 4.4 机器人

拷入修改的只有 7 个仓库，样本太小，只能举例：

- **diffusion_policy**：改采样器、训练 workspace、策略文件（ManiWAV 改采样器 +135、训练 workspace +62）。
- **ACT**：改 `imitate_episodes.py` 和 `utils.py`。
- **legged_gym 与 rsl_rl**：环境与奖励（`legged_robot.py`）和 PPO 的存储、训练器都改了不少（Robot Parkour Learning）。
- **openpi**：改 `src/openpi/models/pi0.py`、训练脚本和数据读入；**openvla**：改 `vla-scripts/finetune.py`（同一篇 ICLR 2026 论文在两个代码库上各做了一遍）。

### 4.5 NeRF 早期代码库

- **nerf-pytorch**（6 个仓库）：主要改数据读入（`load_llff.py` 83%）；要改方法时 `run_nerf.py` 往往被大段改写（改动时中位 400 多行）。
- **torch-ngp、multinerf**：各 1 个仓库。
- 2023 年以后新的渲染工作大多转向 3DGS 或 gsplat、nerfstudio。

## 5. 实例

改动量是相对原代码库最接近版本的代码行差异（新增 / 删除），只列最主要的几处。

| 论文 | 基础代码库 | 主要改动 |
|---|---|---|
| ODGS（NeurIPS 2024，全景 3DGS） | 3DGS + 光栅化器 | `train.py` +83、`dataset_readers.py` +93、`gaussian_model.py` +57；光栅化器 `backward.cu` +105 / −66、`forward.cu` +55 / −34、`rasterizer_impl.cu` +48（全景投影写在 CUDA 前向和反向里） |
| OmniSplat（CVPR 2025，全景前馈 3DGS） | 光栅化器 | 为 Yin-Yang 网格的两半各拷了一份光栅化器并改名，两份改法相同：`backward.cu` +82、`forward.cu` +60、`rasterizer_impl.cu` +25；没有拷 3DGS 的训练流程 |
| Gaussian Grouping（ECCV 2024） | 3DGS + 光栅化器 | `gaussian_model.py` +120、`render.py` +78、`train.py` +66；光栅化器 +125（`backward.cu` +33、`forward.cu` +25） |
| DNGaussian（CVPR 2024） | 3DGS + 别人改好的光栅化器 | `dataset_readers.py` +161、`gaussian_renderer` +150、`gaussian_model.py` +137；仓库里没有改 CUDA，深度渲染用 README 声明的第三方修改版光栅化器（ashawkey/diff-gaussian-rasterization） |
| GS-IR（CVPR 2024） | 3DGS + 光栅化器 | 3DGS 文件 +1039（`train.py` +269、`render.py` +192）；光栅化器 +1141（`forward.cu` +368、`rasterizer_impl.cu` +237、`rasterize_points.cu` +190） |
| Anti-Aliased 2DGS（NeurIPS 2025） | 3DGS + 2DGS 面元光栅化器 | 光栅化器 `backward.cu` +430 / −237、`forward.cu` +209 / −111、`auxiliary.h` +93 |
| VirConv（CVPR 2023） | OpenPCDet | `augmentor_utils.py` +479、`spconv_backbone.py` +388、`proposal_target_layer.py` +320，另有 30 个新文件 |
| CAGroup3D（NeurIPS 2022） | OpenPCDet | `loss_utils.py` +498、`box_coder_utils.py` +107，另有 15 个新文件 |
| SAFDNet（CVPR 2024） | OpenPCDet | `transfusion_head.py` +120、`database_sampler.py` +108，另有 10 个新文件 |
| MapQR（ECCV 2024） | BEVFormer 插件 + mmdetection3d | 插件目录 +807，其中数据读入 `loading.py` +301（原文件为空）；拷入的 mmdetection3d 只改了约 50 行 |
| Robot Parkour Learning（CoRL 2023） | legged_gym + rsl_rl | `legged_robot.py` +739 / −136、`play.py` +283；`rollout_storage.py` +203、`on_policy_runner.py` +80、`ppo.py` +70 |
| ManiWAV（CoRL 2024） | diffusion_policy | `sampler.py` +135 / −82、训练 workspace +62，另有 23 个新文件 |

## 6. 对方法论文的用法

**推理方（Codex）读最接近工作的代码报告时**
1. 先分清它的改动性质：只动 Python 层的损失、致密化、数据读入，是改训练策略或输入适配；改了表示和渲染（CUDA 前向和反向）、加了新网络模块（新文件加注册）、重写了数据流程，各是不同的环节。论文自称的贡献和代码里真正改的地方不一定一致，以代码为准。
2. 关口二的本质差异要在代码层面也成立。本方法的代码修改位置和改动类型如果与某个最接近工作基本相同（例如都在 `loss_utils.py` 加一项正则，都在 `gaussian_model.py` 改致密化规则），审稿人会把它看成同一路线的变体：要么切到别的环节，要么写清机制上的差异。
3. 拼接不是问题，问题是主张和代码对不上。约一半的顶会仓库有两个以上代码来源；拼来的部分写进实现细节并引用，贡献只写新机制。
4. 方法不要写窄：在 OpenPCDet 或 mmdetection3d 里按"新模块文件 + 注册 + 配置"实现，能直接换进多个检测器；3DGS 方法如果只改 Python 层，可以同时作用于原版和它的变体；改了 CUDA 的方法要说明换成别的光栅化器（例如 gsplat）是否同样成立。

**实验方（Claude Code）实现时**
1. 填代码修改位置时先对照第 4 节的热点表和 `research/code_survey/lineage.md`：同一个基础代码库里，最接近工作改过的位置通常就是接入点。
2. 保持基础代码库的结构，新机制写在新文件里，原文件只做必要的接线和改动（参数、注册、调用），机制开关和等容量对照都放进参数或配置，消融时只改配置。
3. 在 `runs.md` 记下基础代码库的版本（提交号），开源时 README 写明基于哪个仓库的哪个版本。

## 7. 为最接近工作跑一份报告

```bash
# 指定仓库
python <本 skill 目录>/scripts/code_lineage.py --repos esw0116/ODGS lkeab/gaussian-grouping --out research/code_survey
# 从关口一判定里收集 github.com/作者/仓库 形式的地址（自动流程的 code_survey 阶段用最近一次判定的输出文件）
python <本 skill 目录>/scripts/code_lineage.py --from-md research/codex/gate1-1.md --out research/code_survey
```

只下载代码和文本文件，分析完删除克隆（加 `--keep` 保留）。需要能用 git 访问 github.com；一个仓库通常十几秒到一分钟。报告 `lineage.md` 给推理方读，`lineage.json` 是逐文件数据。最接近工作基于的代码库不在内置列表里时，报告只能给出 README 声明和导入；可以在脚本的 `KNOWN` 里补上那个代码库的仓库地址和三五个特征文件。
