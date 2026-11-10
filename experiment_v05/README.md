# v0.5 手机传输实验

2026-10-18 本地新增 [schema 8 测量导出](../docs/mobile-metrics-schema8.md)：记录端口、版本、会话配置、测试条件与分阶段时间。尚未部署，线上可用版保持不变；当前手机入口使用无参数根地址，在页面选择编码。

独立入口 `8090`，保留 `8089` 的 v0.4 和 `8088` 的 v0.3，不修改采集器，不占用摄像头设备，只读共享内存。仍是观察系统，没有设备控制功能。

## 当前结论

2026-10-15，用户在 iPhone 4G 下确认新入口能播放，画质和连续性改善；随后分别报告 JPEG 延迟约 80–240 ms、H.264 约 120–400 ms。这是用户观察范围，不是同步光学实测、统计分位数或所有移动网络的保证。H.264 目前不能宣称比 JPEG 更低延迟。建议低延迟观察使用 `http://<树莓派私有地址>:8090/?codec=jpeg`；H.264 保留作画质、连续性和带宽对照。页面选择参数后需点播放应用。

用户截图中的 JPEG 为 6.5–7 fps，H.264 为 9.5–13 fps，均未达到设置的 20 fps。截图 H.264 存在当前帧龄 449.1 ms、最近呈现延迟 149.1 ms 的情况，说明只看最后一帧的延迟会漏掉停顿。截图未放入仓库。

一次将 H.264 时间轴改为采集时间间隔的候选，通过了电脑解码与回归，但用户明确反馈“延迟表现更严重”。已回退到用户此前确认可用的镜像，不将候选记为成功。后续不能未经手机复核再次启用该修改。

## 技术变化

1. H.264 使用 PyAV/libx264、1280×720、YUV420P、baseline、无 B 帧、zerolatency、小 VBV、短 GOP；由 FFmpeg 生成逐帧 fragmented MP4。手机使用 ManagedMediaSource 或 MediaSource 和原生 video 解码，明确不是 WebRTC/RTP，也不是硬件编码。
2. WebSocket 接收确认与发送解耦：初始最多 4 帧在途，按反馈在 2–8 帧内调整；只读最新源帧，没有源端采集 FIFO。超过 1.8 秒未确认则释放会话重连。TCP 已进入传输栈的字节仍不能撤回，以上不是端到端时延保证。
3. 反馈先区分近 10 秒的最低确认耗时与额外排队，避免把稳定 4G RTT 直接误判为低带宽。最多积累 100 ms 未用字节信用，让 P 帧的空闲预算可供 I 帧使用；大帧仍可能瞬间超过信用，之后必须偿还发送时间。它不是物理链路带宽估计，也不是严格网络拥塞控制器。
4. H.264 的重点优先模式仅对背景预滤波，全部任务区域保留同一帧原始像素后再整体编码；低预算使用更低背景采样宽度。JPEG 对照每帧附带所有任务块，保持全景至少 640 像素宽，预算不足时降低实际发帧速度，不再降到 160 宽后放大冒充清晰 720p。
5. 不再用“先到的低清基底覆盖刚显示的旧重点块”拼出时空不一致画面。所有区域随完整一帧更新，区域帧龄不等于经验证的清晰度、检测准确率或实际工业任务可用性。
6. H.264 封装器实际会在下一输入帧到来时输出上一片段。元数据始终对应上一帧，不能拿新帧时间戳少算延迟。保留版本使用规则媒体时间轴，这在变速时仍有重新缓冲风险，是尚未解决的限制。
7. 浏览器有界接收队列、缓冲过大时追赶、超时重连；后台释放会话，回前台重新连接及校时。JPEG 解码完成后绘图，H.264 使用 requestVideoFrameCallback 对应呈现帧。两者计时端点仍不完全相同，不能当作严格 codec 延迟因果对照。

区域是用户定义的任务区域，不是 AI 自动识别。两条路线都刷新全部区域，因此当前版不利用 weight 做块调度。研究比较需要同时报告重点区域质量、背景质量、帧龄与实际码率，不能只看其中一项。

## 本地与树莓派

```sh
python -m pip install -r experiment_v05/requirements.txt
python -m experiment_v05.server --host 127.0.0.1 --port 8090
```

