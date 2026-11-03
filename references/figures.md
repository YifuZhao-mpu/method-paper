# 图表规范

## 目录
1. 通用规则
2. 数据图重画流程（paper_plot.py）
3. 表格
4. 全文图表检查（figure_table_check.py）
5. 算法框架图（framework_figure.py）
6. 框架图 JSON 规格

## 1. 通用规则

- **字号和正文差不多。** 在最终排版尺寸下，轴标签取正文字号或小 1 pt（正文 10 pt 时取 9 pt），刻度和图例不小于正文减 2 pt，任何文字不小于 6.5 pt（上下标除外）。中文论文按学校或期刊模板执行；没有规定时，图中字号取正文字号或小一号（例如正文小四，图中五号）。
- **按最终宽度出图。** 出图宽度就等于插入宽度（单栏 `\columnwidth`，通栏 `\textwidth`，子图按子图宽度），插入时不再缩放。缩放一次，字号就跟着变一次。
- **字体与正文一致。** 英文论文用 Times 系字体，公式用 STIX；中文论文中文用宋体，西文和数字用 Times New Roman。优先用 TrueType 字体：Times New Roman、Liberation Serif（与 Times 等宽）、SimSun。Nimbus Roman、TeX Gyre Termes、Noto CJK 通常是 OpenType-CFF 字体，嵌入 PDF 时部分检查工具（如 IEEE PDF eXpress）会报字体类型警告；脚本遇到这种字体会提示。
- **矢量格式。** 数据图和框架图用 PDF 或 SVG，PDF 里的字体以 TrueType（Type 42）嵌入，不要出现 Type 3 字体，部分会议的 PDF 检查会因此报错。照片类内容可以是位图，但在插入尺寸下不低于 300 dpi。
- **文字不遮挡线条和内容。** 图例放在空白区域或图外；标注不压数据点和曲线；刻度文字不互相重叠。出图前跑遮挡检查。
- **配色一致。** 同一方法在全文所有图里用同一个颜色，本文方法用最醒目的颜色（paper_plot 默认朱红）；用色盲友好的调色板，并用线型或标记做第二重区分。
- 图内不放标题，标题由图题承担；坐标轴写清物理量和单位；图中不出现文件名、路径、实验编号。

## 2. 数据图重画流程

1. 找原始数据（CSV、日志、npz 等）。只有截图没有数据时，向作者要数据，不从图片描点。
2. 用 `scripts/paper_plot.py` 出图：

```python
import sys; sys.path.insert(0, "<本 skill 目录>/scripts")
import paper_plot as pp
pp.setup(venue="cvpr")                       # cvpr iccv eccv ieee icml neurips iclr thesis；可传 body_pt、fig_pt、font_family
fig, ax = pp.figure("single", aspect=0.62)   # "single" 单栏宽，"double" 通栏宽，也可直接传英寸数
colors = pp.method_colors(["Ours", "A", "B"], ours="Ours")
marks = pp.method_markers(["Ours", "A", "B"])
ax.plot(x, y_ours, color=colors["Ours"], marker=marks["Ours"], label="Ours")
...
pp.place_legend_safely(ax)                    # 依次尝试不压数据的位置，都不行就放到图上方
issues = pp.check_overlaps(fig)               # 文字与曲线、数据点、柱、填充区、其他文字的重叠，刻度拥挤，越界
pp.save(fig, "figs/curve")                    # 输出 curve.pdf 和 curve.png，再检查一次
```

3. 打开 PNG 预览确认：没有遮挡；字号接近正文；配色与全文一致。`save` 打印的警告逐条修掉。
4. 在稿件中插入时，宽度与出图宽度一致：`single` 对应 `\columnwidth`，`double` 对应 `\textwidth`。子图按子图实际宽度出图。
5. 中文学位论文用 `venue="thesis"`，并按学校模板核对版心宽度和字号（`pp.setup(venue="thesis", single_in=..., body_pt=..., fig_pt=...)`）。

## 3. 表格

- 用三线表（booktabs 的 toprule、midrule、bottomrule），不用竖线。
- 全文表格字号统一：与正文同号，或统一小一号（例如都用 `\small`）。不用 `\resizebox`、`\scalebox` 缩放整张表，那样每张表的字号都不一样。表太宽时删列、缩写列名、改成通栏表。
- 最优结果加粗，次优加下划线；指标方向用 ↑ / ↓ 标注；小数位数统一；本文方法放在最后一行并统一突出显示。
- 表题位置按模板（IEEE 系模板表题在表格上方）。
- 一张表只回答一个问题；不同配置的结果不放进同一张表；不出现"改动前/改动后""旧版/新版"。

## 4. 全文图表检查

```
python scripts/figure_table_check.py main.tex --venue cvpr
python scripts/figure_table_check.py main.tex --columnwidth-in 3.5 --textwidth-in 7.16 --body-pt 10
python scripts/figure_table_check.py paper.docx
```

LaTeX 模式会递归读取 `\input` 和 `\include`，对每个 `\includegraphics` 计算插入宽度（含 `figure*`、`minipage`、`subfigure` 里的宽度）与原图宽度之比，读出 PDF 图中文字在插入后的实际字号，检查 Type 3 字体和位图的有效分辨率；对每个表格报告字号命令和整表缩放，并检查各表字号是否统一。Word 模式比较各表格与正文的字号。图片里的文字在 Word 中无法自动读取，需要按插入宽度出图后目测。

## 5. 算法框架图

### 要求

