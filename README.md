# RKA-3000 通信测试工具

独立于 RKA-3000 桌面实验程序的串口诊断工具，支持 TAIE 温控仪（Modbus RTU）、Azbil MFC（CPL）和 GS-CPA/龙腾电子天平（串口 ASCII）。界面可列出系统串口、编辑参数、扫描所选端口的设备地址、查看结果，并保存或导出日志。串口扫描与设备读取在后台线程执行，界面不会等待串口超时。

**诊断边界：** 工具不会下发温度/流量设定值、去皮或任意寄存器写入。TAIE 与 Azbil 的读请求会发送协议查询帧；GS-CPA 如勾选“发送 O1 启动连续输出”，会发送 `O1` 命令以启动数据流，扫描结束或停止时发送 `O0` 收尾，默认不勾选。串口可打开不等于设备在线：温控仪需有效 PV，SV 读取失败会作为附加诊断提示；MFC 需有效 PV、SP、量程元数据且原始值处于量程内；天平需有效稳定重量帧。未接真机的离线测试不构成现场验收。

## 安装与启动

需要 Python 3.10 或更高版本。Windows 首次安装：

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e .
.\run_windows.bat
```

macOS/Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m rka_comm_tester
```

操作顺序：

1. 选择设备协议，点击“刷新串口”，从列表选择现场端口；也可手填端口名。
2. 按设备面板或现场记录填写波特率、校验位、停止位、超时和地址范围。Azbil 支持 `8E1` 或 `8N2`。
3. 点击“开始扫描”。列表逐地址显示在线、响应不完整、无响应或错误；点击“停止”会在当前有界读操作结束后中止，并显示结果不完整。
4. 可勾选“自动复查”，间隔 5–300 秒，默认 30 秒。也可“保存配置”供下次加载。
5. 点击“导出日志”保存本次诊断日志，回传该 `.log` 文件，并附上设备型号、现场接线、串口参数和测试时间。

默认日志：`~/.rka-comm-tester/logs/communication.log`，按 5 MB 轮转，保留 3 个备份。界面底部显示实际路径，并提供“打开日志目录”。日志包含扫描参数、串口枚举、每个地址的状态、读取值与错误堆栈；Azbil 驱动另记录收发帧十六进制调试信息。日志可能包含设备标识，请按现场要求保管。

## 命令行扫描

`examples/` 中是**格式示例**，`COM7/8/9` 和地址均不是 RKA 现场事实。先复制并填写，再运行：

```bash
.venv/bin/python -m rka_comm_tester --scan --config examples/taie.json
```

Windows 使用 `.venv\Scripts\python`。结果以 JSON 输出；至少一台设备在线时退出码为 0，否则为 2。可用 `--log-file <路径>` 指定日志文件。地址扫描时间约随地址数量及超时线性增长；建议先缩小到已知站号，再扩大范围。

## 维护与扩展

- `src/rka_comm_tester/config.py`：参数和校验。
- `src/rka_comm_tester/scanner.py`：串口枚举与各协议探测。新增设备时增加独立探测函数和界面选项，保持每次 I/O 有界并只读。
- `src/rka_comm_tester/ui.py`：简洁 Qt 界面与后台扫描线程。
- `src/rka_comm_tester/_vendor/`：从现有项目提取并钉定的协议实现，来源、版本和本地修正见 [`VENDOR.md`](src/rka_comm_tester/_vendor/VENDOR.md)。更新驱动时先比对上游，再运行协议与故障测试。

运行离线测试和代码检查：

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
```

本仓库与 [RKA-3000-desktop](https://github.com/kevinalliswell/RKA-3000-desktop) 独立管理。现场确认表仍在原项目的 `docs/` 中；本工具的示例参数不能代替现场映射或硬件联锁验收。
