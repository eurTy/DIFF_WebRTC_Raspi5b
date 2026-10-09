# 2026-10-08 低延迟优化检查点

按用户要求停止本日优化并保存。此检查点保留画质与分辨率，不宣称已解决弱网长尾延迟。未增加电机、AI 或其他硬件功能。

## 当前部署

- Raspberry Pi 5 + USB 摄像头 + PC Edge，双方通过同一个 5GHz 路由器连接。
- 原生 MJPEG，1280x720，摄像头请求及驱动实际帧率均为 30fps；之前为 10fps。
- 锐度保持 4，曝光仍为原来的自动模式，没有降低 JPEG 质量或添加二次编码。
- 10fps 和 30fps 样本的 JPEG 量化表 SHA-256 均为 `d039ee67638a1083f6d0f916186effa45e406da062ad5683dab8ebe8a9a7894f`。这证明样本量化表一致，不代表不同时间拍到的图像像素相同。
- 采集 MMAP 缓冲申请数 4 -> 2，积压时清掉旧缓冲，仅发布最新帧。共享内存 ABI 和计时起点保持不变。
- 网关取帧周期 10ms -> 1ms，整帧复制后验证写序号；应用层发送高水位 512KiB -> 128KiB。
- 默认 24KiB 视频分片，VPF2 包头包含发布时间。可靠控制通道保留逐帧开始/结束消息及四时间戳校准。
- SCTP 使用默认拥塞参数；视频保持 unordered + maxRetransmits=0。未保留小分片、短寿命和逐帧 ACK 的激进实验参数。
- 浏览器最多重组两个帧，120ms 后清理未完成帧，阻止较旧帧覆盖新帧；帧率按实际解码成功计数。
- 树莓派 Wi-Fi 省电关闭，并通过当前 NetworkManager 连接配置持久化。没有修改电脑网卡参数、路由器、Wi-Fi BSSID 或信道。

## 测量边界

下表延迟均为 **树莓派 JPEG 写入共享内存后，到浏览器 `img.decode()` 完成**，不是 Ping，也不是完整光学端到端延迟。未计入曝光、摄像头内部编码、驱动出队前等待或屏幕扫描输出。

两个单调时钟通过控制通道四时间戳校准。网页采用最近 30 秒最小 RTT；离线报告统一采用该次测量最小 RTT 偏移。误差列为 RTT/2 + 1ms 的估计，不是硬件精度保证。P95 为最近秩定义。测试使用真实摄像头数据和无头 Edge，只有一个 viewer。

| 测试 | 时长 | 解码帧率 | 平均延迟 | 中位延迟 | P95 | 时钟误差估计 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 早段原程序，仅关闭 Wi-Fi 省电 | 60s | 9.88 fps | 82.0ms | 69.1ms | 137.2ms | 3.4ms |
| 后段原程序复测，仍为 10fps | 60s | 9.13 fps | 383.7ms | 44.2ms | 2774.3ms | 3.7ms |
| 当前优化检查点，30fps 采集 | 120s | 20.74 fps | 246.8ms | 33.0ms | 1975.1ms | 2.9ms |

当前两分钟样本共 2489 个有效解码帧，JPEG 解码平均约 3.6ms；最长相邻成功解码间隔为 4118ms。画面年龄中位数约 66.4ms、P95 约 2862.8ms，卡顿时继续增长，不会因停止收帧而显示虚假的低延迟。

**中位 33ms 不等于稳定 33ms。** 不同轮次无线环境发生变化，时长也不同，上表不是受控 A/B 实验，不能把均值差简单归因于代码优化。早段较好的 10fps 结果也不能掩盖后段原程序同样发生卡顿的事实。

数据文件：

- `summary.json`：包括未采用实验在内的各轮统计；初始 baseline 在采样开始后重启过网关，不用于公平的帧率/停顿比较。
- `wifi-awake.json`：早段仅关闭 Wi-Fi 省电的帧时间、时钟样本及画面年龄记录。
- `baseline-recheck.json`：后段完整原程序对照复测。
- `native30.json`：当前检查点的两分钟原始计时数据。

