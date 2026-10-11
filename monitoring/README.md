# 设备远程运维观测原型

主入口：`http://192.168.3.19:8080/monitor/`。原视频入口 `/` 保留。

这是实际部署的只读诊断原型，不是电机控制器、PLC、安全联锁或产线验证结果。当前测量对象是树莓派采集节点：真实摄像头、Linux CPU/内存/温度、Wi-Fi 接口、视频发布时间。摄像头提供现场背景，不能替代光电开关、编码器或保护装置。没有接入实物电机，也没有把模拟数据标成实测。

## 功能与边界

- 1280x720 原生 MJPEG，不二次编码；保留 WebRTC 和最新帧 WebSocket。
- 三条逻辑链路：视频、只读主机状态、独立时钟探测。它们仍共享 Wi-Fi、Pi 和 Node 前端，不能声称物理冗余。
- 实时显示发布至解码估计、驱动至解码估计和画面年龄。没有光学测时设备，不能称为玻璃到玻璃延迟。最小 RTT 的余量不是经校准的扩展不确定度。
- 状态每秒采样。超过 2.5 秒未更新标记过期；缺测显示空，不显示为零。
- 四条诊断规则：采集停止、主机高温、高 CPU、弱 Wi-Fi。阈值是演示参数，不是设备安全限值。
- 事件保留触发时状态、最多 10 个之前样本、5 个之后样本、原生 JPEG 和 SHA256。事件与图片使用同一 Pi 单调时间关联，不是曝光同步。
- 最新 86400 条状态附近、500 条事件；图片最多 20 张且合计最多 64 MiB。数据库空闲页/WAL/文本仍占空间，因此不是 64 MiB 总磁盘配额。
- 页面支持事件筛选、明细、趋势和 JSON 导出。图像和数据库只留在树莓派，禁止提交 Git。

旧 `industrial_station` 软件往复模型保留，当前 Pi 的两个模拟服务已停用开机自启。不再将往复演示作为毕业设计主线。

## 部署

先备份现有目录、systemd 单元和采集 Docker 镜像。以下路径对应当前用户设备，不是通用安装器。首次创建虚拟环境可用 Python 3.12+；现有 Pi 使用 `/home/eur/industrial-lab/.venv`，aiohttp 3.14.4。

```bash
cd /home/eur/industrial-lab
.venv/bin/python -m pip install -r monitoring/requirements.txt
mkdir -p monitoring/runtime
sudo cp monitoring/pi-observer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-observer
```

将仓库整个 `signaling-server` 同步到 `/home/eur/signaling-server`，运行 `npm ci` 和 `npm test`。将 `systemd/pi-signaling-hardening.conf` 安装为 `/etc/systemd/system/pi-signaling.service.d/20-observer-hardening.conf`，再 daemon-reload/restart。其文件系统只读限制需配合目前静态资源布局，不应直接套用到会向工程目录写文件的其他服务。

采集主线沿用 `project_0321plus` 及 `docker-compose.latest.yml` 的共享内存挂载。更新采集源码时按原 Compose 文件重建 `video-processor`，不要改动 USB 摄像头分辨率、JPEG 质量或后台网络配置。默认 CMake Release 使用 `-O2 -DNDEBUG`；本次现场镜像使用相同 O2 优化，构建步骤见审计记录。

## 验收

```bash
cd /home/eur/industrial-lab
.venv/bin/python -m unittest discover -s monitoring/tests -v
curl -fsS http://127.0.0.1:8092/status
cd /home/eur/signaling-server
npm test
```

网页状态应为 GOOD，真实图像应为 1280x720，刷新后数值持续变化。单 viewer 是现有信令约束；打开另一个视频页会替换前一个，独立时钟连接不占 viewer 名额。

`tools/capture_fault_test.py` 是主动故障实验，会短暂停止视频。只能在非生产实验环境、没有真实控制任务时运行。它使用 `docker pause/unpause`，正常退出/异常会尝试恢复；若测试主机断电或强制杀死，手动 `docker unpause video-processor`。采集程序内部超时无法解冻被外部暂停的进程，这两种故障不要混淆。

## API

独立 Python API 只绑定 `127.0.0.1:8092`，Node 仅代理以下 GET 路由：

| 浏览器路径 | 内容 |
| --- | --- |
| `/observe/status` | 最新值、run_id、单调时间、质量、活动诊断 |
| `/observe/history?limit=300` | 最多 600 条历史值 |
| `/observe/events` | 最近 100 条事件摘要 |
| `/observe/events/1` | 单事件和前后上下文 |
| `/observe/snapshot/1` | 尚在保留范围内的 JPEG，否则 404 |

无写入/启停/驱动命令。当前 LAN 页面仍无身份认证和 HTTPS，不得映射公网或作为生产 OT 接入点。快照可能含现场隐私；JSON 导出包含节点状态。服务本地使用 UMask 0077，保存文件需限制访问。

## 诊断规则

| 规则 | 触发条件 | 恢复条件 |
| --- | --- | --- |
| CAPTURE_STALE | 连续 2 次采集质量非 GOOD | 连续 2 次 GOOD |
| HOST_HOT | 连续 3 次温度不低于 75°C | 连续 2 次低于 70°C |
| HOST_BUSY | 连续 5 次 CPU 不低于 90% | 连续 2 次低于 75% |
| WIFI_WEAK | 连续 5 次信号不高于 -70 dBm | 连续 2 次高于 -65 dBm |

实际规则以 `alarms.py` 为准。缺测不会把已触发的温度/CPU/Wi-Fi 告警恢复为正常；单次共享内存并发读失败受去抖处理。告警是规则命中，不是机器故障原因诊断。

## 回退

本次现场备份为 `/home/eur/observer_backup_20261011.e4VMZa`，旧镜像 `observe-capture-base:20261011`。新镜像 `observe-capture:20261011`。停用 `pi-observer` 即停止新采样，不影响原摄像头链路。回退信令先备份当前版本，再还原备份中的 server/public 等文件并重启 `pi-signaling`；不要删除证据数据库。回退采集时恢复旧源码和镜像标签，再用原两份 Compose 配置只重建采集服务。

没有改动 SSH、路由、Wi-Fi 连接、内核启动文件，也没有执行重启。发现 `cgroup_disable=memory`，当前 MemoryMax/容器内存限额不能宣称有效；页面明确显示内存隔离不可用。启用该控制器涉及重启和整机配置，应单独安排现场维护窗口。