- **多用图形元素。** 输入用数据集里的真实样例（图像缩略图、点云、机器人视角帧），中间特征用张量堆叠、热力图、点集等图元，运算用 ⊕、⊗、⊙ 和拼接符号，损失用独立的虚线框。
- **分区用颜色，不用 (a)(b)(c)。** 每个颜色区域对应方法里的一个机制环节，3–5 个为宜。
- **底部写出每片区域的内容。** 默认在图内底部的图例条里，为每个区域给出色块、区域名和一句话机理说明。说明要简单，讲清算法机理，不放结果和任何指标数字。
- **字号与正文接近。** 按最终宽度出图，通栏图宽度就是 `\textwidth`。
- **符合学术规范。** 图中符号与方法节的公式一致；实线表示前向数据流，虚线表示仅在训练阶段使用的监督；冻结参数的模块带雪花标记，并在图例中说明；不用阴影、立体、渐变等装饰；配色与全文一致。
- **图题不重复图例。** 机理说明只放一处：放在图内底部时（默认），图题只写一句总述；版面紧张时设 `"legend": {"descriptions": false}`，图内只留色块和区域名，机理说明改写进图题，按颜色区域逐句说明。两种情况脚本都会打印对应的图题草稿。图题不写结果。

### 流程

1. 从方法卡提取区域、模块、数据流、损失和符号。
2. 从数据集抽 2–4 张代表样例，选与失效机制相关的场景更好；点云直接给 `.npy`、`.npz`、`.bin`、`.txt`、`.csv`、`.ply`、`.pcd` 文件。`.bin` 默认按每点 4 个数（KITTI）读取，nuScenes 的 `.pcd.bin` 要设 `"bin_dims": 5`。
3. 写 JSON 规格（模板：`assets/framework_example.json`）。先在纸面上排好两行或三行的主干流程，再定坐标。
4. 运行 `python scripts/framework_figure.py spec.json -o figs/framework`，得到 PDF、SVG、PNG。打开 PNG 检查，按警告调整坐标，直到输出"检查通过"。
5. SVG 里的文字保留为可编辑文本，作者可以在 Inkscape、Illustrator 或 PowerPoint 里微调；在稿件中插入 PDF。

## 6. 框架图 JSON 规格

坐标单位是英寸，原点在绘图区左下角；底部图例条自动加在绘图区下方，成图高度 = `canvas.height_in` + 图例高度。

```json
{
  "canvas": {"width_in": 6.875, "height_in": 1.98, "font_pt": 9,
             "font_family": ["Times New Roman"], "cjk_family": ["SimSun"], "show_region_names": false},
  "regions": [
    {"id": "enc", "name": "区域名", "color": "#0072B2", "box": [x, y, w, h], "desc": "一句话机理说明"}
  ],
  "nodes": [ ... ],
  "edges": [ ... ],
  "legend": {"columns": 3, "descriptions": true, "line_styles": true, "name_weight": "bold",
             "labels": {"forward": "前向数据流", "supervision": "训练阶段监督", "frozen": "冻结参数"}}
}
```

**节点类型**（共同字段：`id`、`type`、`region`（取该区域颜色）、`color`（覆盖颜色）、`label`、`label_pos`（below / above / left / right / none）、`label_pt`、`label_width`）

| type | 专有字段 | 用途 |
|---|---|---|
| `image` | `src`、`box`、`stack`（叠放张数）、`stack_offset`、`crop`（[左, 上, 右, 下] 归一化裁剪） | 数据集样例图、预测结果图 |
| `points` | `src`、`box`、`view`（persp / bev）、`yaw`、`pitch`、`range`、`max_points`、`point_size`、`cmap`、`frame`、`bin_dims` | 点云 |
| `tensor` | `box`、`layers` | 特征图堆叠 |
| `heatmap` | `src`（2D .npy；省略时画示意热力图）、`box`、`grid`（[行, 列]）、`seed`、`cmap`（region 或 matplotlib 色表名）、`grid_lines` | 注意力、权重、相似度矩阵 |
| `module` | `text`、`box`、`frozen`、`text_pt` | 网络模块、算子模块 |
| `op` | `op`（add / mul / dot / concat）、`center`、`r` | ⊕ ⊗ ⊙ 拼接 |
| `loss` | `text`、`box` | 损失（虚线框） |
| `text` | `text`、`xy`、`ha`、`va`、`size`、`style` | 少量补充文字 |

**连线**：`{"from": "a", "to": "b"}`。可选字段：`style`（solid / dashed，虚线默认用损失色）、`from_side` 和 `to_side`（left / right / top / bottom）、`via`（折点列表）、`route`（elbow / straight）、`label`、`label_side`（above / below / left / right）、`color`。未指定时，脚本按两个节点的相对位置选边，不对齐时自动走直角折线。

文字中可以写 `$...$` 公式（matplotlib mathtext），公式里不要放中文；中文与公式混排时，脚本会把正文部分用正文字体、公式部分用 STIX 分段排版。中文模块名过长被自动折行时，脚本会提示加宽方框，或在 `text` 里用 `\n` 在词语之间断行。

**检查项**（出现即警告，修完才算通过）：文字与文字、文字与节点、文字与箭头的重叠，模块文字超出方框，冻结标记压字，箭头穿过无关节点，节点越出所属区域或画布，底部说明超过 3 行或出现结果数字，缺少数据集样例图或点云（画灰色占位框或示意点云），热力图文件路径写错，图中有中文却没有中文字体，没有 Times 系字体。**提示**（不算失败）：未给 src 的示意热力图、`.bin` 的读取方式、OpenType-CFF 字体。
