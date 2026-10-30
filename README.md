# RKA-3000 通信测试工具

独立于 RKA-3000 桌面实验程序的串口诊断工具，支持 TAIE 温控仪（Modbus RTU）、Azbil MFC（CPL）和 GS-CPA/龙腾电子天平（串口 ASCII）。界面可列出系统串口、编辑参数、扫描所选端口的设备地址、查看结果，并保存或导出日志。串口扫描与设备读取在后台线程执行，界面不会等待串口超时。

**预发布版本：** `v0.2.0-rc1`，面向 Windows 10/11 x64。支持本地管理员登录、三设备扫描、人工单次写入、真机只读采集、模拟协议循环写入及诊断包导出。未接真机的离线测试不构成现场验收。

## 安装与启动

现场使用：从私有发布页下载安装包或便携 ZIP，并按 `SHA256SUMS.txt` 核对。安装包运行后从开始菜单启动；便携版解压后运行 `RKACommTester\\RKACommTester.exe`。均无需安装 Python。预发布资产未签名，Windows 可能提示“未知发布者”。

首次启动创建唯一的本地管理员账号（3–32 位英文字母、数字或下划线）和至少 12 位密码；无默认密码。密码以加盐 scrypt 哈希保存至 `%LOCALAPPDATA%\\RKACommTester\\auth.json`。连续 5 次登录失败锁定 15 分钟；空闲 30 分钟自动锁屏。可在“日志与结果”页修改密码。卸载不会删除账号和运行记录；忘记密码时请由现场负责人备份诊断数据后联系维护人员处理账号重置。

Windows 开发环境需要 Python 3.10 或更高版本：

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev,build]"
.\run_windows.bat
```

操作顺序：

1. **设备连接**：刷新串口，填写协议、串口参数、地址和设备选项；可保存/加载多套 JSON 配置。扫描逐地址检查有效协议响应。自动复查间隔为 5–300 秒。串口可打开不等于设备在线：温控仪需有效 PV，MFC 需有效 PV、SP 和量程元数据，天平需稳定重量帧。
2. **单次读写**：先读取当前设定。按现场 SOP 确认装置脱机、气路断开且加热输出隔离，再勾选确认框，核对当前值、目标值和单位后人工写入一次。TAIE 写 SV；Azbil 写 EEPROM `SP-0` 并分别显示“已存储”与“当前生效 SP”；天平执行去皮。程序核对回显和回读。写入超时或回读缺失显示“结果未知”，先重新读取，**不要直接重发**。完成后核对并手动恢复原设定。
3. **稳定性测试**：真机仅循环读取；模拟器可循环读写 TAIE/Azbil。Azbil `SP-0` 为 EEPROM，真机不循环写入；天平去皮也不循环。每个串口由独立工作线程串行处理；调度器跳过来不及执行的周期并记录漏采。
4. **日志与结果**：查看进度和错误，导出诊断 ZIP。每次运行有独立目录，包含逐次事件、性能摘要、配置副本（无密码）、版本信息和日志。回传 ZIP 时请附设备型号、现场接线、串口参数和测试时间。

默认数据目录：`%LOCALAPPDATA%\\RKACommTester`；日志在 `logs\\communication.log`，按 5 MB 轮转，保留 3 个备份；运行记录在 `runs\\`。日志可能包含设备标识和通信帧，请按现场要求保管。示例配置中的端口、地址和量程不是现场事实。

临时验收目标：真机温控读取 2 Hz、每路 MFC 读取 1 Hz、天平读取 1 Hz，连续 8 小时；模拟写入 5 Hz，连续 2 小时。暂按通信错误率不高于 0.1%、断线恢复不超过 60 秒评估，并递增目标频率报告可持续吞吐。界面“初步通过”仅按本次配置评估；完整时长及真机结论须核对现场导出记录。

## 命令行扫描

`examples/` 中是**格式示例**，`COM7/8/9` 和地址均不是 RKA 现场事实。先复制并填写，再运行：

```powershell
.venv\Scripts\python -m rka_comm_tester --scan --config examples\taie.json
```

结果以 JSON 输出；至少一台设备在线时退出码为 0，否则为 2。可用 `--log-file <路径>` 指定日志文件。命令行仅支持只读扫描，不提供写入入口。

## 维护与扩展

- `src/rka_comm_tester/config.py`：参数和校验。
- `src/rka_comm_tester/scanner.py`：串口枚举与各协议探测。
- `src/rka_comm_tester/operations.py`：单次写入、回显及回读核对。
- `src/rka_comm_tester/workers.py`：每串口独立线程。
- `src/rka_comm_tester/stress.py`、`runs.py`：测试调度、统计及诊断包。
- `src/rka_comm_tester/auth.py`、`ui.py`：本地账号与 Qt 界面。
- `src/rka_comm_tester/_vendor/`：从现有项目提取并钉定的协议实现，来源、版本和本地修正见 [`VENDOR.md`](src/rka_comm_tester/_vendor/VENDOR.md)。更新驱动时先比对上游，再运行协议与故障测试。

运行离线测试和代码检查：

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
```

Windows GitHub Actions 执行自动测试、便携版构建、安装与卸载检查；版本标签发布私有预发布资产。真机读写与长稳验收由现场记录确认。本仓库与 [RKA-3000-desktop](https://github.com/kevinalliswell/RKA-3000-desktop) 独立管理。
