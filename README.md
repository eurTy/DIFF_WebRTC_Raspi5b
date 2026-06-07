# DIFF WebRTC Raspi5B

树莓派 5 低延迟视频传输项目。当前版本已经完成从 USB 摄像头采集、共享内存传递、WebRTC DataChannel 建连、浏览器端 JPEG 重组显示的端到端联调，并进入第二阶段长时间运行优化。

当前版本定位：

- 树莓派端负责摄像头采集、帧差判断、WebRTC 网关和信令服务。
- PC 浏览器当前作为中间测试端。
- 后续手机端复用同一套浏览器 viewer 协议接入。
- 当前优化重点是控制码率、限制 DataChannel 缓冲堆积、减少日志和控制消息洪泛。

当前发布版本：

```text
Web client version: 20260607a
Target viewer: PC browser now, mobile browser next
Pi address in current test LAN: 192.168.3.19
```

## 技术路线

本项目没有使用传统 WebRTC 摄像头媒体轨道，而是采用自定义 JPEG 分片传输：

```text
USB 摄像头
  -> video-processor 容器
  -> MJPEG/JPEG 帧 + 帧差检测
  -> /dev/shm/video_shm 共享内存
  -> webrtc-gateway 容器
  -> WebRTC DataChannel
  -> 浏览器重组 JPEG
  -> 页面显示画面
```

主要组件：

- `video-processor`：OpenCV 读取 `/dev/video0`，检测画面变化，周期性生成关键帧，写入共享内存。
- `webrtc-gateway`：读取共享内存，创建 WebRTC PeerConnection，通过 `control` 和 `video` 两个 DataChannel 发送数据。
- `signaling-server`：Node.js + `ws`，提供网页和 WebSocket 信令转发。
- `public/index.html`：浏览器 viewer，处理 offer/answer、ICE、DataChannel、JPEG 分片重组和渲染。

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
├── systemd/
│   └── pi-signaling.service
└── README.md
```

`libdatachannel` 以源码副本放入仓库，原因是树莓派端网络不稳定时 Docker 构建不能依赖实时从 GitHub 拉取源码。

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

当前代码按 `1280x720`、`30fps`、`MJPG` 方向采集，实际发送由帧差、关键帧间隔和网关缓冲控制共同决定。

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

第二阶段优化已经开始：

- 降低 JPEG 质量到 70，减少局域网带宽和浏览器解码压力。
- 将应用层视频分片从 60KB 降到 24KB，降低 DataChannel 单包过大导致的抖动风险。
- `webrtc-gateway` 增加 `bufferedAmount()` 高水位保护，视频通道积压超过 512KB 时丢弃旧帧，优先保持实时性。
- 浏览器 footer 增加实时 `fps` 和 `Mbps` 统计，后续可以用它判断手机端是否稳定。
- 页面状态只在真实渲染后显示 `streaming <frame_id>`，避免 `skip_frame` 误导为断线。

本轮录屏暴露的问题不是“连接失败”，而是“端到端已经跑通后，长时间传输需要限码率和限缓冲”。因此当前优化方向不是重写架构，而是在既有架构上做流控。

## 关键协议

### 信令角色

网关连接：

```text
ws://127.0.0.1:8080/?role=gateway
```

浏览器连接：

```text
ws://<pi-ip>:8080/?role=viewer&v=20260607a
```

### DataChannel

```text
control: 可靠通道，发送 frame_start、frame_end、skip_frame。
video: 不可靠通道，发送 JPEG 二进制分片。
```

### 视频分片包头

每个 `video` DataChannel 消息前 16 字节为分片头：

```cpp
struct VideoFragmentHeader {
    uint32_t magic;
    uint32_t frame_id;
    uint16_t frag_idx;
    uint16_t frag_total;
    uint32_t payload_len;
};
```

浏览器端按 `frame_id` 聚合所有分片，收齐后生成 JPEG Blob 并显示到 `<img>`。

## 关键参数

### video-processor

当前发送策略保留帧差检测，同时加入最小关键帧间隔，避免运动画面下无限制发送：

```cpp
const size_t FRAGMENT_MAX_SIZE = 24 * 1024;
const int jpeg_quality = 70;
const int target_video_fps = 10;
const uint32_t keyframe_interval_frames = 60;
```

含义：

- 静止画面下约每 2 秒有周期关键帧刷新。
- 运动画面下最多按约 10fps 方向输出关键帧。
- JPEG 质量先降到 70，优先换取稳定性和手机端可用性。

### webrtc-gateway

网关读取共享内存后通过 WebRTC DataChannel 发送。当前加入发送端缓冲保护：

```cpp
const size_t FRAGMENT_MAX_SIZE = 24 * 1024;
const size_t VIDEO_BUFFER_HIGH_WATERMARK = 512 * 1024;
```

含义：

- 视频分片保持与 `video-processor` 一致。
- 如果 `video` DataChannel 已积压超过 512KB，当前帧会被丢弃。
- 该策略牺牲完整帧率，换取实时性，避免浏览器越看越延迟。

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

`video-processor` 当前使用帧号周期关键帧作为双保险：

```cpp
const uint32_t keyframe_interval_frames = 60;
bool is_periodic_keyframe = (frame_id <= 1) || (frame_id % keyframe_interval_frames == 0);
```

即使画面静止，也会约每 2 秒发送一次 JPEG 关键帧。

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

## 下一步计划

- 在 PC 端确认 `fps`、`Mbps` 和延迟稳定后，再切到手机浏览器。
- 手机端继续使用 `role=viewer`，不改变树莓派侧协议。
- 根据手机端实际表现继续调整 `jpeg_quality`、`target_video_fps`、`FRAGMENT_MAX_SIZE` 和 `VIDEO_BUFFER_HIGH_WATERMARK`。
- 增加丢帧/丢分片统计，区分“网关主动丢帧”和“浏览器缺分片未渲染”。
- 评估是否增加 WebSocket/JPEG fallback，用于非 WebRTC 环境下的诊断。
