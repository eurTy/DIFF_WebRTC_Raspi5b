# DIFF WebRTC Raspi5B

树莓派 5 低延迟视频传输项目。当前版本已经完成从 USB 摄像头原生 MJPEG 采集、共享内存传递、WebRTC / WebSocket 传输到浏览器 JPEG 显示的联调。主线扩展为面向测控专业的工业执行机构远程测试系统；已实现独立的软件模拟工位与 Modbus 网关原型，尚未接入真实电机或 PLC。

当前版本定位：

- 树莓派端负责摄像头原生 MJPEG 采集、WebRTC 网关和信令服务。
- PC 浏览器当前作为中间测试端和协议验证平台。
- 后续优先开发设备测试 HMI、模拟工位、Modbus 状态采集、报警记录与测试报告，原生手机 App 暂列远期选项。
- 视频与设备命令/状态分离；可靠传输不等于确定性实时控制，动作互锁必须在被控端执行。
- 当前优化保持 720p 原始 JPEG 质量，重点是有界队列、分段计时、发送节奏及 Wi-Fi 长尾评估。

当前发布版本：

```text
Web client version: 20260607b
Target viewer: PC browser now, mobile browser next
Pi address in current test LAN: 192.168.3.19
Industrial prototype: simulated Modbus station + independent gateway API
Next target: industrial actuator test HMI + video/event correlation
```

2026-10-08 保存点：保留 `1280x720` 原生 MJPEG 画质，部署参数为 `30fps`，增加实时延迟、画面年龄和有界重组；无线抖动造成的秒级卡顿尚未解决。完整数据、尝试记录、测量边界及回退方法见 [本日优化记录](docs/latency/2026-10-08/README.md)。此版本是实验检查点，不是稳定超低延迟发布版。

2026-10-09 / 10：新增可选 **WebSocket 最新帧拉取**通道，保持相同原生 JPEG，浏览器解码完成后再请求最新帧，限制旧帧积压。原 WebRTC 仍为默认入口；顶部选择框可切换，新增网关会话恢复。启用方法、实测边界、无线扫描发现与回退说明见 [双通道实验记录](docs/latency/2026-10-10/README.md)。

2026-10-10 后续：增加三种发送节奏与 VPF3 分段计时，详见 [发送节奏实验](docs/latency/2026-10-10/flow-control.md)。面向设备维护、自动化调试和测试验证的具体场景、设备预算、接口、验收与毕业设计摘要见 [工业执行机构方案](docs/industrial-roadmap/README.md)。

## 技术路线

2026-10-10 工业原型：新增 [industrial_station](industrial_station/README.md)，包括模拟往复机构、Modbus TCP、动作互锁、断线撤权、命令去重、故障注入、SQLite 审计与 JSON 报告。全部设备数据明确标注 SIMULATED，原视频模块不修改；新增 HTTP 8091 是 API 而非完整 HMI。[实施与验收记录](docs/industrial-roadmap/2026-10-10-implementation.md)：23 项新测试、37 项原视频回归通过，部署后 100 次模拟往复完成，原始数据可复算。

本项目没有使用传统 WebRTC 摄像头媒体轨道，而是采用自定义 JPEG 分片传输：

```text
USB 摄像头
  -> video-processor 容器
  -> V4L2 mmap 读取摄像头原生 MJPEG 压缩帧
  -> /dev/shm/video_shm 共享内存
  -> webrtc-gateway 容器
  -> WebRTC DataChannel
  -> 浏览器重组 JPEG
  -> 页面显示画面
```

主要组件：

- `video-processor`：V4L2 读取 `/dev/video0` 原生 MJPEG bytes，直接写入共享内存，不再做 OpenCV 解码和二次 JPEG 编码。
- `webrtc-gateway`：读取共享内存，创建 WebRTC PeerConnection，通过 `control` 和 `video` 两个 DataChannel 发送数据。
- `signaling-server`：Node.js + `ws`，提供网页和 WebSocket 信令转发。
- `public/index.html`：浏览器 viewer，处理 offer/answer、ICE、DataChannel、JPEG 分片重组和渲染。

