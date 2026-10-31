# 构建与发布

## CI/CD 入口

- `Checks`：PR、main 推送及手动运行。Windows x64 下完成所有检查，产出 `rka-comm-tester-windows-x64` artifact，保留 14 天。
- `Release`：推送 `v*` 标签，调用同一 `Checks` 工作流。下载并核对资产后创建 Release，长期保存安装器、便携 ZIP 与 `SHA256SUMS.txt`。含 `-` 的版本标记 prerelease，其余标记正式版。
- 构建失败不会创建 Release；修复后用新提交、新版本标签重新发布。已发布标签与资产不覆盖，不自动合并 PR，也没有后台自动更新客户端。

仅发布任务有 `contents: write`；检查任务只读，PR 无发布权限。Release 使用 GitHub 自动提供的 `GITHUB_TOKEN`，无需个人访问令牌；仓库 Actions 必须允许工作流运行并允许发布任务写入 contents。发布工作流采用相同标签并发互斥，避免重复发布竞态。

## 版本与发版

所有版本必须相同：根及 frontend 的 `package.json` 和 `package-lock.json`、`pyproject.toml`、Python `__version__`、`src-tauri/Cargo.toml` / `Cargo.lock` / `tauri.conf.json`。`scripts/check_version.py` 防止版本遗漏，并校验标签恰为 `v<版本>`。

1. 更新上述版本、`CHANGELOG.md` 与 `docs/release-notes.md`，提交并推送。
2. 等待对应 PR 或手动 `Checks` 成功，检查 artifact。当前迁移分支可直接发布预发布，无需合并 PR。
3. 在已验证的提交上创建并推送标签。正式版本使用不含连字符的标签，例如：

   ```powershell
   python scripts/check_version.py --tag v0.1.0
   git tag -a v0.1.0 -m "RKA-3000 desktop stable release"
   git push origin v0.1.0
   ```

4. 等待 `Release` 成功；核对 Release 关联提交、prerelease 标记、3 个资产及校验值。所有验证针对该标签重新运行，不能用其他提交的 artifact 代替。

修复候选版可继续使用 `v0.1.0-rc.2` 等带连字符的标签。正式版仍需现场串口设备验收；版本标签是发布入口，并不替代代码审查或硬件验收。

## 本地重复构建

使用 Windows 10/11 x64、Python **3.12 x64**、Node.js 24、Rust MSVC stable、Visual Studio C++ Build Tools + Windows SDK、WebView2。本机若缺原生工具链，直接在 GitHub `Checks` 中选择分支手动运行，下载 artifact；工作流首次进入默认分支前也可通过 PR 事件运行。

在干净的工作区与 Python 3.12 虚拟环境中执行（PowerShell；激活后 `python` 指向该环境）：

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements/build.txt -e ".[dev,legacy-ui]"
npm ci
npm --prefix frontend ci
python scripts/check_version.py
python -m pytest -q
python -m ruff check .
npm --prefix frontend run check
cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
python scripts/build_backend.py
python scripts/smoke_backend.py src-tauri/binaries/rka-backend-x86_64-pc-windows-msvc.exe
python scripts/collect_notices.py
npm run tauri build -- --ci --config src-tauri/tauri.release.conf.json -- --locked
python scripts/stage_release.py
```

输出位于 `dist/release/`。`build/portable/` 是相同程序文件的展开目录。发布配置只生成 NSIS（当前用户、中文/英文安装界面），安装器联网补齐 WebView2；便携包不内置 WebView2，离线部署需提前安装 Runtime。

`stage_release.py` 要求 `build/portable/`、`dist/release/` 尚不存在，以免混入旧资产；本地重新打包前只清理这两个生成目录。`npm ci`、`Cargo.lock` 与 `requirements/build.txt` 固定主要依赖；GitHub runner/编译工具及 Python 间接构建依赖仍会更新，当前不承诺逐字节可复现。

## 验收与限制

流水线检查冻结后端的就绪握手、401 认证、health、端口枚举、非法扫描参数、优雅 shutdown。只枚举串口，**不打开串口或发送设备命令**。桌面冒烟在隔离的 `USERPROFILE` 下启动便携程序，检查窗口与随包后端，再正常关闭，确认业务进程退出。随后静默安装到 CI 临时目录，重复窗口验收并卸载，检查主程序移除。

CI 使用完整 Windows runner；安装/卸载脚本会写当前用户的安装注册项，建议仅在测试机器上手动执行。浏览器调试覆盖交互流程，CI 冒烟覆盖实际分发进程，不替代真机设备测试。

当前未配置 Authenticode、签名证书或自动更新签名。安装器/程序为未签名资产；SHA-256 用于核对下载完整性，不等同于发布者代码签名。后续可在独立发布环境加入签名密钥，保持 PR 构建不接触发布秘密。
