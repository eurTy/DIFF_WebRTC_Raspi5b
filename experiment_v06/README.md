# v0.6：手机 WebRTC/RTP 独立实验

日期：2026-10-15。目标是隔离检验 WebSocket/fMP4 之外的媒体传输方式，不替换可用的 v0.4、v0.5。新目录、镜像、Docker 网络和 Tailscale 身份均独立。这里只做远程观察，不执行设备控制。

## 入口与保留版本

- 实验：`http://100.91.218.90:8091/`，Tailscale 节点 `eur-svt-rtp`。
- 强制私有 TURN/TCP：`http://100.91.218.90:8091/?route=tcp`。
- 强制私有 TURN/UDP：`http://100.91.218.90:8091/?route=udp`。
- 原版：`http://100.80.123.16:8089/`。
- 已获用户认可的手机对照版：`http://100.80.123.16:8090/?codec=jpeg`。
- 本地合成测试：`http://127.0.0.1:8091/`。不能通过 SSH 转发网页就宣称同时转发了 UDP 媒体。

2026-10-18 起用户要求视频网页入口最多三个且端口固定：8089 原版、8090 手机可用版、8091 实验版。旧 8080 网页与 v0.3 的 8088 服务已停用；后续 WebRTC 实验复用 8091，不再开 8093 等新网页端口。独立镜像、容器和只读输入隔离仍保留。`127.0.0.1:8092` 仅提供内部诊断 API，不是用户实时视频网页。

以上远程地址只在用户自己的 tailnet 内可达。没有开启 Funnel、公共摄像头端口或公网 TURN；手机必须开 Tailscale，双方必须能访问互联网。每个实验入口仅允许一位观看者，第二位不会抢占第一位。

旧版镜像和归档继续保留：

| 对照 | 已运行镜像 ID | 本轮是否重启 |
| --- | --- | --- |
| 8089 | `6b8f79bf92b7774b806df9080fd3c8aa4b043ea67bc0f20c681b47b8d795341b` | 否 |
| 8090 | `86343a2291d9dd47d3e6e9676b3e9dbfafa73493af2fd3e882d561aa2dde30cf` | 否 |
| 原 Tailscale | `87c55d6be56ad85b3d8bceca5f87f73de288c609131460dd1be1874c250cc41d` | 否 |

旧归档位置：树莓派 `/home/eur/version-archives/mobile-working-20261015/`。本轮没有修改旧服务、摄像头所有者、主机路由或主机 sysctl。

## 技术路线

```text
原采集器 -> 只读共享内存 -> 最新完整帧 -> H.264 / 原生 1280x720
                                       |
                        aiortc RTP / RTCP / DTLS-SRTP
                                       |
                         独立 tailscale0 网络命名空间
                                       |
               ICE 直连，或私有 TURN（UDP / TCP 备用）
                                       |
                 iPhone Safari 原生 WebRTC 解码与呈现
                                       |
            rVFC + 精确 RTP 帧映射 + 校时 + getStats
```

原 Tailscale 容器是 userspace TCP 转发，不能假设其网页入口也会转发 RTP UDP。新 VPN 容器使用 `/dev/net/tun`，但只改变自己的 Docker 网络命名空间；不是在宿主机安装第二套网络服务。应用和 TURN 共享该命名空间，不拥有 NET_ADMIN。

1. 采集端只读共享帧，覆盖竞争最多重试三次；不建立 FIFO、不占用 `/dev/video0`。每次取最新帧，卡顿后不追发过期帧。
2. 原生 1280×720 编码，不先把背景缩到 640/320 像素。采用 libx264 veryfast、zerolatency、无 B 帧、无前视、两线程、一秒 GOP；CRF 20 与 VBV 上限约为参数预算的 88%。编码本身仍有损，不能承诺所有场景逐像素等质。
3. RTP 分包、RTCP、PLI/NACK、SRTP 使用 aiortc，不重写媒体协议。有效 RTP 负载上限 1100 字节，给 1280 MTU 的私有隧道留出协议头空间。
4. 不使用 MSE/fMP4，不以逐帧 TCP ACK 作为发下一帧条件。浏览器支持时请求 `jitterBufferTarget=0`，这是提示，不是强制零缓冲。
5. 接收带宽反馈不足时减少采样频率，保持分辨率及 CRF 配置不变。采样周期从读取前计时，不把 JPEG 解码耗时额外累加到周期。极差链路仍可能卡顿，这不是“任何带宽都保持 20 fps、同等画质”的保证。
6. 手机逐步发送 ICE 候选，不阻塞在全部网络探测完成上。实机 answer 通过 SDP 解析器只保留该节点可达的私有 IPv4，排除 Docker/IPv6 无效探测。TURN 使用一小时有效的 HMAC 凭据，服务端密钥只在树莓派 `.env` 和 `private/turnserver.conf`，不进入 Git。

