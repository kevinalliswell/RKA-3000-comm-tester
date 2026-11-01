# 技术架构设计

RKA-3000 通信测试工具采用 Tauri 2 + Svelte 5/TypeScript 的桌面窗口，以及 Python 业务 sidecar。窗口负责交互和桌面生命周期，Python 负责配置、任务、协议驱动、日志和数据模型；Rust 不实现串口业务。

## 组件关系

```mermaid
flowchart LR
    UI["Svelte 5 / TypeScript<br/>表示与状态"] --> IPC["Tauri IPC<br/>白名单命令"]
    IPC --> Shell["Rust Tauri shell<br/>窗口与进程生命周期"]
    Shell -->|"127.0.0.1:动态端口 / 随机 Bearer token"| API[Python API 适配层]
    API --> App["应用服务<br/>扫描任务/取消/配置"]
    App --> Domain["领域模型<br/>ScanConfig/ScanResult"]
    App --> Ports["注入接口<br/>Probe/ProfileStore/端口枚举"]
    Scanner[串口探测适配器] -.实现 Probe.-> Ports
    Scanner --> TAIE[TAIE Modbus RTU]
    Scanner --> Azbil[Azbil CPL]
    Scanner --> Balance[GS-CPA / 龙腾 ASCII]
    Store[配置文件适配器] -.实现 ProfileStore.-> Ports
    API --> Logs[轮转日志初始化]
```

源码映射如下：

| 组件 | 位置 | 责任 |
| --- | --- | --- |
| 窗口与状态 | `frontend/src/` | 表单、轮询、任务状态和结果展示 |
| 桌面壳 | `src-tauri/src/main.rs` | 启动/等待/关闭 Python，保存 token，转发受限 API |
| HTTP/CLI 适配 | `src/rka_comm_tester/api.py`、`app.py` | 解析请求、鉴权、状态码和依赖组装 |
| 应用服务 | `application.py` | 单任务互斥、取消、历史上限、配置用例 |
| 领域模型 | `config.py`、`models.py` | 参数约束、地址范围、结果结构 |
| 基础设施 | `scanner.py`、`profiles.py`、`logging_setup.py`、`_vendor/` | 串口 I/O、协议帧、配置文件和日志 |

## 进程启动与请求路径

```mermaid
sequenceDiagram
    participant T as Tauri Rust
    participant P as Python sidecar
    participant S as Svelte
    T->>T: 生成随机 token
    T->>P: 启动并注入 RKA_API_TOKEN、--port 0
    P-->>T: stdout 返回监听端口 JSON
    T->>T: 校验端口范围并连接 127.0.0.1
    S->>T: invoke(白名单 API)
    T->>P: loopback HTTP + Bearer token
    P->>P: API -> 应用服务 -> 探测适配器
    P-->>T: JSON 任务快照/结果
    T-->>S: 返回类型化数据
```

启动时 Python 监听 `127.0.0.1:0`，由操作系统分配空闲端口，再通过 stdout 输出机器可读握手。Rust 最多等待 30 秒，就绪握手失败时终止并等待子进程退出。后端 stderr 继承自桌面进程；Python 日志初始化成功后的诊断另写用户日志。CI 冒烟脚本单独捕获桌面 stdout/stderr；发布窗口的启动失败另追加到 `~/.rka-comm-tester/logs/desktop.log`，后端未就绪时页面显示失败原因。后端 stdin 是 Tauri 持有的管道，桌面进程异常退出后 Python 读到 EOF 自行收尾退出。开发环境可使用仓库 `.venv` 或 `RKA_PYTHON`；发布包只使用主程序旁的 `rka-backend.exe`。

页面通过桌面白名单访问健康检查、端口枚举、配置、扫描、天平去皮测试、取消和日志；shutdown 仅由 Rust 在退出时调用，不向页面开放。token 不进入前端 bundle；Python API 只允许绑定 `127.0.0.1`，拒绝缺失或错误的 token，并限制配置/扫描 JSON 请求体不超过 1 MB。

## 扫描和关闭生命周期

1. Svelte 提交已填写的配置，Python 领域模型再次校验参数。
2. 应用服务在活动任务存在时返回冲突，否则创建任务并在线程中运行探测器。
3. 探测器在每个地址/帧之间检查取消事件；驱动的 `finally` 关闭串口并执行必要的 GS-CPA `O0`。
4. 任务只有在探测迭代器退出后才进入 `completed` 或 `cancelled`，结果和清理状态一起返回。
5. 退出窗口时 Tauri 调用私有 shutdown，Python 取消活动任务、等待资源清理并关闭 HTTP 服务；90 秒后才强制回收仍未退出的进程。

这套顺序把设备资源的所有权留在 Python，避免 Rust 或前端在取消期间直接触碰串口。桌面进程崩溃时后端经 stdin EOF 收尾；后端自身崩溃、断电或外部强杀时，协议层无法保证最后一条清理命令送达，因此现场仍应使用硬件安全状态。

## 数据与安全边界

- 用户配置原子写入 `~/.rka-comm-tester/config.json`，日志写入同目录 `logs/communication.log`，按 5 MB 轮转并保留 3 个备份。
- Python 任务历史只保留最近 50 条，避免自动复查长期占用内存；任务结果为 JSON 快照，不作为持久化数据库。
- Rust 只保存当前会话 token 和子进程句柄；前端只持有类型化的业务响应，不读取进程环境变量。
- 串口协议的校验、地址、字数、量程和状态判定在 Python 驱动层完成；Svelte 只负责呈现，不复制判定逻辑。

## 构建、分发与 CI/CD

开发构建使用 Vite 和 Tauri dev，Python 直接来自 `.venv`。发布构建先用 PyInstaller 生成 `rka-backend-<target>.exe`，再由 Tauri `externalBin` 嵌入 NSIS 安装器和便携目录。`stage_release.py` 复制主程序、sidecar、第三方许可和使用说明，并生成 `SHA256SUMS.txt`。

```mermaid
flowchart TD
    PR[Pull Request / main push] --> Checks[Windows Checks]
    Checks --> Unit[Python tests + Ruff]
    Checks --> Frontend[Svelte check/build]
    Checks --> Rust[Rust fmt + Tauri 编译]
    Checks --> Freeze[PyInstaller sidecar]
    Checks --> Smoke[后端/桌面/安装卸载冒烟]
    Tag["v版本号 tag"] --> Release[Release workflow]
    Release --> Checks
    Checks --> Assets[NSIS + portable ZIP + SHA256]
    Assets --> Publish[Release 发布任务校验 SHA256]
    Publish --> GitHub[GitHub Release]
```

检查工作流只读仓库内容并上传构建 artifact；发布工作流在标签验证成功后使用 `contents: write` 创建 Release。带 `-rc.N` 的标签自动标记为 prerelease，正式标签例如 `v0.1.0`。目前未配置 Authenticode，SHA-256 只验证下载完整性，不能代替代码签名。

更具体的本地命令、版本同步清单和发布故障处理见 [`docs/releasing.md`](releasing.md)。
