# 发送节奏与分段计时实验

日期：2026-10-10。基线为 `4fb6611`。保持 `1280x720`、摄像头 `30fps`、`CAMERA_BUFFERS=2`、`CAMERA_SHARPNESS=4` 和原始 MJPEG，不改变 JPEG 量化质量、不二次压缩。

## 已实现

WebSocket 最新帧模式新增三种可选节奏：

| URL 参数 | 策略 | 目的与代价 |
| --- | --- | --- |
| `flow=decode` | 解码完成后归还一个帧额度 | 最保守，网络等待与解码串行 |
| `flow=receive` | 收到完整帧即归还一个额度 | 下一帧传输与本帧解码重叠 |
| `flow=window2` | 最多两帧未确认，解码/丢弃后归还额度 | 更积极重叠，但可能增加 TCP 队列与旧帧等待 |

示例：`http://192.168.3.19:8080/?transport=ws&flow=receive`。无 `flow` 参数仍使用 `decode`；无 `transport` 参数仍使用原 WebRTC。发送节奏选择只适用于 WebSocket，不暗中改变原协议的参数。

前端新增 `LatestRenderer`，任意时刻最多一个正在解码帧和一个待解码最新帧；新帧覆盖待解码旧帧时归还其额度。避免并行修改同一个 `<img>` 造成解码相互取消。服务端初次请求只接受 1 或 2 个额度，重复/伪造确认不会增加窗口。窗口大小限制的是应用消息数量，不能清空 TCP 内部队列，也不等于网络延迟上限。

## 时间戳与兼容性

采集端在原 128 MiB SHM 尾部保留 64 字节 timing sidecar，原 48 字节头和 JPEG 起始位置不变，`flags & 2` 表示存在扩展。头、图像和 sidecar 使用同一次奇偶写序号提交。Node 读取后复核整个原头，并验证 timing 的 magic、帧 ID 与发布时间，拒绝混合快照。原网关仍读取原头和 JPEG，发送 VPF2，不需要同步修改网关。

sidecar 为 Pi 本机小端布局：magic `0x314d4954`、帧 ID、驱动 sequence、V4L2 flags 各 4 字节，随后为 driver/dequeued/published 三个 64 位微秒时间戳，最后保留 24 字节。位置为 `128*1024*1024-64`。

WebSocket 检测到有效扩展时发送 VPF3，全部整数采用大端网络字节序：

| 偏移 | 字节 | 字段 |
| ---: | ---: | --- |
| 0 | 4 | magic `0x56504633` |
| 4 | 4 | frame_id |
| 8 | 2 | fragment index，当前为 0 |
| 10 | 2 | fragment total，当前为 1 |
| 12 | 4 | JPEG 字节数 |
| 16 | 8 | SHM published_ms，保留旧口径 |
| 24 | 8 | driver_us |
| 32 | 8 | dequeued_us |
| 40 | 8 | published_us |
| 48 | 8 | send_us，调用 `ws.send` 前记录 |
| 56 | 4 | V4L2 buffer flags |
| 60 | 4 | V4L2 driver sequence |
| 64 | N | 原样 JPEG |

旧采集程序没有扩展时仍发送 VPF2；前端兼容 VPF1/2/3。WebRTC 当前只有旧口径延迟，分段计时显示为空，这是协议能力差异，不是测量值为零。

## 测量含义

页面在“分段计时”中显示：

- 驱动到输出：采集程序的 published 与 V4L2 driver 时间戳差值。
- 输出到发送：`ws.send` 调用前减去 published，包括取最新帧与等待额度，不含后续内核排队。
- 发送到收到：浏览器 WebSocket 整条消息回调时间，校准到 Pi 时钟后减去 send；包括用户态/内核排队、网络和浏览器调度，不能称作纯 Wi-Fi 空口耗时。
- 收到到解码：包括浏览器等待解码的时间及 JPEG 解码，不只是解码器 CPU 耗时。
- 驱动到解码：比旧口径更靠前的估计；仍不包括显示器刷新。