初始手机尝试到达 `ICE checking` 后失败，未收到视频帧。这是实际负面结果，不是摄像头编码成功就可以忽略的错误。随后增加私有 TURN 备用。TURN 只监听新节点私有 IPv4，relay 端口 50000–50031，只允许中继到该节点；没有映射到宿主机公网端口。

## 延迟测量

`media.instrument_sender()` 是唯一使用 aiortc 私有字段的适配器，因此严格锁定 1.14.0。下一次编码调用开始时，前一帧 RTP 发送循环已经完成，此时读取随机起点偏移后的真实 RTP 时间戳，与该帧共享内存发布时刻配对。该映射通过无重传、无序数据通道发送，不参与媒体发送节拍。

浏览器只在 `requestVideoFrameCallback` 的 `rtpTimestamp` 与映射完全一致时计算：

```text
软件段延迟 = 浏览器预计呈现时刻 + 时钟偏移估计 - 共享帧发布时间
当前帧龄   = 当前浏览器时刻     + 时钟偏移估计 - 当前显示帧发布时间
```

若浏览器不提供 RTP 时间戳、元数据丢失或校时无效，显示 `--`，不能拿最近一次收包冒充当前显示帧。时钟采用往返最小样本与对称路径假设，非光学测量；曝光、摄像头内部缓冲、最终屏幕扫描不在这个起点内。

抖动缓冲是两次 `getStats` 间 `jitterBufferDelay` 增量除以 `jitterBufferEmittedCount` 增量；媒体 RTT 单独显示。TURN/TCP 内部也承载 RTP，但不能写成端到端 UDP。即使 ICE 显示 UDP，Tailscale 外层也可能走 DERP 中继，需另查路径。

目前重点框只是原有任务区域位置，整幅画面每帧同步更新；没有冒充“检测到某个目标”或“验证细节已经清晰”。本版先建立全质量传输基线，不将 WebRTC 本身作为论文原创贡献。

