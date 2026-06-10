# Early YUYV + DCT + LZ4 Experiment

本目录归档树莓派上早期“YUYV/DCT/LZ4 变化域”方向的尝试代码。它早于当前主线的原生 MJPEG + 共享内存 + WebRTC DataChannel 方案，主要用于验证：从摄像头帧中提取亮度信息，将画面按 8x8 块做 DCT，仅保留少量低频系数，再用 LZ4 压缩后通过 UDP 发送。

这些文件来自树莓派 `192.168.3.19` 的 `/home/eur/project_0205` 和同阶段的顶层实验文件。它们是历史实验归档，不是当前推荐运行版本。

## 目录结构

```text
early_yuyv_dct_lz4/
├── core_project_0205/
│   ├── CMakeLists.txt
│   ├── FastCodec.h
│   └── sender_pi.cpp
└── related_experiments/
    ├── low_latency_udp.cpp
    ├── test_camera.cpp
    ├── test_frame.cpp
    └── test_UDP.cpp
```

## 核心实验

`core_project_0205` 是这一路线的主要代码：

- `sender_pi.cpp`：使用 OpenCV 打开摄像头，按 `640x480@60fps` 读取帧，提取亮度通道，按 8 行 strip 分块处理，并通过 UDP 发往 PC。
- `FastCodec.h`：对每个 8x8 亮度块执行 DCT，按 ZigZag 顺序保留前几个低频系数，转为 `int16_t` 后调用 `LZ4_compress_default`。
- `CMakeLists.txt`：依赖 OpenCV、OpenMP 和 liblz4。

当时的目标是用“变化域/频域低频系数”降低传输数据量，减少完整帧传输压力。现存代码更接近 `Y/luma + DCT + LZ4 + UDP` 的原型；它没有形成当前主线那样完整的 WebRTC 信令、DataChannel、共享内存和浏览器渲染链路。

## 相关小实验

`related_experiments` 保存同阶段探索文件：

- `test_camera.cpp`：V4L2 mmap 摄像头采集和帧大小验证。
- `test_UDP.cpp`：UDP 往返延迟和单向延迟估算。
- `test_frame.cpp`：最小帧差/变化像素计数实验。
- `low_latency_udp.cpp`：V4L2 MJPEG 采集、JPEG 解码灰度、帧差计算和 UDP 发送的低延迟草稿。

这些文件用于理解路线演进：先验证摄像头、UDP 和变化检测，再尝试把亮度块变换、低频保留和 LZ4 压缩组合起来。

## 构建方式

在树莓派上安装依赖：

```bash
sudo apt update
sudo apt install -y build-essential cmake pkg-config libopencv-dev liblz4-dev
```

构建核心实验：

```bash
cd early_yuyv_dct_lz4
cmake -S core_project_0205 -B build
cmake --build build -j
```

运行前需要检查 `core_project_0205/sender_pi.cpp` 里的硬编码目标地址：

```cpp
#define TARGET_IP "192.168.71.93"
#define TARGET_PORT 5000
```

如果当前 PC 或接收端 IP 不同，需要先改成实际地址。

## 已知限制

- 这是历史原型，保留原始思路和代码形态，没有按当前主线重构。
- 发送端只包含 UDP 发送逻辑，没有配套接收端重建和显示流程。
- `prev_gray` 等变化域草稿没有完整初始化/边界处理，不能直接当稳定产品代码使用。
- 当前仓库主线已经改为 USB 摄像头原生 MJPEG 直通、共享内存交接、WebRTC DataChannel 分片发送，优先保证端到端可运行和低延迟。