## 早期手机 App 规划（远期选项）

以下保留早期移动接收端设想，不是当前实施承诺。近期优先完成工业测试 HMI 与可验证的测控流程；原生 App / UDP 仅在浏览器链路被证明是主要瓶颈且有明确使用需求后评估。

最终目标架构：

```text
Raspberry Pi 5
  -> UGREEN Camera native MJPEG
  -> video-processor latest-frame shared memory
  -> low-latency sender
       |                                  |
       | UDP unreliable video channel      | reliable control channel
       v                                  v
Mobile App
  -> latest-only JPEG reassembly/render    -> state/control/heartbeat/latency feedback
```

### 不确定性视频通道

视频通道以极低延迟为目标，允许丢包、丢帧和乱序，不做视频分片重传。

设计规则：

- 每帧都是独立 MJPEG/JPEG 图像，不依赖前后帧。
- 新 `frame_id` 到达时，App 端立即删除更旧的未完成帧。
- 缺分片超过短时间窗口后直接丢弃该帧，不等待、不补帧。
- 树莓派端发送队列有积压时直接丢旧帧，只发送最新帧。
- UDP 包大小目标控制在约 1200 bytes，避免 IP 层分片。

该通道的目标不是完整保存每一帧，而是让手机端永远尽可能显示最新画面。

### 可靠控制通道

可靠控制通道不承载主视频，只负责必须到达的控制与状态信息。

计划承载：

- start / stop / reconnect
- 心跳与在线状态
- 摄像头分辨率、帧率、曝光、增益、锐度参数
- 端到端 latency、fps、Mbps、drop rate 统计
- App 端反馈的网络拥塞状态
- 请求关键帧或请求降低码率

初期可使用 TCP 或 WebSocket，后续可以评估 QUIC。控制通道可靠，视频通道不可靠，两者职责必须分离。

### 与 H264/H265 的差异化

传统 H264/H265 侧重压缩率，通常依赖 GOP、参考帧、编码缓冲和解码缓冲；在弱网或拥塞时，旧帧可能继续排队，导致显示延迟累积。

本项目的差异化目标：

- 延迟优先，不追求最高压缩率。
- 每帧独立，旧帧无价值，可随时丢弃。
- 传输策略主动丢旧帧，而不是排队等待。
- App 端渲染 latest-only，不重传视频分片。
- 后续扩展 `DIFF-MJPEG / ROI tile`，静态背景低频整帧，运动区域高频局部更新。

因此本项目不是复刻传统视频编码链路，而是面向低延迟视觉状态同步的传输方案。

## 仓库结构

```text
.
├── project_0321plus/
│   ├── docker-compose.yml
│   ├── video-processor/
│   │   ├── Dockerfile
│   │   ├── CMakeLists.txt
│   │   └── src/main.cpp
│   └── webrtc-gateway/
│       ├── Dockerfile
│       ├── CMakeLists.txt
│       ├── libdatachannel/
│       └── src/main.cpp
├── signaling-server/
│   ├── package.json
│   ├── package-lock.json
│   ├── server.js
│   └── public/index.html
├── early_yuyv_dct_lz4/
│   ├── README.md
│   ├── core_project_0205/
│   └── related_experiments/
├── systemd/
│   └── pi-signaling.service
└── README.md
```

`libdatachannel` 以源码副本放入仓库，原因是树莓派端网络不稳定时 Docker 构建不能依赖实时从 GitHub 拉取源码。

`early_yuyv_dct_lz4` 是早期“YUYV/DCT/LZ4 变化域”路线归档，用来保留频域低频系数压缩和 UDP 发送的原型代码，不属于当前主线运行链路。

## 当前实测环境

当前联调环境：

```text
PC: 192.168.3.3
Raspberry Pi: 192.168.3.19
Web/signaling port: 8080
Camera: /dev/video0
```

浏览器访问：

```text
http://192.168.3.19:8080/?t=1
```

摄像头实测支持：

```text
MJPG 1920x1080 30fps
MJPG 1280x720 30fps
MJPG 640x480 30fps
```

