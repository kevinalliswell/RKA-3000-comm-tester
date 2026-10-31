#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    env,
    io::{BufRead, BufReader},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::{mpsc, Mutex},
    time::{Duration, Instant},
};

use reqwest::blocking::Client;
use serde_json::Value;
use tauri::{Manager, RunEvent, State};

struct Backend {
    child: Mutex<Option<Child>>,
    connection: Connection,
}

#[derive(Clone)]
struct Connection {
    url: String,
    token: String,
}

fn python_executable() -> PathBuf {
    if let Some(value) = env::var_os("RKA_PYTHON") {
        return PathBuf::from(value);
    }
    // Cargo/Tauri may run in src-tauri, so do not resolve the venv from cwd.
    let root = if cfg!(debug_assertions) {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .to_path_buf()
    } else {
        env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_default()
    };
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

fn backend_command() -> (Command, PathBuf) {
    // An explicit interpreter override is useful for development/diagnostics.
    // Packaged applications always prefer the adjacent frozen backend.
    if env::var_os("RKA_PYTHON").is_none() {
        if let Some(directory) = env::current_exe()
            .ok()
            .and_then(|path| path.parent().map(|path| path.to_path_buf()))
        {
            let sidecar = directory.join(if cfg!(windows) {
                "rka-backend.exe"
            } else {
                "rka-backend"
            });
            if sidecar.is_file() {
                return (Command::new(&sidecar), sidecar);
            }
        }
    }
    let python = python_executable();
    let mut command = Command::new(&python);
    command.args(["-m", "rka_comm_tester.api"]);
    (command, python)
}

fn start_backend() -> Result<Backend, String> {
    let token = uuid::Uuid::new_v4().to_string();
    let (mut command, executable) = backend_command();
    command
        .args(["--port", "0"])
        .env("RKA_API_TOKEN", &token)
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::null())
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
            child: Mutex::new(Some(child)),
            connection: Connection {
                url: format!("http://127.0.0.1:{port}"),
                token,
            },
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
        ("POST", ["api", "scans" | "config"]) => true,
        ("GET", ["api", "scans", id]) | ("POST", ["api", "scans", id, "cancel"]) => {
            id.len() == 32 && id.chars().all(|c| c.is_ascii_hexdigit())
        }
        _ => false,
    };
    if !allowed {
        return Err("不支持的业务接口".to_string());
    }
    let connection = backend.connection.clone();
    tauri::async_runtime::spawn_blocking(move || request(&connection, &method, &path, body))
        .await
        .map_err(|e| e.to_string())?
}

fn stop_backend(backend: &Backend) {
    if let Ok(mut guard) = backend.child.lock() {
        if let Some(mut child) = guard.take() {
            // Python cancels the active task and waits for protocol finally blocks (including O0).
            let _ = request(&backend.connection, "POST", "/api/shutdown", None);
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

fn main() {
    let app = tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![api_request])
        .setup(|app| {
            app.manage(start_backend()?);
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("无法创建 Tauri 窗口");
    app.run(|app, event| {
        if matches!(event, RunEvent::Exit) {
            stop_backend(&app.state::<Backend>());
        }
    });
}
