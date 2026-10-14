# 受限环境下的语义自适应视频传输系统设计与实现

面向测控技术与仪器专业的独立研究项目。目标是在受限带宽与边缘算力下，衡量并改善远程观察中“重点细节是否可辨”和“所见状态是否足够新鲜”的矛盾。本系统没有执行器控制接口，不承担安全联锁或机器闭环控制。

2026-10-12 实机验收完成：[更新与重启说明](CHANGELOG.md)、[30 页论文初稿](thesis/semantic-adaptive-thesis.docx)、[独立复核结果](results/analysis.json)。结果包含 1080 个配对样本和 84 个网络单元，不含伪造的工业任务真值。

2026-10-13 稳定性更新为 v0.2.0：[技术路线与本科贡献](docs/technical-route-v020.md)。新增保守自适应发送窗口、预算恢复、过期丢弃和自动重连。新实验独立保存在 `results/stability-20261013/`，不覆盖旧论文或旧数据。旧版基线标签为 `semantic-adaptive-v2-20261012`。

当前 v0.3.0：[修正后的技术路线](docs/technical-route-v030.md)、[配对实验与长测证据](results/convergence-20261013/README.md)。修复弱网降预算滞后与混合确认误升档，采用单帧启动；首次断线可恢复，已应用参数和区域可跨刷新保存。新旧策略在本轮 128/512 kbps 对照中的 P95 已接近，但不能据此宣称任务细节全面占优。页面只保存参数，不保存摄像头图像；本地存储不可用时仍能观看。

独立 v0.4 实验：[运行说明](experiment_v04/README.md)、[技术路线与论文依据](docs/technical-route-v040.md)。它使用新的 8089 入口和只读输入；旧 8088 不受影响。先检验手机端同源帧渐进显示与任务区域真实确认时效，当前仍是 WebSocket/TCP，不将其写成已完成 WebRTC 传输优化。

10 月 14 日续测发现无线客户端之间的严重间歇延迟，具体根因仍待路径隔离。程序回归通过不代表现场已经无卡顿，负面长测与只读网络诊断一并保存，不用前一晚的顺畅片段替代当前状态。

## 与旧项目的关系

本分支 `research/semantic-adaptive-v2` 从空目录和独立 Git 历史建立，不继承旧应用代码，也不使用旧容器镜像作为基础。第三方 Lucide 图标库及许可证独立保留。旧 `main` 固定在 `d31ff54`，冻结标签为 `observer-v1-frozen-20261011`，整合版在 `release/observer-integrated-v1`。不要把本分支合并覆盖旧 main。

默认输入是明确标记的合成实验场景。唯一可选的旧系统边界是 **只读** 48 字节共享内存头适配器；新版默认不依赖它。切换到该输入不占用 `/dev/video0`，不发送反馈，不修改旧文件。原生 USB 输入另有实现，但未证明可与旧采集器共用同一摄像头，禁止自动停掉旧采集器来抢设备。

上述只读边界指视频容器。另有独立、可选的[主机恢复检查](ops/README.md)：仅在旧采集器因已知 USB 设备缺失错误退出、设备重新出现后，有限尝试启动它，不改旧代码、不处理手动停止、不向新容器挂载 Docker socket。本次现场已启用该 timer，并记录采集器恢复启动这一运行状态变化。

## 系统边界

- “语义”指使用者给定的任务区域及其重要性，加上局部变化和高质量刷新等待时间；**不是大模型理解、目标检测或新型神经语义编解码**。
- 每个二进制帧包含同一时刻的低分辨率全景和重点 JPEG 块。浏览器完整解码后一次合成，不与历史补丁混用。
- 四种策略：`full` 全帧 Q85、`uniform` 全局画质分辨率自适应、`static-roi` 固定优先权重、`semantic` 变化与等待时间参与优先级。
- 历史反馈模式每会话最多一帧在途，新默认 `realtime` 在一至两帧之间保守调节；源端读取最新帧，不创建采集 FIFO。实际发帧周期同时受目标帧率和有效载荷上限约束。编码最低档仍超预算时明确计数并降低发送频率，不伪称一定能满足任务细节需求。
- 预算反馈可以切换原始 `single` 与抗离群点 `median`。后者取最近五组确认服务时间的中间样本及其对应包长，减轻孤立慢确认的影响；代价是持续拥塞识别可能更慢。两者的消融实验独立保存，旧基线由 `semantic-single-ack-baseline-20261012` 固定。
- 2026-10-12 的冻结版默认 `single`。当日中值反馈在真实摄像头试验中新鲜覆盖率为 48.3%，低于单次反馈的 81.7%；每模式仅两次短时重复且无线条件不完全相同。v0.2.0 新增的 `realtime` 是另一个方法，不能沿用这些旧数字作为新方法结论，也不宣称在全部限速条件下优于单帧模式。
- WebSocket 基于 TCP，仍存在队头阻塞。没有证明优于硬件 H.264/WebRTC 或专用工业协议。
- 独立四时间戳校时通道支持软件段延迟与当前帧龄显示。数值不是摄像头曝光到屏幕发光的光学端到端延迟。

## 本地启动

Python 3.12，独立虚拟环境：

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock.txt
python -m semantic_video.server --host 127.0.0.1 --port 8088
```

打开 `http://127.0.0.1:8088`。输入为合成场景，权重和区域修改在应用参数后生效。网页默认流畅档为 20 fps / 2400 kbps，受限对照档为 10 fps / 1200 kbps，两者每帧上限均为 15000 B。速率是有效载荷节奏上限，不含 TCP/IP 和时钟控制消息；流畅档不是同带宽算法收益。

