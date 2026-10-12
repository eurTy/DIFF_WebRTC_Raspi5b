# 论文初稿与复现

`semantic-adaptive-thesis.docx` 为可编辑论文初稿，标题为“受限环境下的语义自适应视频传输系统设计与实现”。不是学校审核或答辩后的终稿，没有伪造学校、学号、导师、工业场景或生产收益。

正文源文件为 `manuscript.md`，结果表格由 `build.py` 从 `results/analysis.json` 注入，避免手抄和选择性改写数据。`experiments/summarize.py` 独立重算帧龄网格和字节分母；结果文件 SHA256 随文列出。公式采用 Word 原生 OMML，表格可以直接编辑。

复现顺序：

1. 按项目说明复现实验或使用仓库内已标注来源的数值记录。
2. 运行 `python -m experiments.summarize`，生成经过算术核对的 `results/analysis.json`。
3. 在文档环境中运行 `python thesis/build.py`。本次文档环境使用 python-docx 1.2.0、Pillow 12.3.0，图中文字采用 Windows 微软雅黑；其他平台需替换为可用的中文字体路径。
4. 使用 Word 更新目录和分页，输出 PDF 后逐页检查。交付的是 Word 文件，渲染 PDF 和 PNG 仅用于私有排版验收。

排版采用通用 Letter 纵向研究初稿格式，不代表任何学校模板。提交学校前应按本校要求调整纸张、封面、字号、引用格式及附件，并补充真实任务真值和更多独立重复实验。

不得把 `network-pi-fixture-pilot.json` 或电脑预实验混入树莓派正式结果。第二轮校时失败、续测和脚本溯源限制见 `results/experiment-notes.md`。ROI 保真度、信息新鲜度与成功帧延迟有不同的分母和含义；修改结论时必须同时检查这些指标。