上述文件没有摄像头图像、SSH 密码或账号令牌。时间为测试电脑记录的 UTC，显示时间可加 8 小时转换为北京时间。

## 尚未解决

1. 局域网探测 65 次、500ms 超时阈值：60 次回复、5 次超时；成功回复 1-406ms，平均 53ms。这不是视频延迟测量。
2. 电脑到路由器 30 次探测无丢包，1-8ms；树莓派到路由器另一段 30 次探测无丢包，但峰值约 885ms、平均 44ms。结果更指向树莓派无线侧抖动，但不足以确定是干扰、驱动、扫描还是路由器调度。
3. 小分片、过小发送窗口和激进 SCTP 超时实验出现过严重停顿，没有按其短时低中位数宣布成功。网络条件变化使各项参数的独立影响尚不能确定。
4. 尚未证明当前方案能长时间稳定低延迟。下一次应先解决/隔离无线抖动，再进行同条件重复测试；有线连接可作为排除无线影响的对照。
5. 信令目前仅支持单 viewer；刷新或换 viewer 后可能需要重启网关。自动测试结束后已释放测试浏览器，网关会重启等待正常访问。

本次不继续试验 Wi-Fi BSSID 绑定、驱动参数或新传输协议，避免在停止工作时留下未经验证的网络改动。

## 验证与复现

```bash
cd signaling-server
npm test
cd ../project_0321plus
docker compose build video-processor webrtc-gateway
docker compose up -d
```

同时将仓库 `signaling-server/public` 中三个网页文件同步到运行目录。浏览器只打开一个 `http://<pi-ip>:8080/`，硬刷新后等待至少三次时钟校准回复。观察当前延迟、画面年龄、近十秒均值/P95、解码 fps，不只观察某一瞬间的最小值。

依赖的采集和网关 C++ 程序已在树莓派构建成功；19 项 Node 单元测试通过。测试覆盖时钟校准、过期/负值防护、乱序/重复/丢失分片、旧帧保护、帧号回绕和采集进程重启。

## 运行环境回退

本次树莓派备份目录为 `/home/eur/latency_opt_20261008.0SfFRh`，其中 `*.before` 保存本次优化前文件。镜像保留为：

```text
latency-opt-gateway-base:20261008
latency-opt-capture-base:20261008
```

需要回到优化前 10fps 版本时，在树莓派执行下面命令；这是人工回退说明，不会自动执行：

```bash
backup=/home/eur/latency_opt_20261008.0SfFRh
sudo install -m 644 "$backup/docker-compose.yml.before" /home/eur/project_0321plus/docker-compose.yml
sudo install -m 644 "$backup/gateway.cpp.before" /home/eur/project_0321plus/webrtc-gateway/src/main.cpp
sudo install -m 644 "$backup/index.html.before" /home/eur/signaling-server/public/index.html
sudo install -m 644 "$backup/latency.js.before" /home/eur/signaling-server/public/latency.js
docker tag latency-opt-capture-base:20261008 project_0321plus-video-processor:latest
docker tag latency-opt-gateway-base:20261008 project_0321plus-webrtc-gateway:latest
docker compose -f /home/eur/project_0321plus/docker-compose.yml up -d --no-build
```

以上恢复运行镜像和网页；若要重新构建旧采集代码，应从本检查点前的 Git 提交恢复源码。Wi-Fi 省电属于独立设置；其原值为 NetworkManager 的 default，需回退时用当前连接名设置 `802-11-wireless.powersave 0`，并按原运行状态执行 `iw dev wlan0 set power_save on`。

参考：[NetworkManager 无线配置文档](https://networkmanager.dev/docs/api/latest/settings-802-11-wireless.html)中 powersave=2 表示禁用省电。
