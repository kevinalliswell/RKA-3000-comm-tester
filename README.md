# RKA-3000 通信测试工具

独立串口诊断工具，支持 TAIE 温控仪（Modbus RTU）、Azbil MFC（CPL）及 GS-CPA/龙腾电子天平。桌面使用 **Tauri 2 + Svelte 5/TypeScript**；Python 继续负责业务、配置数据、日志和串口通信。

## 分层设计

| 层 | 实现 | 职责 |
|---|---|---|
| 表示层 | `frontend/src/App.svelte`、`lib/session.svelte.ts` | 表单、状态、轮询、自动复查 |
| 桌面适配层 | `src-tauri/src/main.rs` | 窗口、受限 IPC、Python 进程启动与退出 |
| 接口适配层 | `src/rka_comm_tester/api.py`、`app.py` | 本地 JSON API、CLI、依赖组装 |
| 应用层 | `application.py` | 扫描任务、并发互斥、取消、配置用例 |
| 领域层 | `config.py`、`models.py` | 参数校验、扫描结果、端口数据模型 |
| 基础设施层 | `scanner.py`、`profiles.py`、`logging_setup.py`、`_vendor/` | 串口协议、文件存储和日志 |

完整依赖关系、任务状态与接口约定见 [分层设计](docs/architecture.md)。应用层通过注入的 `Probe`、端口枚举函数和 `ProfileStore` 使用基础设施；不导入 Qt、Tauri、HTTP 或串口库。

## 文档导航

| 文档 | 内容 |
|---|---|
| [通讯协议与诊断边界](docs/protocols.md) | TAIE Modbus RTU、Azbil CPL、GS-CPA 与龙腾协议的帧、寄存器、状态判定、天平去皮测试和只读边界 |
| [技术架构设计](docs/technical-architecture.md) | Tauri、Svelte、Python sidecar、进程生命周期、数据边界与 CI/CD |
| [分层设计思路](docs/layered-design.md) | 六层职责、依赖方向、端口注入、新增协议步骤和测试对应关系 |
| [分层与运行时细节](docs/architecture.md) | 扫描状态机、API 约定、进程收尾和扩展说明 |
| [构建与发布](docs/releasing.md) | 版本同步、Windows 构建、发布资产和发布限制 |
| [发行说明](docs/release-notes.md) | 当前发行版的变更、安装和现场验收提示 |

## 下载与运行

