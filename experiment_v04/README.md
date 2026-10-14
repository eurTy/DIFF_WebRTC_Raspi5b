# v0.4 独立任务新鲜度实验

本目录新增独立服务和手机页面，不修改 v0.3 的 8088 页面、采集容器或旧服务。当前传输仍为 WebSocket/TCP；先验证“全景先显示、同帧重点块限时补齐、仅确认已显示块”的收益和失效边界，不把它宣传成已实现 WebRTC 或解决 Wi-Fi 秒级失联。技术依据与对照要求见 [技术路线](../docs/technical-route-v040.md)。

本地合成输入：

```bash
python -m experiment_v04.server --host 127.0.0.1 --port 8089
python -m unittest tests.test_experiment_v04 -v
```

树莓派上已有 `semantic-adaptive-v2:local` 基础镜像且旧采集共享路径存在时，独立构建：

```bash
SHM_READER_GID=$(stat -c %g /dev/shm/pi-video) docker compose -f compose.experiment.yml up -d --build
docker compose -f compose.experiment.yml ps
```

手机与树莓派连接同一可信 Wi-Fi 后访问 `http://<树莓派局域网地址>:8089/`。界面会显示帧龄、重点块完成次数和上次区域清晰显示时间；导出 JSON 只含数值轨迹，不含图像。停用本实验仅需 `docker compose -f compose.experiment.yml down`，不要对原项目执行 `down`。

该服务默认独占一个观看会话，不操作 `/dev/video0`，只读 `/dev/shm/pi-video`；容器为非 root、只读文件系统、无 capabilities。尚无身份认证或 HTTPS，不可映射到公网。真实摄像头质量和手机端光学总延迟尚未验收；本地合成输入成功不能替代实机结论。

2026-10-14 部署核对：树莓派独立目录 `/home/eur/semantic-v04-experiment`，8089 与原 8088 同时可访问且两个容器均为 `running healthy`、重启计数 0。电脑浏览器从 8089 收到 `LIVE_READ_ONLY_ADAPTER`，Canvas 非空，重点块计数增长；这是短时功能验证，**不是手机 Safari、长期稳定性或光学端到端延迟验收**。部署时曾因上传目录权限导致静态页 403，已通过实验镜像 `COPY --chown=10001:10001` 修复，旧容器未重建。