启动日志本次报告 timestamp flags `73728 = 0x12000`，即 MONOTONIC + SOE。根据 V4L2 定义，源标记用于区分开始曝光与帧结束；这里只信任驱动的报告，没有通过外部仪器验证摄像头内部时间戳准确度，不能直接称为光学端到端实测。无已知单调时钟或驱动时间晚于出队时间时，不显示驱动口径数值。[Linux V4L2 buffer 文档](https://docs.kernel.org/userspace-api/media/v4l/buffer.html)

在线校准取最近 30 秒最小 RTT，少于三次回复或最后回复超过 10 秒则不显示分段数值。跨端段落有链路不对称与时钟漂移误差；同端时间差不需要跨端校准。旧口径 published_ms 有不足 1 ms 取整误差，因此它与微秒分段之和可能略有不同。

## 对照方法

同一 PC、Pi、摄像头与 Wi-Fi，按 `decode / receive / window2 / window2 / receive / decode` 顺序，各采样 150 秒，总有效采样目标 15 分钟；每轮额外预热和校准。反向第二轮用于减少简单的时间顺序偏差，但仍不是完全受控实验。

不同时开两个 viewer。Edge 无头浏览器调用实际页面、记录成功解码帧的时间戳、50 ms 间隔画面年龄、时钟回复及错误。Pi 同时被动记录 `iw event` 和每秒一次到路由器的 Ping，不主动触发扫描、不改 Wi-Fi 配置。顺序测试和同一无线环境的波动意味着不能把差值全部归因于算法。

报告必须一起列出解码 fps、延迟均值/P95、驱动口径延迟、画面年龄、超过 200 ms / 1 s 的解码间隔及图像错误。成功帧延迟不会直接包含缺帧期间的等待，因此不能单凭低延迟值宣布不卡顿。

原始 JSON 只保存数值和状态，不包含 JPEG 图像、SSH 密码、访问令牌。摄像头截图仅保留本机。

## 实测结果与选择

六轮实际有效采样合计 900.096 秒、21,412 个成功解码帧，全部 1280x720，浏览器异常、图像错误及负延迟样本均为零。下面按实际运行顺序列出；两种延迟口径不可混用。

| 轮次 | fps | 输出到解码均值 | 输出到解码 P95 | 驱动到解码均值 | 驱动到解码 P95 | 画面年龄 P95 | 最大解码间隔 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| decode A | 20.18 | 48.2 ms | 98.4 ms | 80.9 ms | 130.6 ms | 156.2 ms | 390.2 ms |
| receive A | 22.76 | 42.1 ms | 98.5 ms | 74.8 ms | 130.9 ms | 148.5 ms | 150.1 ms |
| window2 A | 24.58 | 64.5 ms | 135.0 ms | 97.3 ms | 167.7 ms | 173.4 ms | 404.4 ms |
| window2 B | 24.44 | 69.9 ms | 132.0 ms | 102.6 ms | 164.7 ms | 167.6 ms | 114.2 ms |
| receive B | 25.62 | 32.7 ms | 90.3 ms | 65.4 ms | 123.8 ms | 144.6 ms | 281.9 ms |
| decode B | 25.15 | 40.5 ms | 77.9 ms | 73.2 ms | 111.2 ms | 124.6 ms | 433.4 ms |

时钟误差估计约 2.3 至 3.7 ms。共出现 14 次超过 200 ms 的相邻解码间隔，没有超过 1 秒的间隔；不是“完全无卡顿”。本轮仍未达到长时间稳定 28 至 30fps 的目标。

**选择：默认保持 decode，receive 保留为可选实验，window2 不作为低延迟默认。** receive 两轮均值更低，但 P95 并未稳定优于各自相邻的基线；window2 两轮延迟均明显较高。不同时间的 Wi-Fi、摄像头场景和原始 JPEG 大小未锁定，不能声称严格的因果提升或固定优化百分比。

分段结论：驱动到输出各轮均值约 33.2 ms；出队后验证/复制/发布均值约 0.004 至 0.010 ms；收到到解码均值约 3.0 至 3.9 ms。当前证据不支持为了省几微秒而重写共享内存采集路径。发送到收到的均值约 23.1 至 56.5 ms，这一段混合了排队、无线传输和浏览器调度，是更值得继续定位的方向。

被动监听记录到四次后台扫描，每次总历时约 3.67 至 3.68 秒，间隔约 304 秒。覆盖本次实验前后的 1080 次路由器 Ping 丢失 1 次，RTT 均值 18.720 ms、最大 158.148 ms；扫描持续时间不是连续断网时间，本次没有重现前一记录中的 3 秒级 Ping/视频停顿。不能用“发现扫描”解释全部 P95。数值摘要见 `flow-network.json`，完整私有日志保留在 Pi 的 `/home/eur/flow_timing_20261010/network/`。

原始逐帧数值已无损压缩为 `flow-raw/*.json.gz`，汇总为 `flow-summary.json`。使用 Node 18+ 可从原始数据重新计算：

```bash
node summarize-flow.cjs flow-raw/flow-decode-a.json.gz flow-raw/flow-receive-a.json.gz flow-raw/flow-window2-a.json.gz flow-raw/flow-window2-b.json.gz flow-raw/flow-receive-b.json.gz flow-raw/flow-decode-b.json.gz
```

脚本输出 JSON 到标准输出，不修改原始数据。场景并非静态标准测试图，PC 同期完成文档编辑和一次约半秒的本机单元测试，不能当作完全隔离的实验室基准。

## 验证

- 37 项 Node 单元/真实 WebSocket 集成测试通过，包括双帧额度边界、重复确认、非法窗口、VPF3 时间戳、旧 VPF2 兼容、JPEG 原样传递及有界解码队列。
- C++ 采集程序在 Pi 上成功编译并运行，驱动时间戳有实际有效样本。
- `ws -> webrtc -> ws -> webrtc -> webrtc -> ws` 与主动关闭 WS 后重连均通过，记录在 `flow-switch-check.json`。
- decode 与 receive 模式均完成实际浏览器检查：1280px、375px、320px 无横向溢出/数值裁切；视频 Canvas 像素非空，源尺寸 1280x720。
- 暂停收帧时年龄增长并高亮；旧画面仍到达时也高亮；校准失效后数值为空。receive 检查数值保存在 `flow-ui-check.json`，不发布现场截图。

## 部署与回退

当前 Pi 部署备份：`/home/eur/flow_timing_backup_20261010.gQYhG3`。采集原镜像保留为 `flow-capture-base:20261010`，新镜像为 `flow-capture:20261010`。源代码、Node 模块和页面已备份。

正常仓库部署沿用上一份记录的可选共享内存 Compose 配置：同步采集源码与整个 `signaling-server`，运行 `npm test`，重建采集容器并重启 `pi-signaling`。当前 gateway 不需重建；服务重启会触发网关已实现的会话恢复。

仅回退发送节奏时打开 `?transport=ws&flow=decode` 即可。完整回退本次代码时，人工核对备份路径后执行：

```bash
backup=/home/eur/flow_timing_backup_20261010.gQYhG3
sudo install -m 644 "$backup/capture.cpp" /home/eur/project_0321plus/video-processor/src/main.cpp
sudo install -m 644 "$backup/latest-source.js" "$backup/latest-session.js" /home/eur/signaling-server/
sudo cp -a "$backup/public/." /home/eur/signaling-server/public/
sudo systemctl restart pi-signaling
docker tag flow-capture-base:20261010 project_0321plus-video-processor:latest
docker compose -f /home/eur/project_0321plus/docker-compose.yml -f /home/eur/project_0321plus/docker-compose.latest.yml up -d --no-build --no-deps video-processor
```

回退不删除原文件、录像、卷或镜像。备份页面不会引用新增的 `latest-renderer.js`，闲置文件无需删除。单元测试也需随选定源码版本恢复，否则新测试可能对旧实现失败。

## 后续优先级

发送策略以重复测试为依据，不默认认为双帧窗口更快。当前先把视频变成可信的观测与测试组件；工业控制/状态系统见 [工业执行机构方案](../../industrial-roadmap/README.md)。原生 UDP、QUIC、FEC 均尚未实现，不能用名字代替对照实验。