从 [GitHub Releases](https://github.com/kevinalliswell/RKA-3000-comm-tester/releases) 选择版本；带 `rc` 的版本是预发布版本，正式版本也仍需在现场设备上验收后再用于生产工作。

| 资产 | 用途 |
|---|---|
| `*_windows-x64_setup.exe` | Windows 10/11 x64 当前用户安装器，带 Python 业务服务；缺少 WebView2 时在线下载安装 |
| `*_windows-x64_portable.zip` | 解压全部文件，运行 `rka-3000-comm-tester.exe`；须已有 WebView2 Runtime |
| `SHA256SUMS.txt` | 两个分发包的 SHA-256 校验值，可用 `Get-FileHash <文件> -Algorithm SHA256` 核对 |

运行发行包无需安装 Python、Node.js、Rust。便携包必须保留同目录的 `rka-backend.exe`；“便携”仅指程序免安装，配置与日志仍放在用户目录。两种分发方式共用用户数据，升级/卸载不会主动删除配置和日志。

当前构建未配置 Windows 代码签名，系统可能显示未知发布者。仓库公开不改变 [应用许可证](LICENSE)；随包保留第三方许可与依赖清单。离线机器需提前安装 [Microsoft WebView2 Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)。当前仅发布 Windows x64 资产。

## 源码开发

Windows 开发运行需要 Python ≥3.10、Node.js ≥20.19（推荐 24 LTS）、Rust MSVC，以及 **Visual Studio Build Tools 的“使用 C++ 的桌面开发”工作负载、Windows SDK、WebView2 Runtime**。只安装 Rust 无法提供 `link.exe`。

```powershell
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
npm ci
npm --prefix frontend ci
.\run_windows.bat
```

也可在仓库根目录执行 `npm run tauri dev`。Tauri 使用仓库 `.venv`，可通过 `RKA_PYTHON` 指向其他已安装此 Python 包的解释器。

开发默认配置 `bundle.active=false`，无需预先冻结 Python。独立安装包使用单独的 `src-tauri/tauri.release.conf.json`，具体步骤见 [构建与发布](docs/releasing.md)。`RKA_PYTHON` 只在开发构建中生效；发布构建只运行随包的 `rka-backend.exe`。

macOS/Linux 同样使用 `.venv/bin/python`、`npm ci` 和 `npm run tauri dev`，另需安装 Tauri 对应系统的原生依赖。当前 CI 验证 Windows。

## 使用

1. 选择设备协议，刷新串口并选择/输入端口。枚举串口不会探测设备，也不会自动选择第一个端口。
2. 填写波特率、校验位、停止位、超时与站号范围；Azbil 仅支持 `8E1` 或 `8N2`，站号 1–127。
3. 开始扫描；在线、无响应、响应不完整和错误分别显示。停止会等待当前串口操作与驱动清理完成，结果标记为不完整。
4. 自动复查间隔 5–300 秒，从一轮扫描结束后计时；取消扫描或关闭复查会撤销等待中的下一轮。
5. 电子天平可执行「去皮测试」：确认后发送一次去皮命令，在设定等待时间内读到稳定零点（默认 ±0.05 g）即为成功。去皮会改变天平零点，不参与自动复查。
6. 保存的配置由 Python 写入 `~/.rka-comm-tester/config.json`，重启后加载。可以导入/导出 JSON 配置、导出日志。

默认日志为 `~/.rka-comm-tester/logs/communication.log`，5 MB 轮转，保留 3 个备份。页面显示实际路径。日志记录参数、串口枚举、协议结果与错误；Azbil 驱动另外记录收发帧。

**诊断边界：** 扫描不下发温度/流量设定值、去皮或任意寄存器写入；天平去皮只在点击「去皮测试」并确认后发送。TAIE 以有效 PV 判断在线，SV 失败保留诊断提示；Azbil 需有效 PV、SP、实时量程且原始值在量程内。天平需稳定重量帧；GS-CPA 的 O1 连续输出默认关闭，主动勾选后结束/停止时发送 O0。离线测试不能代替真机验收。

## 无原生工具链时调试界面

可使用同一套 Svelte 页面与真实 Python API 联调。两个终端使用相同的 `RKA_API_TOKEN`；该变量只由 Python 与 Vite 代理读取，不进入前端包：

```powershell
# 终端一，在仓库根目录
$env:RKA_API_TOKEN = 'local-development-only'
.venv\Scripts\python -m rka_comm_tester --api

# 终端二，在仓库根目录
$env:RKA_API_TOKEN = 'local-development-only'
npm --prefix frontend run dev
```

打开 `http://127.0.0.1:5173`。这是浏览器开发模式，不能验证 Tauri 的窗口/进程生命周期。正常桌面运行由 Tauri 创建随机令牌和动态端口，无需设置此变量。

## 命令行与旧界面

```powershell
.venv\Scripts\python -m rka_comm_tester --scan --config examples/taie.json
```

`examples/` 只是格式示例，端口与站号不是现场事实。CLI 输出 JSON，至少一台在线返回 0，否则返回 2；可用 `--log-file` 指定日志路径。

Qt 仅保留为可选兼容入口：安装 `pip install -e ".[legacy-ui]"` 后运行 `python -m rka_comm_tester --legacy-ui`。新的桌面运行和 Python 业务服务不依赖 PySide6。

## 验证

```powershell
.venv\Scripts\python -m pytest -q
.venv\Scripts\python -m ruff check .
npm --prefix frontend run check
npm --prefix frontend run build
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
cargo check --locked --manifest-path src-tauri/Cargo.toml
```

PR 和 main 推送触发 Windows CI：测试、类型检查、冻结 Python、Tauri release 编译、NSIS 打包、后端与桌面退出冒烟测试、安装/卸载验收，然后上传完整构建资产。推送 `v<版本号>` 标签复用同一流程，全部通过后才创建 GitHub Release；带 `-rc.N` 的标签自动标记预发布。发布流程不会合并 PR，详见 [构建与发布](docs/releasing.md)。

协议实现来源、钉定版本与本地修正见 [VENDOR.md](src/rka_comm_tester/_vendor/VENDOR.md)。本项目与 [RKA-3000-desktop](https://github.com/kevinalliswell/RKA-3000-desktop) 独立管理。