本地默认是合成输入，不是树莓派摄像头。Pi 部署目录 `/home/eur/semantic-v05-mobile`。`Dockerfile.mobile` 是独立研究工程镜像的扩展，新增固定版本 PyAV，不更改基础镜像。离线构建依赖 `wheelhouse/av-16.1.0-cp312-cp312-manylinux_2_28_aarch64.whl`，不提交 wheel。主机需已存在 `semantic-adaptive-v2:local`。

```sh
docker build --network=none -f Dockerfile.mobile -t semantic-mobile-v05:local .
SHM_READER_GID=1000 docker compose -f compose.mobile.yml up -d --no-build
```

GID 应以当前 `/dev/shm/pi-video` 的实际属组为准，不改原目录权限。新容器非 root、只读根文件系统、移除 capabilities、1.5 CPU 上限。用户态 Tailscale 接入只增加一个私有 TCP 入口，不改主机路由、DNS、Wi-Fi、防火墙或公网端口映射：

```sh
docker exec semantic-mobile-access-tunnel-1 tailscale serve --bg --tcp=8090 tcp://127.0.0.1:8090
```

现有 8089 Serve 规则仍保留。私有网并不意味着跨网 UDP 媒体已打通，本轮没有假装在 TCP Serve 下完成 WebRTC 媒体通道。

## 保留与回退

- 原可用 v0.4 镜像标签：`semantic-adaptive-v04:phone-working-20261015`。
- 原镜像归档：`/home/eur/version-archives/mobile-working-20261015/v04-image.tar`；实际部署代码副本在同目录 `deployed-v04`，与此前尚未部署的本地 v0.4 修改区分开。
- 手机确认可用的 v0.5 镜像：`semantic-mobile-v05:phone-before-timeline-20261015`。本轮回退后 `semantic-mobile-v05:local` 指向同一镜像 ID。
- 新目录只保存新实验代码，不把 Tailscale 设备身份卷、密码、一次性登录链接或现场图像写入 Git。

停止新实验可在新目录执行 `SHM_READER_GID=1000 docker compose -f compose.mobile.yml down`，并执行 `docker exec semantic-mobile-access-tunnel-1 tailscale serve --tcp=8090 off`。不要执行 Serve reset，不要停掉接入容器或旧采集器；8089 仍可使用。

## 复核

```sh
python -m unittest discover -s tests -v
node tests/mobile-v05.cjs http://127.0.0.1:8090 runtime/mobile-v05
python -m experiments.mobile_quality --frames 90 --output runtime/mobile-quality.json
python -m tools.mobile_probe --url http://192.168.3.19:8090 --seconds 45 --output runtime/mobile-phone.json
```

浏览器测试需要 Playwright 和 Edge，检查桌面及手机尺寸的真实解码、非空画面、暂停恢复、横向溢出。真实摄像头测试不保存截图。单元测试在未安装可选 PyAV 时跳过新实验测试，不阻断旧工程使用。

`/api/status` 的 `client_report` 是浏览器上报的数字，不是摄像头内容。采样必须同时检查 `sessions` 和 `client_report_age_ms`；陈旧报告和重复轮询不能当成新样本。保留版本的状态没有保存 codec 配置，统计时必须另外记录用户实际选择，不能仅凭页面默认值认定当前编码。

## 已知限制与下一步

- 4G 路径曾短暂直连，后来又出现东京中继，再次探测又直连。一次 37/45 ms 探测不能证明稳定直连；本轮没有强改路由器或暴露公网服务。
- H.264/MSE 的片段等待和浏览器缓冲仍有额外时延，保留版的时间轴与变速节奏仍需更完整的播放器实验，手机负面结果优先于电脑单测。
- JPEG 全景清晰度底线和全部任务块刷新增加了每帧体积，在 2400 kbps 下无法承诺 20 fps。不可在不说明画质代价的情况下悄悄降分辨率。
- 稳定低延迟的下一条研究路线是独立 WebRTC/RTP 媒体通道与可验证的 UDP 连通/中继条件；不能只把 HTTP 信令放进 Tailscale 就宣称媒体通道已经实现。
- 本轮不声称创新编码器、神经语义传输、工业控制安全性或跨地点恒定时延。

## 官方依据

- [Tailscale 连接类型](https://tailscale.com/docs/reference/connection-types)
- [Tailscale 私有 Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [WebKit ManagedMediaSource](https://webkit.org/blog/15036/how-to-use-media-source-extensions-with-airplay/)
- [FFmpeg MP4 分片选项](https://ffmpeg.org/ffmpeg-formats.html)
- [PyAV 编解码 API](https://pyav.org/docs/stable/api/codec.html)
