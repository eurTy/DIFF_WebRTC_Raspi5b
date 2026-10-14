# 摄像头断开后的有限恢复

2026-10-13 现场发现旧采集容器 `video-processor` 因 `VIDIOC_DQBUF: No such device` 退出。摄像头重新枚举为 `/dev/video0` 后，Docker 曾因设备不存在而失败的启动没有自行恢复。本次只启动了已退出的容器，没有修改旧程序、设备权限或旧 compose 文件。

`capture_guard.py` 默认只检查；`--apply` 才能启动容器。必须同时满足：指定容器、退出码 1、不是 OOM、原重启策略为 always、明确的 `/dev/video0` 不存在错误、原设备映射匹配、设备已作为字符设备重新出现。每个退出时间标识最多尝试三次。手动停止、其他错误和仍未接入摄像头均不触发。

## 安装范围

主机级 timer 每约 30 秒运行一次服务，服务以 `eur` 身份使用该用户原有的 Docker 权限。两个新 unit 位于 `/etc/systemd/system/semantic-capture-recovery.{service,timer}`，脚本和状态文件仅在新版目录。没有向视频容器挂载 Docker socket，没有修改已有 systemd unit，没有开启用户 linger。

```sh
python3 ops/test_capture_guard.py
python3 ops/capture_guard.py
sudo install -m 0644 ops/semantic-capture-recovery.service /etc/systemd/system/
sudo install -m 0644 ops/semantic-capture-recovery.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now semantic-capture-recovery.timer
systemctl status semantic-capture-recovery.timer --no-pager
```

本配置固定适配本机用户 `eur` 与 `/home/eur/semantic-adaptive-v2`。脚本单元测试覆盖触发与拒绝条件；现场检查确认健康采集不会触发。本轮没有人为拔插摄像头，不能宣称完整热插拔实测通过。

## 停用与回退

```sh
sudo systemctl disable --now semantic-capture-recovery.timer
```

这不会停止当前采集。新版软件回退可使用保留镜像 `semantic-adaptive-v2:before-v030-20261013` 与 `/home/eur/version-archives/semantic-before-v030-20261013.tgz`。不要把回退操作指向旧项目目录。摄像头仍无法读取时，先检查 USB 连接与 `docker logs video-processor`，不要反复重装或修改网络参数。
