# 树莓派视频与状态观测统一项目

本分支将现有树莓派采集、WebRTC 网关、网页信令和真实状态观测整理为同一个可校验项目。它保留上一版全部应用、实验数据和论文，不启动新控制任务，不覆盖原 main，不混入新的语义自适应研究代码。

## 入口

- [统一模块清单与核对方法](integration/README.md)
- [现有监测页面](http://192.168.3.19:8080/monitor/)，需要树莓派在线
- [观测服务与部署说明](monitoring/README.md)
- [实测结果及系统审计](docs/monitoring/2026-10-11/README.md)
- [上一版完整论文](docs/thesis/draft/thesis-draft.docx)
- [历史根说明](docs/history/original-root-readme.md)

## 一个项目四个模块

```text
USB camera -> native MJPEG capture -> shared memory
                                   |-> WebRTC gateway -> browser
                                   |-> latest-frame WebSocket -> browser
                                   |-> observer -> state / alarms / evidence
```

权威源码分别位于 `project_0321plus/video-processor`、`project_0321plus/webrtc-gateway`、`signaling-server` 和 `monitoring`。共同配置及部署路径由 `integration/project.json` 描述。

```bash
python project.py check
python project.py inventory --output integration/source-manifest.json
python project.py status
```

`project.py` 只读检查源码或现有接口，没有停止、覆盖、安装或删除操作。在树莓派上执行 `python3 project.py compare-runtime --home /home/eur` 可以逐文件检查现场部署，CRLF 换行差异不会被误判为源码变更。

## 版本边界

原版本为 `observer-v1-frozen-20261011`，提交 `d31ff54`。此整合版本位于 `release/observer-integrated-v1`，本机单独目录 `_observer_unified_v1`。早期 YUYV/DCT/LZ4 与模拟执行机构完整保留为历史材料，不是当前真实测量对象。

语义自适应系统从独立空目录和独立历史分支开始，使用独立容器、数据和端口。本分支没有语义编码器、实验限速器或新模型依赖。

## 当前边界

系统只适合可信局域网的只读实验。保留 720p 原生 MJPEG，但不能保证恒定接收帧率；Wi-Fi 秒级停顿未消除。软件时延估计不等于光学端到端测量。没有实物电机、PLC、真实产线或 AI 故障识别结论。

树莓派在本轮备份过程中由用户确认关机或更换网络；本地整合可继续，远端归档完整性和整合部署需要联网后复核。归档状态不能用历史在线结果替代。