## 独立容器

```bash
docker compose -f compose.yml up -d --build
docker compose -f compose.yml logs --tail=30
docker compose -f compose.yml down
```

该 Compose 使用独立项目名，仅映射 8088；非 root、只读根文件系统、移除全部 capabilities、禁止提权、限制 1 CPU 和 64 进程，不修改全局 sysctl、Wi-Fi、CPU governor 或旧服务。合成实验配置不自动启动；v0.2.0 真实输入覆盖文件对本容器启用 `unless-stopped`，随 Docker 重启恢复，手动停止后不自动拉起。树莓派旧服务仍使用 8080 / 8092。

在旧采集已经正常工作且共享文件可读时，可选择只读输入：

```bash
SHM_READER_GID=$(stat -c %g /dev/shm/pi-video) docker compose -f compose.yml -f compose.live.yml up -d --no-build
```

只有新版容器会重建。输入共享路径不存在时明确失败，不自动创建替代输入。输入目录现场权限为 0750，因此只为新容器追加目录现有 GID，不对旧目录 chmod、不改所有者、不用 root 绕过权限；挂载仍是只读。默认实验服务不需要共享挂载或该组。远程页面仅用于可信局域网；当前没有用户身份认证、HTTPS 或互联网接入设计，禁止直接暴露公网。

Pi 若不能稳定下载依赖，可使用 `Dockerfile.offline`：先将 `requirements.lock.txt` 对应 ARM64 wheel 放入 `wheelhouse/`，通过 `docker load` 导入官方 Python 基础镜像，核对文件系统层后将其标为 `python:semantic-v030-base`，再执行 `docker build --network=none -f Dockerfile.offline -t semantic-adaptive-v2:local .`。基础镜像和工具来源见 `deployment-provenance.json`，本轮层摘要核对见 `results/convergence-20261013/base-image-audit.json`。在线 Dockerfile 固定官方多架构索引；离线导入的配置摘要不是官方清单摘要，不能拿它作为新的网络拉取地址。不把 wheel、镜像 tar 或现场媒体提交到 Git。

## 验证与实验

```bash
python -m unittest discover -s tests -v
node --test tests/protocol.cjs
python -m experiments.rd --frames 30 --output results/rd.json
python -m experiments.network --url http://127.0.0.1:8088 --output results/network.json
python -m experiments.feedback --url http://127.0.0.1:8088 --output results/feedback.json
```

浏览器验收脚本为 `tests/browser.cjs`，需测试环境提供 Playwright 和 Edge，执行 `node tests/browser.cjs http://127.0.0.1:8088 runtime/browser`。运行目录不提交；真实摄像头截图也不得公开上传。

`rd.py` 对三种纹理场景、三档预算和四种策略逐帧配对，输出任务区域 PSNR、背景 PSNR、合成标记保真度和编码耗时。帧相同不代表真实工业数据；细线与小色标是人为设定的任务，实验存在场景偏向。`network.py` 使用真实 TCP 字节中继限速，不触碰任何系统 qdisc。它不模拟射频丢包，不能据此推断复杂工厂无线信道表现。

网络实验同时报告收到帧的延迟和按 100 ms 网格采样的帧龄。超过 250 ms 或没有帧的时段算新鲜度失败。合成任务指标要求位置误差不超过 3 像素且颜色状态正确；它用于比较传输失真，不是 AI 检测准确率。软件接收端为 Python 解码，不等于浏览器最终显示。校时前后偏移、连接失败和所有低性能基线均保留。

`results/rd-pc.json` 和 `results/network-pc.json` 为电脑首轮预实验，**不是树莓派最终性能**。论文与最终部署验收将使用分别标注的平台结果，不能把预实验数字换一个标题当作实机数据。

树莓派配对实验为 `rd-pi.json`；原始单次反馈的 48 组主对照为 `network-pi-fixture.json` 和 `network-pi-confirmation.json`。`network.py` 默认显式请求原始单次反馈，用于稳定复现旧基线，不随网页默认值改变。反馈消融由 `feedback.py` 显式请求两种模式。真实输入与合成输入结果分开命名，合成代理任务分数不会用于真实摄像头。

## 可复现性与限制

输入参数、预算档位、合成随机种子、策略、校时数据和原始数值记录都保留。测量不确定性包括时钟路径不对称、软件计时端点、图像生成/解码、操作系统调度和 100 ms 帧龄采样离散化。PSNR 完全一致时以 99 dB 作为有限存储上限。低预算下丢失背景细节是主动代价，不应以重点区域单一指标掩盖。

现阶段不宣称动态策略在所有条件下优于固定 ROI；其价值还必须通过区域刷新公平性、场景变化和预算突变对照检验。未实现真实生产线故障诊断、PLC 接口、自动识别任务区域或工业安全认证。

## 参考来源

- [AccMPEG](https://arxiv.org/abs/2204.12534)：任务相关编码质量分配的研究背景，不作为本项目性能来源。
- [AWStream](https://awstream.github.io/paper/awstream.pdf)：带宽与任务精度自适应的背景。
- [Age of Information](https://arxiv.org/abs/2007.08564)：信息新鲜度指标的背景。
- [DDS](https://people.cs.uchicago.edu/~junchenj/docs/DDS-Sigcomm20.pdf)：服务器驱动区域细化的背景；本项目采用同帧自包含方案。

这些既有思路不能包装成本文首次提出的理论。本科工作的贡献定位是边界清晰的工程实现、受限条件下的对照与失效分析。