当前 Compose 按 `1280x720`、`30fps`、`MJPG` 采集，保留摄像头自身 MJPEG 压缩数据，不做二次编码。实际浏览器帧率受网络、网关流控及不完整帧丢弃影响，不保证达到 30fps；采集程序未配置环境变量时仍回退到 10fps。

## 部署步骤

### 1. 安装信令服务依赖

```bash
cd /home/eur/signaling-server
npm install
```

如果是从本仓库部署到树莓派，建议目录保持为：

```text
/home/eur/signaling-server
/home/eur/project_0321plus
```

### 2. 安装 systemd 信令服务

```bash
sudo install -m 0644 systemd/pi-signaling.service /etc/systemd/system/pi-signaling.service
sudo systemctl daemon-reload
sudo systemctl enable --now pi-signaling.service
```

检查状态：

```bash
systemctl status pi-signaling.service
ss -ltnp | grep ':8080'
curl -i http://127.0.0.1:8080/?t=1
```

### 3. 构建并启动视频容器

```bash
cd /home/eur/project_0321plus
docker compose build
docker compose up -d
```

检查容器：

```bash
docker compose ps
docker compose logs --tail=80 video-processor
docker compose logs --tail=80 webrtc-gateway
```

## 当前运行状态

第一版已经完成：

- 摄像头 `/dev/video0` 可用。
- `video-processor` 可以采集并写共享内存。
- `webrtc-gateway` 可以连接信令服务器并发送 offer/ICE。
- 浏览器可以连接信令服务器并返回 answer/ICE。
- 已处理浏览器 `.local` mDNS candidate 导致 ICE 卡在 `checking` 的问题。
- WebRTC DataChannel 已跑通。
- 浏览器端已能显示持续更新的摄像头画面。
- 长时间运行时的 `skip_frame` 和日志刷屏问题已限流。

录屏分析结论：

```text
画面持续显示，frames 和 KB 持续增长。
右上角长期显示 skip 不是断线，而是跳帧控制消息覆盖页面状态。
已改为成功渲染后显示 streaming <frame_id>。
```

第二阶段第一轮历史改动如下，10fps / 512KB 为当时参数；当前保存点参数见下文：

- `video-processor` 从 OpenCV `VideoCapture + imencode` 改为 V4L2 `mmap` 原生 MJPEG 读取。
- 移除树莓派端二次 JPEG 压缩，减少 CPU、延迟和重压缩画质损失。
- 默认采集参数改为 `1280x720@10fps MJPG`，更适合后续手机端先跑通。
- 将应用层视频分片从 60KB 降到 24KB，降低 DataChannel 单包过大导致的抖动风险。
- `webrtc-gateway` 增加 `bufferedAmount()` 高水位保护，视频通道积压超过 512KB 时丢弃旧帧，优先保持实时性。
- 共享内存增加 `write_idx` 写入序号语义，网关避免读取半写入帧，降低花屏/破帧概率。
- 浏览器 footer 增加实时 `fps` 和 `Mbps` 统计，后续可以用它判断手机端是否稳定。
- 页面状态只在真实渲染后显示 `streaming <frame_id>`，避免 `skip_frame` 误导为断线。

本轮录屏暴露的问题不是“连接失败”，而是“端到端已经跑通后，长时间传输需要限码率、限缓冲和避免重压缩”。因此当前优化方向不是重写架构，而是在既有架构上改为摄像头 MJPEG 直通。

## 关键协议

### 信令角色

网关连接：

```text
ws://127.0.0.1:8080/?role=gateway
```

浏览器连接：

```text
ws://<pi-ip>:8080/?role=viewer&v=20260607b
```

### DataChannel

```text
control: 可靠通道，发送 frame_start、frame_end、skip_frame 和时钟校准消息。
video: 不可靠通道，发送 JPEG 二进制分片。
```

### 视频分片包头

当前 VPF2 的每个 `video` DataChannel 消息前 24 字节为分片头，所有整数采用网络字节序。浏览器仍能接收旧版 VPF1 的 16 字节包头：

