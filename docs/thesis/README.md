# 论文初稿与复核入口

题目：**面向设备远程运维的视频与状态协同监测系统设计及实验研究**。

- [完整 Word 初稿](draft/thesis-draft.docx)，可编辑公式与自动目录。
- [可检索正文](draft/thesis.md)，与 Word 同一来源。
- [真实实验数据与系统审计](../monitoring/2026-10-11/README.md)。
- [部署和演示](../../monitoring/README.md)。

这是完整研究初稿，不是已完成真实产线验证的定稿。未编造作者、学校、导师、电机/PLC、光学测量、AI 准确率或工厂收益。默认使用 Letter 排版；提交前改为学校要求的模板和纸张，作者应亲自复核数据、引用并按学校规范说明辅助工具使用。现场隐私图像不在论文或公开仓库中。

## 研究落点

核心是测量系统与设备诊断：真实状态采集、时间戳语义、新鲜度质量、告警回差、故障前后证据、可复现实验。现有视频库是观测链路，不是被丢弃重写的背景。摄像头提供环境上下文，不替代光电开关、编码器或安全保护。旧模拟往复项目保留作历史选项，已不再是当前主线。

## 数据依据

时钟 ABBA 四轮 9111 个成功帧；完整监测页 610.361 s、12955 帧；三次真实采集暂停及恢复；三轮 Linux 队列层探索。另外复核了此前保存的 900.096 s 发送节奏实验。不同口径与时段分开报告，不作虚假的固定优化百分比。

当前无线长尾没有消除。完整 HMI 的发布至解码均值 39.3 ms、P95 62.8 ms，并不能掩盖画面年龄 P95 1566.2 ms 与 48 次超过 1 秒的相邻解码间隔。论文同时报告这些结果，避免成功帧指标的幸存偏差。

## 重建

构建脚本使用 Python、python-docx、Pillow 及 Windows 微软雅黑字体。它不会读取现场图像，只读取公开数值并生成架构及统计图。

```powershell
python docs/thesis/build_thesis.py --matrix docs/monitoring/2026-10-11/matrix --fault docs/monitoring/2026-10-11/fault-results.json.gz --output docs/thesis/draft
```

计算结果由原始 JSON.gz 复核：

```powershell
python tools/summarize_benchmark.py docs/monitoring/2026-10-11/matrix/C-hmi-soak.json.gz
```

源码中的模板为限定格式的 Markdown：标题、段落、表格、公式和图标记。Word 内有原生 OMML 公式；目录页码由 Word 更新。最终文件已通过本机 Word 隐藏渲染、PDF 页面栅格化和逐页视觉检查。随附 PNG 是论文图表，不是页面截图；渲染中间文件不发布。

本机打包渲染器因未安装 LibreOffice 不能运行，因此采用已经安装的 Microsoft Word 原生渲染作替代。此依赖限制不影响 Word 文件阅读；其他平台重建需提供相应字体及渲染工具。
