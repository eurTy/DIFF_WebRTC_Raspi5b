# 工业执行机构模拟测试模块

状态：2026-10-10 已实现可运行的软件原型。**全部动作、限位、位置和故障均为 SIMULATED；没有连接真实电机、PLC、传感器或 GPIO。** 电流为 `null`，位置为数学模型，不是编码器测量。新增硬件成本为 0。

本阶段交付模拟工位、Modbus 网关、命令行演示、事件审计和 JSON 报告。完整 HMI、报警视频快照、真实 I/O 尚未实现。原视频程序和 720p 参数不改动。

## 1. 部署结构

```text
PC CLI -- HTTP / Wi-Fi --> Pi gateway :8091
                              | Modbus TCP / loopback
                         Pi simulator 127.0.0.1:5020

PC browser <-- existing video --> Pi video :8080
```

为便于常驻演示，先将两个独立进程放在树莓派，而非原方案中的 PC 模拟器。Modbus 这一段是本机回环，不能声称已验证跨机 Modbus 的 Wi-Fi 性能。模拟器可单独在 PC 运行，网关通过 `--device-host` 改地址；跨机时须加访问控制，不能直接暴露公网。

采用 [PyModbus 官方服务端](https://pymodbus.readthedocs.io/en/stable/source/server.html)及客户端，不自行实现协议编解码。依赖固定为 `pymodbus==3.15.0`、`aiohttp==3.14.4`。

Pi 服务：`pi-industrial-simulator`、`pi-industrial-gateway`。代码路径 `/home/eur/industrial-lab`。`http://192.168.3.19:8091/health` 是健康 JSON，**不是操作界面**；视频仍在 `http://192.168.3.19:8080/`。

## 2. 电脑演示

在仓库根目录打开 PowerShell。本机已配置被 Git 忽略的 `industrial_station/runtime/access.json`，不要上传或截图其中令牌。

```powershell
.\industrial_station\lab.ps1 status
.\industrial_station\lab.ps1 command ARM
.\industrial_station\lab.ps1 command MODE --parameter 1
.\industrial_station\lab.ps1 command EXTEND
.\industrial_station\lab.ps1 command RETRACT
.\industrial_station\lab.ps1 demo --cycles 100
.\industrial_station\lab.ps1 --role observer status
```

`demo` 顺序执行授权、清除注入故障、复位、切换 AUTO，再逐次执行有限的往复动作；运动结果失败便停止并导出报告，不自动重试动作。初始化或 HTTP 异常则退出，已提交命令仍保存在服务器，可用 report 导出。开始时应在原点，暂停在中间位置时须在 MANUAL 下明确执行回退，脚本不会擅自找原点。默认报告在 `industrial_station/runtime/demo.json`。

故障演示（从正常原点开始）：

```powershell
.\industrial_station\lab.ps1 command ARM
.\industrial_station\lab.ps1 command MODE --parameter 1
.\industrial_station\lab.ps1 command INJECT --parameter 1
.\industrial_station\lab.ps1 command EXTEND --id demo-limit-001
.\industrial_station\lab.ps1 command INJECT --parameter 0
.\industrial_station\lab.ps1 command RESET
.\industrial_station\lab.ps1 command RETRACT
```

伸出应得到 `FAILED / ACTION_TIMEOUT`。同一 ID 和内容重复请求只返回原结果；新的测试必须用新 ID。CLI 显示的是服务端记录，不因重复查询而再次执行动作。

| 注入编号 | 含义 |
| ---: | --- |
| 0 | 清除注入条件，不等于复位报警 |
| 1 | 目标限位不触发，触发动作超时 |
| 2 | 两端限位同时有效，输入矛盾 |
| 3 | 运动变慢，触发动作超时 |
| 4 | 暂时返回 Modbus DEVICE_BUSY，不是真实网络丢包 |
| 5 | 心跳数值冻结，其他模型逻辑继续运行 |

注入只允许在非运动状态。4、5 默认持续 2 秒，通信恢复不自动重新授权。故障清除后仍需满足复位条件，复位不自动启动。

## 3. 状态与动作约束

状态：`IDLE / EXTENDING / EXTENDED / RETRACTING / FAULT / STOPPED`；通信状态另用 `GOOD / STALE / OFFLINE`，数据来源始终标明 `SIMULATED`。

- 单程模型时间 400 ms，单程超时 1200 ms，本地控制租约 1000 ms，模型检查周期约 10 ms。均为实验参数，不是工业安全标准。
- MANUAL 允许伸出/回退；AUTO 的 CYCLE 是一次有限往复，不是无限自动运行。
- 双向输出互锁；运动中拒绝模式切换、新运动和故障注入，只允许 STOP。
- 中途 STOP 保留实际模型位置并进入 STOPPED，不伪造原点到位。
- 网关通信中断时撤销控制权；模拟器租约过期清输出。恢复后必须显式 ARM，不能自动续跑。
- 租约检测的是网关连接，不是操作员在场；关闭 PC 后，已确认的有限动作可能完成。这不是人员保护或急停系统。

命令带 ID、启动标识、会话、递增序号和设备时钟截止时间。过期、旧启动标识、旧会话和内容不同的重复 ID 均拒绝。模拟器保存 128 项去重缓存，并用序号下限防止缓存淘汰后重放；活动命令不会被淘汰。旧会话短期退役时间覆盖命令有效期，防止延迟到达的 ARM 抢回控制权。

网关不排运动队列，不自动重试写命令。发送后失联记为 `UNKNOWN`，不能把“无法确认”伪装为“未执行”。SQLite 中未结束命令在网关重启后也转 UNKNOWN，不重新发送。

轮询目标周期 50 ms，单次通信超时 200 ms；首次失败撤权并标记 STALE，连续 3 次失败 OFFLINE。不能简单将其称为 150 ms 检出，因为还有超时与调度等待。心跳冻结超过 400 ms 判异常；读取快照年龄超过 300/750 ms 分别为 STALE/OFFLINE。

## 4. 冻结的寄存器协议 v2

PDU 零基 Holding Registers，32 位高字在前，设备 ID 为 1。旧路线图 v1 仅是历史草案，不能用作本实现接口。

| 地址 | 数量 | 内容 |
| ---: | ---: | --- |
| 0 | 1 | 协议版本 2 |
| 1 / 2 | 各 1 | 状态 / 模式 |
| 3 / 4 / 5 | 各 1 | 输入位 / 输出位 / 报警 |
| 6 / 8 | 各 2 | 已完成往复计数 / 最近动作耗时 ms |
| 10 / 11 | 各 1 | 电流占位 0（无效）/ 质量位（模拟、输入有效） |
| 12 / 14 | 各 2 | 心跳 / 快照序号 |
| 16 / 18 / 20 | 各 2 | 启动标识 / 所有者会话 / 设备运行时钟 ms |
| 22 / 24 | 各 2 | 活动动作序号 / 最新命令确认序号 |
| 26 / 27 | 各 1 | 最新命令结果 / 原因 |
| 28 / 29 | 各 1 | 模型位置千分比 / 注入故障编号 |
| 30 / 32 | 各 2 | 运动启动次数 / 最近结束运动序号 |
| 34 / 35 / 36 | 1 / 1 / 2 | 结束运动结果 / 原因 / 耗时 ms |
| 38 / 39 | 各 1 | 剩余租约 ms / 保留 0 |
| 100 | 12 | 原子命令块，FC16 一次完整写入 |
| 120 | 6 | 原子续租块，FC16 一次完整写入 |

状态仅支持 FC03 完整或范围内读取；禁止写状态和拆分命令写入。命令块依次为：动作(1)、序号(2)、参数(1)、启动标识(2)、会话(2)、截止时间(2)、保留(2)。续租块为启动标识、会话、递增计数，各 2 字。

枚举和解码以 `protocol.py` 为准。网关拒绝非 v2 或缺少 SIMULATED 标记的设备，防止误接实物。32 位毫秒时钟约 49.7 天回绕；当前原型拒绝截止时间溢出，需要重启并重新授权，不是长期连续运行产品。

## 5. API、审计与测量边界

除公开的 `/health` 外，所有接口要求 `Authorization: Bearer <token>`。operator 可读写，observer 只读；无效凭据 401，只读提交动作 403，并记录审计事件。不同角色令牌不等于人员账号体系，多操作员控制权仲裁仍待实现。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/status` | 状态、质量、授权与模型数据 |
| POST | `/api/commands` | command_id/action/parameter/validity_ms |
| GET | `/api/commands/{id}` | 查询结果，不能触发重执行 |
| GET | `/api/events?after=0&limit=100` | 增量事件（最多 500 条） |
| GET | `/api/report?prefix=...` | 按命令前缀导出记录与统计 |

当前 HTTP 明文仅限可信局域网，不得映射公网；远程接入需另加 VPN/TLS 和账户管理。Modbus 默认只监听回环。服务和角色令牌不是安全 PLC 的替代品。

SQLite 保存命令生命周期与状态变化，不是每 50 ms 的全量波形。尚未实现保留期限和轮转。报告最多返回 5000 命令，截断会标记；FAILED、REJECTED、UNKNOWN 不从原始记录中删除。

- `device_duration_ms`：模拟器单调时钟的动作耗时；不含 PC Wi-Fi 往返，不是真实电机响应时间。
- `confirmation_ms`：网关从建立记录到收到确认的时间；不是浏览器端 RTT。重启后的 UNKNOWN 不计算跨进程单调时钟差。
- 均值/P95 仅对成功 CYCLE 的模拟动作耗时统计，同时保留其他状态计数；原始命令可重算。
- 视频延迟仍由原视频页面单独显示，不将其等同于设备动作时间。此版没有视频帧与报警关联。

## 6. 安装、测试与回退

首次克隆时（本机已有环境无需重复 init）：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r industrial_station/requirements.txt
.\.venv\Scripts\python.exe -m industrial_station.cli --url http://192.168.3.19:8091 init
.\.venv\Scripts\python.exe -m unittest discover -s industrial_station/tests -v
```

init 生成新凭据，拒绝覆盖旧文件。客户端和网关必须使用同一凭据文件；新生成的凭据不会自动适配已部署服务器。勿将 access.json、运行数据库或现场录像提交 Git。

本地启动：`python -m industrial_station.simulator` 和 `python -m industrial_station.gateway`。默认 HTTP 只监听 127.0.0.1。Pi 的 systemd 模板位于 `deploy/`，使用固定 eur 用户和 `/home/eur/industrial-lab` 路径，其他环境须修改。

部署后的显式故障验收（仅模拟工位，开始时须在 IDLE）：

```powershell
.\.venv\Scripts\python.exe -m industrial_station.acceptance
```

该脚本会发出模拟动作并注入限位/心跳故障，检查匿名与只读权限、重复命令、故障报警及恢复后重新授权。结果写入 `runtime/live-smoke.json`，不是只读健康检查。单元/集成测试共 23 项；部署验收与原始记录见 [2026-10-10 实施记录](../docs/industrial-roadmap/2026-10-10-implementation.md)。

回退只需停止新增服务，不动视频：

```sh
sudo systemctl disable --now pi-industrial-gateway pi-industrial-simulator
```

本项目适合展示工业通信、顺序控制、故障注入、审计与测试验证能力，不能替代真实 PLC 编程、电气接线、仪器标定及现场安全训练。
