# 2026-10-09 / 10 双通道低延迟实验

本次在保持 1280x720、30fps 采集、锐度 4 和摄像头原生 JPEG 的前提下，增加可选的 WebSocket 最新帧拉取通道。原 WebRTC DataChannel 保留为默认入口和对照，不把换协议等同于保证低延迟。

## 通信方式

```text
V4L2 native MJPEG -> latest-frame SHM
                       |                         |
                 C++ WebRTC gateway        Node LatestFrameSource
                       |                         |
               SCTP / DTLS / UDP        WebSocket / TCP, TCP_NODELAY
                       |                         |
                VPF2 JPEG fragments       VPF2 complete JPEG
                       |                         |
                       +---- browser decode -----+
```

新通道的区别不是把连续视频无限写进 TCP：

1. 浏览器发送 `{"type":"frame_request","after":0}` 获取第一帧。
2. 服务端读取最新 SHM 帧，复制前后核对完整 48 字节头部及写序号；正在写入、被覆盖或非法长度的帧不发送。
3. 原始 JPEG 前添加 24 字节 VPF2 头，一条二进制 WebSocket 消息携带一帧；不转 Base64，不解码重编码，不启用 permessage-deflate。
4. 浏览器 `img.decode()` 完成后，用该帧单调时钟发布时间作为 `after` 请求下一帧。
5. 服务端只接受与上次发送帧匹配的请求。每个连接最多一帧在途、一个合并的请求额度，不积累待发历史帧。采集仍为 30fps，网络慢时跳过采集帧而不降低单帧画质。
6. 摄像头暂时没有新帧时返回 `frame_wait`；读取失败返回 `frame_error`。客户端延时重试，断线后重新校准时钟并自动连接。被另一个 viewer 替换时不抢回连接。

浏览器通过顶部选择框切换：

- 新通道：`http://192.168.3.19:8080/?transport=ws`
- 原通道：`http://192.168.3.19:8080/?transport=webrtc`

两者仍是单 viewer。无参数入口继续使用 WebRTC；不会根据一次测试结果强制改变默认协议。

## 底层与恢复修复

- 沿用前一检查点的双 MMAP 采集缓冲、积压时取最新帧、网关 1ms 轮询和有界浏览器重组。
- 两个容器可通过可选 Compose 配置共享宿主机 RAM 路径 `/dev/shm/pi-video`，Node 直接读取同一份 JPEG。没有增加中间视频转发进程。
- RAM 目录由 systemd-tmpfiles 创建，权限 `0750 eur:eur`。Node 保持普通用户运行，不放开 Docker 私有目录权限。
- 修复采集进程重启后，延迟到达的旧帧污染新帧号序列的问题。
- 画面年龄超过 1 秒时，即使仍有旧帧陆续到达也高亮，不再只判断“是否收到数据”。
- 已使用的 WebRTC 会话不再向新 viewer 重发旧 offer。信令请求重建后，网关这个无状态工作进程退出，由 Compose 的 `restart: always` 回收旧 ICE/SCTP 会话并重新启动。信令断开、PeerConnection 失败/关闭也触发恢复；不重启采集容器。
- 上述网关恢复依赖进程监督器；脱离 Compose 手动运行时，退出后需要由调用方重启。它不是无中断切换，也不是进程内 ICE restart。

## 测量边界

延迟指 **JPEG 写入共享内存后的时间戳，到浏览器 `img.decode()` 完成**。它不含曝光、摄像头内部编码、驱动出队前等待和显示器刷新，不是光学端到端延迟，也不是 Ping。

Node 的 `process.hrtime` 和 C++ 的 `steady_clock` 在这台 Linux Pi 上对应同一单调时间域。浏览器使用四时间戳校准，在线取最近 30 秒最小 RTT，离线报告取整次测量的最小 RTT。误差估计为 RTT/2 + 1ms，不是精度保证。

必须同时观察平均/P95、实际解码 fps、画面年龄和最长解码间隔。拉取会跳过未发送的采集帧，不能只用成功解码帧的低延迟宣称完全没有卡顿。页面“丢弃”计数仅表示重组器丢弃，不等于网络丢包率或跳过的全部采集帧。

实测数据及汇总保存在同目录 JSON 文件，测试电脑记录的 `measuredAt` 为 UTC。所有实验均使用同一摄像头配置和 Wi-Fi，但非同时进行，不能视为受控的协议性能比较。

| 测试 | 时长 | 解码 fps | 平均 | P95 | 最差单帧 | 画面年龄 P95 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 10-09 原 WebRTC | 60s | 28.67 | 63.6ms | 343.0ms | 1055.3ms | 463.7ms |
| 10-09 WebSocket 首测 | 120s | 28.83 | 40.5ms | 63.3ms | 337.0ms | 87.7ms |
| 10-10 原 WebRTC 复测 | 120s | 26.79 | 244.6ms | 592.8ms | 1104.6ms | 653.6ms |
| 10-10 WebSocket 复测 | 180s | 23.49 | 42.8ms | 87.3ms | 151.9ms | 129.5ms |

新通道两次时钟误差估计分别约 2.9ms、2.4ms，均为 1280x720，无图像解码错误。后一轮最长相邻成功解码间隔 138.5ms，解码本身平均 3.5ms。后一轮同时做过一次短暂的服务单元测试和轻量 SSH 状态检查，并非完全隔离的负载环境。

**结果支持在这台设备上继续使用新通道测试，但不支持“稳定 40ms”或固定百分比提升的承诺。** 10-10 复测解码帧率从对照的 26.79 降至 23.49fps，说明单帧额度用较少的画面更新换取更短的排队；分辨率、采集 fps 和单帧 JPEG 质量没有降低。不能隐去这一取舍。

