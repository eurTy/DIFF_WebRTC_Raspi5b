# 更新日志

## 2026-10-12 独立研究版验收

旧 main、旧应用目录和旧采集服务均保留。旧系统冻结于 `observer-v1-frozen-20261011`；整合版位于 `release/observer-integrated-v1`，部署目录为 `/home/eur/observer-unified-v1`，没有切换旧运行服务。

新研究版从独立 Git 根目录建立，使用官方 Python 基础镜像、独立依赖与容器，在 `/home/eur/semantic-adaptive-v2` 运行，仅映射 8088。非 root、只读文件系统、移除 capabilities，输入共享目录只读挂载；只为新版追加已有输入目录组，不修改旧权限。未改动主机内核、sysctl、Wi-Fi 配置、启动文件或旧服务。

实现四种传输策略、同帧背景与重点块、单帧在途确认、过期输入拒绝、独立四时间戳校时、帧龄显示、区域框选及数值导出。修复低速传输时关闭连接延迟释放，以及正常视频帧清除参数错误提示的问题。

增加五样本中值反馈并完成消融。真实输入试验未支持默认替换，最终默认仍为单次反馈，中值模式保留为实验选项。保留低性能样本、校时警告、失败记录和原始单次反馈标签，不选择性删改结果。

验收：本地及 Pi 各 17 项 Python 测试、2 项 JavaScript 测试通过；合成和真实输入分别通过 15 项浏览器检查。复核 1080 个 Pi 配对编码样本及 84 个网络测量单元。完成 30 页可编辑本科论文初稿，含 10 张表格、8 个原生公式和 2 幅图，已逐页渲染检查。

## 使用与回退

旧入口：`http://192.168.3.19:8080/monitor/`。新入口：`http://192.168.3.19:8088/`。地址随树莓派网络变化而变化，仅限可信局域网，不可直接暴露公网。

新版未设置自动开机启动。树莓派重启后，在旧采集正常运行的前提下执行：

```bash
cd /home/eur/semantic-adaptive-v2
SHM_READER_GID=$(stat -c %g /dev/shm/pi-video) docker compose -f compose.yml -f compose.live.yml up -d --no-build
```

停止新版，不影响旧服务：

```bash
docker compose -f /home/eur/semantic-adaptive-v2/compose.yml down
```

没有电机、PLC、GPIO 或工艺控制接口。软件延迟不是光学端到端延迟；合成标记保真度不是实际工件识别准确率。论文是有实测依据的研究初稿，仍需依据学校模板和导师要求修改，并补充真实工业任务验证。
