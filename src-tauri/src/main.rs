#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    env,
    fs::{self, OpenOptions},
    io::{BufRead, BufReader, Write},
    path::PathBuf,
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Mutex},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use reqwest::blocking::Client;
use serde_json::Value;
use tauri::{Manager, RunEvent, State};

struct Backend {
    child: Mutex<Option<Child>>,
    // Held for the app lifetime: if this process dies, the OS closes the pipe and Python exits.
    stdin: Mutex<Option<ChildStdin>>,
    connection: Result<Connection, String>,
}

impl Backend {
    fn unavailable(error: String) -> Self {
        Backend {
            child: Mutex::new(None),
            stdin: Mutex::new(None),
            connection: Err(error),
        }
    }
}

#[derive(Clone)]
struct Connection {
    url: String,
    token: String,
}

fn sidecar_path() -> Option<PathBuf> {
    let directory = env::current_exe().ok()?.parent()?.to_path_buf();
    let sidecar = directory.join(if cfg!(windows) {
        "rka-backend.exe"
    } else {
        "rka-backend"
    });
    sidecar.is_file().then_some(sidecar)
}

#[cfg(not(debug_assertions))]
const MISSING_SIDECAR: &str = "未找到随包业务服务 rka-backend.exe，请重新安装或完整解压便携包";

/// Release builds only run the frozen backend shipped next to the executable, so a stray
/// RKA_PYTHON or interpreter on PATH can never become the serial service.
#[cfg(not(debug_assertions))]
fn backend_command() -> Result<(Command, PathBuf), String> {
    let sidecar = sidecar_path().ok_or(MISSING_SIDECAR)?;
    Ok((Command::new(&sidecar), sidecar))
}

#[cfg(debug_assertions)]
fn python_executable() -> PathBuf {
    if let Some(value) = env::var_os("RKA_PYTHON") {
        return PathBuf::from(value);
    }
    // Cargo/Tauri may run in src-tauri, so do not resolve the venv from cwd.
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .unwrap()
        .to_path_buf();
    let local = root.join(if cfg!(windows) {
        ".venv/Scripts/python.exe"
    } else {
        ".venv/bin/python"
    });
    if local.is_file() {
        local
    } else {
        PathBuf::from(if cfg!(windows) { "python" } else { "python3" })
    }
}

/// Development builds may override the interpreter with RKA_PYTHON for diagnostics.
#[cfg(debug_assertions)]
fn backend_command() -> Result<(Command, PathBuf), String> {
    if env::var_os("RKA_PYTHON").is_none() {
        if let Some(sidecar) = sidecar_path() {
            return Ok((Command::new(&sidecar), sidecar));
        }
    }
    let python = python_executable();
    let mut command = Command::new(&python);
    command.args(["-m", "rka_comm_tester.api"]);
    Ok((command, python))
}

fn start_backend() -> Result<Backend, String> {
    let token = uuid::Uuid::new_v4().to_string();
    let (mut command, executable) = backend_command()?;
    command
        .args(["--port", "0", "--exit-on-stdin-eof"])
        .env("RKA_API_TOKEN", &token)
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("无法启动 Python 业务服务（{}）: {e}", executable.display()))?;
    let stdout = child.stdout.take().ok_or("Python 就绪管道不可用")?;
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let result = reader
            .read_line(&mut line)
            .map_err(|e| e.to_string())
            .and_then(|_| serde_json::from_str::<Value>(&line).map_err(|e| e.to_string()));
        let _ = sender.send(result);
    });
    let ready = receiver
        .recv_timeout(Duration::from_secs(30))
        .map_err(|_| "等待 Python 业务服务就绪超时".to_string())
        .and_then(|value| value)
        .and_then(|value| {
            value["port"]
                .as_u64()
                .filter(|port| *port > 0 && *port <= u16::MAX as u64)
                .ok_or_else(|| "Python 返回的监听端口无效".to_string())
        });
    match ready {
        Ok(port) => Ok(Backend {
            stdin: Mutex::new(child.stdin.take()),
            child: Mutex::new(Some(child)),
            connection: Ok(Connection {
                url: format!("http://127.0.0.1:{port}"),
                token,
            }),
        }),
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(format!(
                "{error}；请确认 rka-backend 与桌面程序位于同一目录，或 Python 环境已安装此项目"
            ))
        }
    }
}

