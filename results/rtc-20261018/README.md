# 8091 手机连接诊断（2026-10-18）

## 范围

只修改 8091 实验 viewer 的 `experiment_v06/server.py` 和 `experiment_v06/web/app.js`。8089 原版、8090 手机可用版及摄像头采集进程未重建。固定视频网页端口仍只有 8089、8090、8091；8092 为 loopback-only 诊断 API。

故障现象：iPhone Safari 经 4G + Tailscale 打开 8091 `?route=tcp` 后页面停在“连接中”、黑屏、0 fps。此前本机 TCP relay 测试通过，但无法判断手机卡在 ICE 配置、offer、信令还是媒体建链。

## 本轮变更

- 浏览器记录连接阶段 `ice-config`、`create-offer`、`local-description`、`signal-opening`、`offer-sent`、`answer-received`、`remote-description`、`remote-track`、`first-frame`，同时汇报 ICE/连接状态、host/srflx/relay 数量及 ICE 错误码。不上传候选地址、TURN 凭据或摄像头图像。
- 无首帧超过 25 秒、ICE 配置/offer/远端描述等步骤超时时显示具体阶段；上次错误保留到首次显示视频帧，不会被自动重连立即擦掉。
- 服务端保留最近 40 条阶段记录与最后失败阶段，修正浏览器以空字符串表示结束候选时的解析路径。

## 已完成验证

- Python `tests.test_rtc_v06`：10 项通过，包括空字符串 ICE 候选和失败诊断保留。
- Edge 合成视频源：390×844 和 1440×1000 两种视口均连接、解码和暂停恢复成功，1280×720，页面异常 0、横向溢出 0；帧数 232/227，软件段延迟 P50 约 35.6/36.2 ms，P95 约 41.3/42.7 ms。仅为本机合成测试，不可与 iPhone 4G 的光学或软件延迟直接比较。合成源截图在 `runtime/rtc-v06-diagnostics/`，不提交真实摄像头画面。
- Pi 端 8089、8090 HTTP 200；监听的视频端口仅 8089、8090、8091。8091 `/api/status` 显示新增诊断字段。
- Pi 同网络命名空间严格 TURN/TCP：解码 35 帧，1280×720，约 20.29 fps；所选本地候选为 `relay`。这只验证该命名空间内的 TURN 与 RTP 路径，不能证明手机外网路径。
- 全项目 `unittest discover` 本轮运行超过两分钟未结束，已中断；不将其记为通过。聚焦的 v0.6 单测与浏览器测试均通过。

## 手机实测结论

用户在 iPhone 4G + Tailscale 上打开强制 TURN/TCP 页面后仍黑屏，并要求换一种方法。服务端收到本次 Safari 诊断：`offer-sent`、`answer-received`、`remote-track`、`remote-description` 均到达；最后观察到 `ice=gathering`、`ice_connection=new`，服务端会话一度处于 `connection=connecting`、`ice=checking`，手机侧 host/srflx/relay 计数均为 0。用户关闭页面后会话释放，未留下首帧或有效延迟样本。

这表明 HTTP/WebSocket 信令和 SDP 交换可用，但强制 TURN/TCP 的手机 ICE 媒体通路未建立。没有候选不等于已证明单一根因；可能涉及手机 Safari、Tailscale 路由、TURN 分配或其组合。不能将树莓派同命名空间的 relay 成功外推到手机 4G。

下一条路线：复用固定 8091 端口做无 ICE 的 WebSocket 传输实验，并与原封不动的 8090 作交替对照。优先验证独立帧 JPEG 的最新帧覆盖/截止期限调度（无需 HTTPS，能快速获得手机可靠基线），再评估 H.264 + WebCodecs 直接解码；后者按 WebCodecs 规范要求安全上下文，现有私有 IP 的 HTTP 页面不满足，必须先建立尾网内 HTTPS 并检测 Safari 支持，不能直接宣布可用。两种方案都不应承诺有限 4G 上 720p、20 fps、固定画质和极低延迟同时成立。

## 复测方法与边界

手机关闭其他视频页，保持 4G + Tailscale，在 Safari 打开 `http://100.91.218.90:8091/?route=tcp` 约 30 秒。记录是否有视频、显示阶段、ICE 状态和服务端 `/api/status` 的 `diagnostic_history`、`last_server_failure`、`remote_candidates`。本次已得到上述失败证据，不要求用户重复测试同一个黑屏入口。如日后重新启用 WebRTC，可按阶段判断：

| 最后阶段 | 优先排查 |
| --- | --- |
| `ice-config` / `create-offer` / `local-description` | Safari API、ICE 配置读取和本地候选分配 |
| `signal-opening` / `offer-sent` | WebSocket、服务端 answer、会话占用 |
| `answer-received` / `remote-description` | SDP 兼容性与候选解析 |
| `remote-track` / ICE `checking` | 4G/Tailscale/TURN 路径与媒体流 |

本次手机确认 8091 未打通；不声称低于 8090 的延迟。

参考：[WebCodecs 的 `VideoDecoder` 安全上下文要求](https://www.w3.org/TR/webcodecs/)、[WebKit 的 Safari 视频 WebCodecs 支持说明](https://webkit.org/blog/13966/webkit-features-in-safari-16-4/)、[Tailscale Serve 的尾网内 HTTPS 条件](https://tailscale.com/docs/features/tailscale-serve)。
