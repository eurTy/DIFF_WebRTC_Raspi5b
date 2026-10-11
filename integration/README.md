# 统一项目管理

本目录把树莓派实际运行的四个模块纳入同一发布清单。整合不等于把所有进程塞进一个容器，也不需要搬动正在工作的目录。`project.json` 记录源码位置、部署映射、接口、配置和冻结镜像标识；根目录 `project.py` 是只读验收入口，没有安装、删除或重启命令。

## 单一来源

| 模块 | 权威源码 | 当前部署 | 运行方式 |
| --- | --- | --- | --- |
| 原生采集 | project_0321plus/video-processor | 同名目录 | Docker |
| WebRTC 网关 | project_0321plus/webrtc-gateway | 同名目录 | Docker |
| 页面与信令 | signaling-server | /home/eur/signaling-server | systemd |
| 真实状态与证据 | monitoring | /home/eur/industrial-lab/monitoring | systemd |

基础 Compose 必须与 `docker-compose.latest.yml` 一起解释，否则会错用历史命名共享卷。现场有效挂载是 `/dev/shm/pi-video`，不是 README 早期示例中的默认命名卷。Pi 上信令权限片段名为 `hardening.conf`，仓库内容在 `systemd/pi-signaling-hardening.conf`；文件名不同不表示权限缺失。

`early_yuyv_dct_lz4` 与 `industrial_station` 保留为历史实验，不参与当前发布的四模块运行依赖。旧根说明归档在 `docs/history/original-root-readme.md`，其中相对链接以旧仓库根目录为基准。

## 统一验收

```bash
python project.py check
python project.py inventory --output integration/source-manifest.json
python -m unittest discover -s integration -v
python -m unittest discover -s monitoring/tests -v
cd signaling-server
npm ci
npm test
```

在树莓派新整合目录中运行以下命令，只读取旧部署，不会同步覆盖：

```bash
python3 project.py compare-runtime --home /home/eur
python3 project.py status --url http://127.0.0.1:8080
```

源文件比较将 CRLF 统一为 LF，避免 Windows 换行差异淹没真正改动。清单检查应用与测试核心文件，Git 发布归档同时保留构建所需的 vendored libdatachannel。镜像 ID 记录的是核对时实际运行的镜像；源码一致不等于已证明二进制逐位可复现。

## 冻结与恢复

旧版本固定在 `observer-v1-frozen-20261011`，基线提交 `d31ff5420a3038e4f518ae7599b4690af371683f`，原 main 不随整合分支变动。本机 `_version_archives/observer-v1-d31ff54/` 保存完整 Git bundle 和私有工作区归档。后者含现场媒体，绝不能推送公开仓库。

Pi 私有备份目标为 `/home/eur/version-archives/observer-v1-d31ff54/`：实际运行镜像导出、部署目录压缩包、SQLite 在线备份以及配置记录。运行数据库不能仅复制主 `.sqlite` 文件来替代在线备份。断线前数据库 `quick_check` 返回 `ok`；镜像导出命令已结束。部署压缩包及远端最终校验仍须在连接恢复后确认，不宣称已经全部验证。

恢复应先在隔离目录校验归档与数据库，再安排维护窗口切换服务。此项目不提供自动停止旧摄像头或覆盖 `/etc/systemd` 的捷径。单个 USB 摄像头不能假定允许两套采集器并发占用。

## 新方向隔离约定

语义自适应系统使用另一工作目录、独立代码根、独立分支、独立容器及端口。新系统不导入此项目的应用模块；可通过明确的只读采集适配器观察已有摄像头输出，或使用独立实验输入。共享物理摄像头和完全独立运行是不同条件，实验必须注明。