## 验证

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -q
node tests/rtc-v06.cjs http://127.0.0.1:8091 runtime/rtc-v06-final
```

浏览器测试依赖 Playwright 与本机 Edge。截图只保存合成测试源；真实摄像头测试仅记录数值。

- 全量回归 58 项通过；覆盖 H.264 即时可解码、1100 字节分包、原生尺寸、强制关键帧、最新帧跳跃、RTP 随机偏移与回绕、单会话释放、跨站拒绝、反馈突发、私有候选筛选和 VP8 优先 offer 的 H.264 协商。
- 桌面 Edge 合成源，390×844 / 1440×1000：均为 1280×720，零页面异常、零横向溢出；视频像素非空、播放与暂停恢复通过。采样修正后的软件段延迟 P50 约 46.7 / 41.8 ms，P95 54.8 / 49.7 ms；约 16–17 fps。这不是 iPhone、4G 或光学总延迟；前一轮数据也保留，不只挑较低数字。
- 树莓派同命名空间接收实际共享输入：严格 TURN/TCP、TURN/UDP 各接收并解码 35 帧，均为 1280×720，约 20.21 / 19.82 fps，确认 `selected_local_type=relay`。最早的中继测试实际选中了 host 候选，已作废其中继证明；严格探测禁用本机 host socket 绕过。这仍不代表手机路径可用。
- 手机初始 UDP 直连未通过；TURN 手机路径需要单独实测，不得从上述数字推算其收益。

2026-10-18 在同一 8091 入口加入分阶段连接诊断、25 秒无首帧超时、ICE 候选类型计数、候选错误码和服务端失败阶段；空字符串结束候选不再作为普通候选解析。修复仅部署到 8091，8089/8090 未重建。详见 `results/rtc-20261018/README.md`。树莓派本机 TURN/TCP 解码成功不等于手机 4G 成功；手机复测结果待记录。

数值记录放在 `results/rtc-20261015/`；没有活动观看者的轮询不能算延迟样本。比较前应关闭其他视频页，固定 4G、位置、2400 kbps/20 fps、原生 720p和相同场景，至少做三组交替对照。应同时报告软件延迟/帧龄 P50/P95、冻结率、实际帧率、分辨率、码率、重点区/背景 PSNR 和路径类型。

## 部署与回退

树莓派目录 `/home/eur/semantic-v06-rtc`。依赖离线 ARM64 wheel 放 `wheelhouse/rtc/`，`Dockerfile.rtc` 复用独立研究项目的 v0.3 基础镜像，不修改它。

```bash
SHM_READER_GID=1000 docker compose -f compose.rtc.yml up -d vpn
docker compose -f compose.rtc.yml exec vpn tailscale up --hostname=eur-svt-rtp --accept-dns=false --accept-routes=false
# 用同一账号添加新节点，然后获取它的私有 IPv4
docker compose -f compose.rtc.yml exec vpn tailscale ip -4
python3 experiment_v06/setup_turn.py --ip NEW_TAILNET_IP --gid 1000
docker compose --env-file .env -f compose.rtc.yml up -d --build viewer turn
docker compose --env-file .env -f compose.rtc.yml exec viewer python -m experiment_v06.smoke --relay tcp
```

配置生成器拒绝覆盖已有密钥，不打印密钥。`setup_turn.py` 的部署用户为 UID 1000，与 TURN 的只读配置权限对应。不将 `.env`、`private/`、镜像 tar 或摄像头图像提交。TURN 容器保留镜像二进制所需的 NET_BIND_SERVICE capability，但不具有 NET_ADMIN，也不使用 host network。

回退只需关闭新页面、打开旧 8090。需要停止实验时只执行本项目 `compose.rtc.yml stop`，不要停旧采集/旧 VPN；不要使用 `down -v` 删除新节点的持久身份。新 VPN 容器重建后，需要一起重建共享其命名空间的 viewer 和 turn。

本轮实验检查点镜像为 `semantic-rtc-v06:checkpoint-20261015`，镜像归档 `/home/eur/version-archives/mobile-working-20261015/v06-experimental-checkpoint.tar`。它不是手机验收通过的版本标签；原工作版归档未被覆盖。日志另存 `/home/eur/更新日志/2026-10-15-rtc-v06.md`。

## 下一阶段与论文边界

下一阶段先以手机证据决定是否采用该路线，不能在手机未通时继续宣称低延迟优化完成。再在相同编码器与同码率下加入任务变化、区域权重和区域帧龄联合决策；在编码前选取/跳过整帧，不任意丢弃已经编码的参考 P 帧。任务指标与时效指标同时约束，并与固定帧率、固定 ROI 和纯带宽自适应做消融。

仍未完成：真实 iPhone 4G 长时间验收、光学总延迟标定、跨路径自动切换、准确的工业任务真值、按 RTP 包期限处理的专门发送节拍器。固定 CRF/VBV 不保证在严重拥塞下严格满足网络容量；关键帧突发、蜂窝抖动、DERP 队头阻塞仍需研究。不得将这些缺口写成论文已实现功能。

## 依据

- [aiortc 1.14.0 RTP sender](https://github.com/aiortc/aiortc/blob/1.14.0/src/aiortc/rtcrtpsender.py)：编码、随机 RTP 起点、发送循环、RTCP 反馈实现。
- [aiortc 1.14.0 H.264](https://github.com/aiortc/aiortc/blob/1.14.0/src/aiortc/codecs/h264.py)：复用的 H.264 packetizer。
- [W3C WebRTC Statistics](https://www.w3.org/TR/webrtc-stats/)：RTT、抖动缓冲统计定义。
- [Tailscale Docker](https://tailscale.com/docs/features/containers/docker/docker-params)：userspace 与内核 TUN 配置边界。
- [Tailscale 连接类型](https://tailscale.com/docs/reference/connection-types)：直连与 DERP 不同。
- [Coturn 容器说明](https://github.com/coturn/coturn/blob/master/docker/coturn/README.md)：私有中继部署与端口配置。
