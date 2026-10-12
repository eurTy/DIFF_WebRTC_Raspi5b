# 更新日志

## 2026-10-13 v0.2.0 卡顿与恢复优化

保留旧系统、旧论文、旧数据及 `semantic-adaptive-v2-20261012` 标签。树莓派保留镜像 `semantic-adaptive-v2:baseline-268a358`；仅更新独立 8088 容器。

- 新增 `realtime` 反馈：保守的一至两帧发送窗口，连续慢确认才收缩预算，中性区间保留低频恢复探测。`single`、`median` 算法保留作对照。
- 过期帧不显示，也不刷新任务区域的高质量确认时间；迟到样本仍导出，图表不只挑选成功帧。
- 编码搜索在重点块均无法放入时保留更好的可容纳背景；同一共享输入帧由不同观看端复用不可变解码。
- 前端复用合成画布、有界顺序解码，增加可见过期状态、自动退避重连、看门狗和快速校时。手动暂停停止重连，已应用配置在恢复后保持。
- 传输超时与参数错误使用不同关闭类别；关闭握手限时，失联客户端及时释放名额。
- 增加流畅观察档 20 fps / 2400 kbps，保留受限对照档 10 fps / 1200 kbps。两档每帧上限相同，提高流畅度消耗更多带宽。
- 只有真实输入覆盖配置启用 `unless-stopped`，避免树莓派重启后实验入口消失。没有改旧容器、内核、网络或摄像头权限。

回归：本地及树莓派各 24 项 Python 测试通过，3 项 JavaScript 协议、时钟及低速看门狗测试通过，合成与实机输入各 16 项浏览器检查通过。三分钟流畅档实际约 19.56 fps，软件段延迟 P95 约 68.71 ms；同参数 10 fps 的正常网络延迟与旧版基本相当。最终一次 6 秒受控断流在恢复后约 765 ms 重新显示，旧版在剩余观察期内未恢复。新数据和失败候选保存在 `results/stability-20261013/`，不把这些短时结果推广为任意 Wi-Fi 保证。

[技术路线与创新边界](docs/technical-route-v020.md)包含任务新鲜度、发送窗口与预算反馈的联合设计，以及测控专业定位。WebRTC 部分可靠传输仅列为后续独立研究路线，尚未实现。

回退仅操作新服务：

```bash
docker tag semantic-adaptive-v2:baseline-268a358 semantic-adaptive-v2:local
SHM_READER_GID=$(stat -c %g /dev/shm/pi-video) docker compose -f /home/eur/semantic-adaptive-v2/compose.yml -f /home/eur/semantic-adaptive-v2/compose.live.yml up -d --no-build
```

回退后网页需重新加载；新代码仍保存在 Git 分支，可重新构建，不需要操作旧项目。

## 2026-10-12 独立研究版验收

旧 main、旧应用目录和旧采集服务均保留。旧系统冻结于 `observer-v1-frozen-20261011`；整合版位于 `release/observer-integrated-v1`，部署目录为 `/home/eur/observer-unified-v1`，没有切换旧运行服务。

新研究版从独立 Git 根目录建立，使用官方 Python 基础镜像、独立依赖与容器，在 `/home/eur/semantic-adaptive-v2` 运行，仅映射 8088。非 root、只读文件系统、移除 capabilities，输入共享目录只读挂载；只为新版追加已有输入目录组，不修改旧权限。未改动主机内核、sysctl、Wi-Fi 配置、启动文件或旧服务。

实现四种传输策略、同帧背景与重点块、单帧在途确认、过期输入拒绝、独立四时间戳校时、帧龄显示、区域框选及数值导出。修复低速传输时关闭连接延迟释放，以及正常视频帧清除参数错误提示的问题。

增加五样本中值反馈并完成消融。真实输入试验未支持默认替换，最终默认仍为单次反馈，中值模式保留为实验选项。保留低性能样本、校时警告、失败记录和原始单次反馈标签，不选择性删改结果。

验收：本地及 Pi 各 17 项 Python 测试、2 项 JavaScript 测试通过；合成和真实输入分别通过 15 项浏览器检查。复核 1080 个 Pi 配对编码样本及 84 个网络测量单元。完成 30 页可编辑本科论文初稿，含 10 张表格、8 个原生公式和 2 幅图，已逐页渲染检查。

## 使用与回退

旧入口：`http://192.168.3.19:8080/monitor/`。新入口：`http://192.168.3.19:8088/`。地址随树莓派网络变化而变化，仅限可信局域网，不可直接暴露公网。

以下为 v0.1.0 手动启动方式；v0.2.0 实时输入配置已启用单容器重启恢复。需要手动启动时，在旧采集正常运行的前提下执行：

```bash
cd /home/eur/semantic-adaptive-v2
SHM_READER_GID=$(stat -c %g /dev/shm/pi-video) docker compose -f compose.yml -f compose.live.yml up -d --no-build
```

停止新版，不影响旧服务：

```bash
docker compose -f /home/eur/semantic-adaptive-v2/compose.yml down
```

没有电机、PLC、GPIO 或工艺控制接口。软件延迟不是光学端到端延迟；合成标记保真度不是实际工件识别准确率。论文是有实测依据的研究初稿，仍需依据学校模板和导师要求修改，并补充真实工业任务验证。