fn request(
    backend: &Connection,
    method: &str,
    path: &str,
    body: Option<Value>,
) -> Result<Value, String> {
    let client = Client::builder()
        .no_proxy()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|e| e.to_string())?;
    let method = method
        .parse::<reqwest::Method>()
        .map_err(|e| e.to_string())?;
    let mut request = client
        .request(method, format!("{}{path}", backend.url))
        .bearer_auth(&backend.token);
    if let Some(body) = body {
        request = request.json(&body);
    }
    let response = request
        .send()
        .map_err(|e| format!("本地业务服务不可用: {e}"))?;
    let status = response.status();
    let payload: Value = response.json().map_err(|e| e.to_string())?;
    if !status.is_success() {
        return Err(payload["error"]
            .as_str()
            .unwrap_or("业务请求失败")
            .to_string());
    }
    Ok(payload)
}

#[tauri::command]
async fn api_request(
    backend: State<'_, Backend>,
    method: String,
    path: String,
    body: Option<Value>,
) -> Result<Value, String> {
    let segments: Vec<_> = path.trim_matches('/').split('/').collect();
    let allowed = match (method.as_str(), segments.as_slice()) {
        ("GET", ["api", "health" | "ports" | "config" | "log"]) => true,
        ("POST", ["api", "scans" | "config" | "tare"]) => true,
        ("GET", ["api", "scans", id]) | ("POST", ["api", "scans", id, "cancel"]) => {
            id.len() == 32 && id.chars().all(|c| c.is_ascii_hexdigit())
        }
        _ => false,
    };
    if !allowed {
        return Err("不支持的业务接口".to_string());
    }
    let connection = backend.connection.clone()?;
    tauri::async_runtime::spawn_blocking(move || request(&connection, &method, &path, body))
        .await
        .map_err(|e| e.to_string())?
}

fn stop_backend(backend: &Backend) {
    let Ok(connection) = &backend.connection else {
        return;
    };
    if let Ok(mut guard) = backend.child.lock() {
        if let Some(mut child) = guard.take() {
            // Python cancels the active task and waits for protocol finally blocks (including O0).
            let _ = request(connection, "POST", "/api/shutdown", None);
            // Closing stdin is the fallback shutdown signal if the HTTP request failed.
            if let Ok(mut stdin) = backend.stdin.lock() {
                stdin.take();
            }
            let deadline = Instant::now() + Duration::from_secs(90);
            loop {
                match child.try_wait() {
                    Ok(Some(_)) => break,
                    _ if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
                    _ => {
                        eprintln!("Python 未在 90 秒内完成清理，强制结束业务服务");
                        let _ = child.kill();
                        let _ = child.wait();
                        break;
                    }
                }
            }
        }
    }
}

const DESKTOP_LOG_HINT: &str = "详见 %USERPROFILE%\\.rka-comm-tester\\logs\\desktop.log";

/// Release builds have no console, so startup diagnostics go to the user log directory.
fn desktop_log(message: &str) {
    eprintln!("{message}");
    let Some(home) = env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }) else {
        return;
    };
    let directory = PathBuf::from(home).join(".rka-comm-tester").join("logs");
    if fs::create_dir_all(&directory).is_err() {
        return;
    }
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(directory.join("desktop.log"))
    {
        let seconds = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|elapsed| elapsed.as_secs())
            .unwrap_or_default();
        let _ = writeln!(file, "unix={seconds} {message}");
    }
}

#[cfg(windows)]
fn show_error(message: &str) {
    use std::{ffi::c_void, iter::once, ptr::null_mut};

    #[link(name = "user32")]
    extern "system" {
        fn MessageBoxW(hwnd: *mut c_void, text: *const u16, caption: *const u16, kind: u32) -> i32;
    }
    let text: Vec<u16> = message.encode_utf16().chain(once(0)).collect();
    let caption: Vec<u16> = "RKA-3000 通信测试".encode_utf16().chain(once(0)).collect();
    // MB_OK | MB_ICONERROR
    unsafe {
        MessageBoxW(null_mut(), text.as_ptr(), caption.as_ptr(), 0x10);
    }
}

#[cfg(not(windows))]
fn show_error(_message: &str) {}

fn fatal(message: &str) -> ! {
    desktop_log(message);
    show_error(message);
    std::process::exit(1);
}

fn main() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![api_request])
        .setup(|app| {
            // Keep the window usable so the page can show why the backend is unavailable.
            let backend = start_backend().unwrap_or_else(|error| {
                desktop_log(&format!("业务服务启动失败: {error}"));
                let message = format!("本地业务服务启动失败：{error}（{DESKTOP_LOG_HINT}）");
                Backend::unavailable(message)
            });
            app.manage(backend);
            Ok(())
        })
        .build(tauri::generate_context!())
        .unwrap_or_else(|error| {
            fatal(&format!(
                "无法创建桌面窗口：{error}\n请确认已安装 Microsoft Edge WebView2 Runtime。"
            ))
        });
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            stop_backend(&app.state::<Backend>());
        }
    });
}