`summary.json` 汇总全部六轮；对应原始数据为 `baseline30.json`、`ws-first.json`、`rtc-followup.json`、`ws-followup.json`。未采用实验保存在 `lifetime80.json` 和 `pacing2000.json`：平均/P95 分别为 218.8/1121.0ms、288.2/2034.0ms，不能因其短时中位数较低而宣布成功。JSON 不包含摄像头图像、SSH 密码或访问令牌。

## 无线侧发现与未采用尝试

10 月 9 日的被动 `iw event` 记录捕捉到一次约 3.69 秒的全频段扫描；同时 Pi 到路由器 Ping 峰值约 3438ms。这说明至少一部分长尾与无线扫描同时发生，尚不能认定它是全部延迟的唯一原因。

曾用 NetworkManager checkpoint 保护临时 BSSID 固定试验，重连导致 SSH 中断，未确认 checkpoint，随后自动回退。已经核对 BSSID 配置为空，未留下锁定 AP 的配置。Wi-Fi 省电仍为关闭，未修改路由器或电脑无线驱动参数。

`maxPacketLifeTime=80ms` 和分片间隔 2ms 的 WebRTC 实验均出现较差长尾，未保留。后者期间捕捉到扫描，不能把退化全部归因于分片间隔。

## 部署

先同步仓库的 `signaling-server`、`project_0321plus` 代码到 Pi 对应目录，保留现有依赖安装和备份。以下启用可选宿主机共享内存路径，短暂重建两个容器：

```bash
sudo install -m 644 systemd/pi-video-shm.conf /etc/tmpfiles.d/pi-video-shm.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/pi-video-shm.conf
cd /home/eur/signaling-server
npm ci
npm test
sudo systemctl restart pi-signaling
cd /home/eur/project_0321plus
docker compose -f docker-compose.yml -f docker-compose.latest.yml up -d --build
```

默认读取 `/dev/shm/pi-video/video_shm`，可用服务环境变量 `VIDEO_SHM_PATH` 覆盖。tmpfiles 配置里的账号 `eur` 应与信令服务账号一致。不加载可选 Compose 文件时，原命名卷部署仍适用于 WebRTC；此时 WebSocket 视频通道没有数据源。

## 验证

- Node 单元和本机真实 WebSocket 集成测试共 29 项通过，覆盖时钟、完整帧读取、写入冲突、单帧额度、非法控制消息、源文件暂缺、过期 offer 和协议路由。测试需要 Node 18+，以及本机监听端口权限。
- 新网关 C++ 在 Pi 上编译成功；实际验证 `ws -> webrtc -> ws -> webrtc -> webrtc -> ws`，包括重复刷新，无需手动重启网关。
- 主动关闭 WebSocket 后自动重连成功，重新校准并继续显示帧。`transport-switch-check.json` 保存实际浏览器检查结果。
- Edge 桌面 1280px、375px 和 320px 截图检查无横向溢出或数值裁切。Canvas 检查源图像为 1280x720 且非空白，浏览器无异常、JPEG 无解码错误。
- 测试暂停收帧后画面年龄增长并高亮；仍有旧帧到达也高亮；时钟失效后延迟显示 `-- ms`。检查结果保存在 `ui-check.json`，摄像头截图只留本机，不纳入仓库。

## 回退

此次 Pi 文件备份为 `/home/eur/transport_backup_20261009.gjo051`，前一网关源码为 `/home/eur/latency_debug_20261009/gateway.before.cpp`，原网关镜像保留为 `latency-debug-gateway-base:20261009`。仅想换通道时无需回退文件，直接选 WebRTC。

完整回退到 10 月 8 日的网关、网页和原命名卷，人工执行：

```bash
backup=/home/eur/transport_backup_20261009.gjo051
sudo install -m 644 "$backup/server.js" /home/eur/signaling-server/server.js
sudo install -m 644 "$backup/index.html" "$backup/video-frames.js" /home/eur/signaling-server/public/
sudo install -m 644 /home/eur/latency_debug_20261009/gateway.before.cpp /home/eur/project_0321plus/webrtc-gateway/src/main.cpp
sudo systemctl restart pi-signaling
docker tag latency-debug-gateway-base:20261009 project_0321plus-webrtc-gateway:latest
docker compose -f /home/eur/project_0321plus/docker-compose.yml up -d --no-build
```

保留原命名卷和镜像；回退不删除数据，不改变 720p30 采集设置。新增模块和 tmpfiles 配置可闲置，不必删除。

## 限制与后续

- WebSocket 建立在 TCP 上，丢包重传仍可能阻塞当前帧。单帧额度限制历史队列，但不能消除无线失联或保证任何网络下都低延迟。[RFC 6455](https://www.rfc-editor.org/rfc/rfc6455.html)
- 这次比较的是本项目的“自定义 JPEG DataChannel”与“JPEG WebSocket 拉取”，不是宣称 WebSocket 普遍优于 WebRTC 的原生视频轨道。DataChannel 使用 SCTP/DTLS/UDP，含自己的拥塞和可靠性机制。[RFC 8831](https://www.rfc-editor.org/rfc/rfc8831.html)
- 当前 `http/ws` 只供可信局域网测试，新通道视频不是 DTLS 加密。不能直接暴露到公网；外网使用需另做认证、TLS 和访问控制。
- 后续可以将同样的最新帧策略放到原生 App 的 UDP 接收端，再比较过期丢弃与可选 FEC；本次没有实现 UDP App、QUIC/WebTransport 或可靠电机控制协议。