```cpp
struct VideoFragmentHeader {
    uint32_t magic;
    uint32_t frame_id;
    uint16_t frag_idx;
    uint16_t frag_total;
    uint32_t payload_len;
    uint32_t published_hi;
    uint32_t published_lo;
};
```

VPF2 magic 为 `0x56504632`。`published_hi * 2^32 + published_lo` 是共享内存发布时间（毫秒）。时间戳与图像分片同行，不必等待可靠通道元数据才能计算延迟。浏览器按 `frame_id` 重组，最多保留两个未完成帧，超过 120ms 丢弃；收齐后生成 JPEG Blob 并显示到 `<img>`，拒绝旧帧覆盖新帧。仍保留原来的逐帧控制消息。

## 关键参数

### video-processor

当前发送策略直接读取摄像头原生 MJPEG 压缩帧：

```cpp
const size_t FRAGMENT_MAX_SIZE = 24 * 1024;
CAMERA_WIDTH=1280
CAMERA_HEIGHT=720
CAMERA_FPS=30
CAMERA_BUFFERS=2
CAMERA_SHARPNESS=4
```

含义：

- 摄像头自己输出 MJPEG，不再由树莓派二次 JPEG 编码。
- 当前部署为 30fps，驱动确认接受；MMAP 申请两个缓冲区，出队积压时仅发布最新帧。
- 未调低分辨率、锐度或 JPEG 量化质量。10fps 与 30fps 实测 JPEG 量化表哈希一致。
- `CAMERA_SHARPNESS=4` 比摄像头默认值 5 略低，用于减少边缘过锐和锯齿感。
- 如需锁定曝光，可通过环境变量增加 `CAMERA_EXPOSURE`、`CAMERA_GAIN`、`CAMERA_BACKLIGHT`。

### webrtc-gateway

网关读取共享内存后通过 WebRTC DataChannel 发送。当前加入发送端缓冲保护：

```cpp
const size_t FRAGMENT_MAX_SIZE = 24 * 1024;
const size_t VIDEO_BUFFER_HIGH_WATERMARK = 128 * 1024;
```

含义：

- 默认视频分片为 24KiB，可通过 `VIDEO_FRAGMENT_BYTES` 指定 512 至 32768 bytes。共享内存中的分片字段不再决定网络分片数。
- 网关轮询周期从 10ms 缩短至 1ms，复制整帧后复核写入序号，避免读到半写入图像。
- 应用层待发送量超过 128KiB 时暂不发送，恢复时重新读取最新共享内存帧；发布时间已超过 100ms 的帧不再发送。
- 该阈值只限制应用层可见排队，不能清空 SCTP 内部队列，也不是端到端延迟上限。当前无线环境仍有秒级停顿。

### browser viewer

浏览器端显示三类运行指标：

```text
frames: 已成功渲染帧数
fps: 最近约 1 秒成功渲染帧率
Mbps: 最近约 1 秒浏览器收到并渲染的 JPEG 数据码率
KB: 累计渲染数据量
```

如果页面能显示 `streaming <frame_id>` 且 `frames` 持续增加，说明 WebRTC 链路和 JPEG 重组是通的。

## 已解决问题

### 1. 直接打开 JS 文件

错误方式：

```text
file:///.../serverchange1.0.js
```

正确方式：

```text
http://<pi-ip>:8080/?t=1
```

### 2. `/?t=1` 返回 Not found

原因是 HTTP 静态路由没有正确处理带 query 的根路径。当前 `server.js` 已先去掉 query，再把 `/` 映射到 `index.html`。

### 3. 信令服务掉线

早期通过临时 `node server.js` 启动，容易因会话退出导致 8080 消失。当前改为 `pi-signaling.service`，并设置开机自启。

### 4. 浏览器 ICE candidate 为 `.local`

Chrome/Edge 会把局域网 IP 隐藏为 mDNS `.local` 地址，树莓派端 libnice 不一定能解析。当前信令服务器会在转发 viewer candidate 前，将 `.local` 地址替换为 WebSocket 连接看到的真实 PC IPv4。

### 5. 静止画面长期只有跳帧

旧版本 `video-processor` 使用帧差检测和周期关键帧：

```cpp
const uint32_t keyframe_interval_frames = 60;
bool is_periodic_keyframe = (frame_id <= 1) || (frame_id % keyframe_interval_frames == 0);
```

当前原生 MJPEG 版本不再做帧差检测，而是让摄像头按 `CAMERA_FPS` 直接输出压缩帧。这样减少 CPU 和重压缩损失，但会牺牲静止画面下的极限省流量能力。

### 6. 长时间运行日志和 skip 控制消息洪泛

当前 `webrtc-gateway` 将跳帧控制消息作为低频心跳发送：

```cpp
const auto skip_control_interval = std::chrono::milliseconds(1000);
const uint32_t skip_log_interval_frames = 60;
```

效果：

```text
skip_frame 不再每帧发送。
跳帧日志不再每帧打印。
页面正常渲染后显示 streaming <frame_id>。
```

## 常用排查命令

检查信令：

```bash
systemctl status pi-signaling.service
journalctl -u pi-signaling.service -n 100 --no-pager
curl -i http://127.0.0.1:8080/?t=1
```

检查容器：

```bash
cd /home/eur/project_0321plus
docker compose ps
docker compose logs --tail=100 video-processor
docker compose logs --tail=100 webrtc-gateway
```

检查摄像头：

```bash
ls -l /dev/video*
v4l2-ctl -d /dev/video0 --list-formats-ext
```

重启：

```bash
sudo systemctl restart pi-signaling.service
cd /home/eur/project_0321plus
docker compose restart webrtc-gateway video-processor
```

## 网页实时延迟

网页底部显示当前帧延迟、画面年龄、近 10 秒平均值、P95 和时钟误差估计。

- 测量起点是采集程序将 JPEG 写入共享内存时的 `steady_clock` 毫秒时间，字段名沿用 `last_feedback_time`；它不是摄像头曝光时间。
- 网关通过 VPF2 包头和 `frame_start.published_steady_ms` 发送该时间。浏览器在当前 JPEG 的 `img.decode()` 完成后记录终点，按帧 ID 匹配，兼容旧版元数据晚于视频到达的情况。
- `control` DataChannel 上的四时间戳请求/回复估算两个单调时钟的偏移。使用最近 30 秒内 RTT 最小的有效样本，启动时加密探测，随后每秒一次。
- 时钟误差显示为最小有效 RTT 的一半加 1 ms 时间戳量化误差的估计；链路不对称和时钟漂移仍可能影响结果，不是硬件精度保证。
- 画面年龄在没有新帧时继续增长；平均和 P95 只计算最近 10 秒解码成功且有时间戳的帧。解码失败的帧不计入延迟样本。
- 尚未取得三次校准回复、校准超过 10 秒未更新或连接断开时，不显示虚假的延迟数值。
- 不包含摄像头曝光/读出、内部 MJPEG 编码、V4L2 出队前等待及显示器刷新，因此不能称为完整的光学端到端延迟。

实时延迟本身不改变共享内存 ABI。要完整部署本次采集及网关优化，需同步网页的 `index.html`、`latency.js`、`video-frames.js`、测试文件及 Compose 配置，并重建两个容器：

```bash
cd /home/eur/project_0321plus
docker compose build video-processor webrtc-gateway
docker compose up -d
cd /home/eur/signaling-server
npm test
```

## 下一步计划

- 完成同画质发送节奏对照，不用降低分辨率或二次压缩掩盖排队问题。
- 建立明确标注模拟数据的往复执行机构工位，冻结状态机、I/O 与 Modbus 寄存器定义。
- 增加独立设备网关，实现命令生命周期、互锁、通信过期、事件日志及报告。
- 在测试 HMI 关联状态事件与视频时间戳；补齐鉴权、操作权限和断线恢复验收。
- 获取真实设备后补做接线、测量标定和现场验证；模拟结果不能替代实物验证。
- 原生 UDP 接收端、差分图块与离线 AI 分析保留为后续选项，不阻塞上述主线。
