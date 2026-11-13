use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::{
    env, fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Arc, Condvar, Mutex},
    thread,
    time::{Duration, Instant},
};
use tauri::{Manager, State};

const CONTRACT_VERSION: &str = "1.0.0";
/// Folder under %LOCALAPPDATA% for pgdata, logs and exports (packaging/windows/README.md). It must not be the
/// bundle identifier folder: the uninstaller's "delete app data" box removes that one with everything inside.
const DATA_FOLDER: &str = "IRDesktop";

#[derive(Clone, Serialize)]
pub struct Endpoint {
    base_url: String,
    token: String,
}

#[derive(Deserialize)]
#[serde(tag = "event")]
enum EngineEvent {
    #[serde(rename = "ready")]
    Ready {
        port: u16,
        version: String,
        contract_version: String,
    },
    #[serde(rename = "error")]
    Error { code: String, message: String },
}

/// Engine lifecycle. The engine starts on a background thread so the window shows at once (the first run includes
/// initdb); commands wait for it. A crashed engine is noticed on the next request and can be restarted from the UI.
enum Phase {
    Idle,
    Starting,
    Ready(Endpoint),
    Failed(String),
}

struct EngineProcess {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    phase: Phase,
    data_dir: Option<PathBuf>,
}

pub struct Engine {
    state: Mutex<EngineProcess>,
    changed: Condvar,
}

struct Launched {
    child: Child,
    stdin: ChildStdin,
    endpoint: Endpoint,
}

const START_TIMEOUT: Duration = Duration::from_secs(120);
const STOP_TIMEOUT: Duration = Duration::from_secs(20);

impl Engine {
    fn new() -> Self {
        Engine {
            state: Mutex::new(EngineProcess {
                child: None,
                stdin: None,
                phase: Phase::Idle,
                data_dir: None,
            }),
            changed: Condvar::new(),
        }
    }

    fn begin_start(self: &Arc<Self>, app: &tauri::AppHandle) {
        let paths = app
            .path()
            .resource_dir()
            .and_then(|resources| Ok((resources, data_dir(&app.path().local_data_dir()?))))
            .map_err(|e| e.to_string());
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        if matches!(state.phase, Phase::Starting)
            || (matches!(state.phase, Phase::Ready(_)) && state.exit_status().is_none())
        {
            return;
        }
        let (resource_dir, data_dir) = match paths {
            Ok(paths) => paths,
            Err(error) => {
                state.phase = Phase::Failed(error);
                self.changed.notify_all();
                return;
            }
        };
        state.phase = Phase::Starting;
        state.data_dir = Some(data_dir.clone());
        drop(state);

        let engine = Arc::clone(self);
        thread::spawn(move || {
            let result = launch(&resource_dir, &data_dir);
            if let Ok(mut state) = engine.state.lock() {
                match result {
                    Ok(launched) => {
                        state.child = Some(launched.child);
                        state.stdin = Some(launched.stdin);
                        state.phase = Phase::Ready(launched.endpoint);
                    }
                    Err(error) => {
                        eprintln!("[ir-engine] {error}");
                        state.phase = Phase::Failed(error);
                    }
                }
            }
            engine.changed.notify_all();
        });
    }

    /// Endpoint of a running engine; waits while it starts and reports an engine that has exited.
    fn wait_endpoint(&self, timeout: Duration) -> Result<Endpoint, String> {
        let deadline = Instant::now() + timeout;
        let mut state = self
            .state
            .lock()
            .map_err(|_| "Engine state is locked".to_owned())?;
        loop {
            match &state.phase {
                Phase::Ready(endpoint) => {
                    let endpoint = endpoint.clone();
                    match state.exit_status() {
                        None => return Ok(endpoint),
                        Some(status) => {
                            state.child = None;
                            state.stdin = None;
                            state.phase =
                                Phase::Failed(format!("Engine đã dừng bất thường ({status})"));
                        }
                    }
                }
                Phase::Failed(error) => return Err(error.clone()),
                Phase::Idle => return Err("Engine chưa được khởi động".to_owned()),
                Phase::Starting => {
                    let now = Instant::now();
                    if now >= deadline {
                        return Err("Engine chưa sẵn sàng sau thời gian chờ".to_owned());
                    }
                    state = self
                        .changed
                        .wait_timeout(state, deadline - now)
                        .map_err(|_| "Engine state is locked".to_owned())?
                        .0;
                }
            }
        }
    }

    fn stop(&self) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        // Closing stdin is the shutdown signal (contracts/engine_protocol.md).
        drop(state.stdin.take());
        if let Some(child) = state.child.take() {
            stop_child(child, STOP_TIMEOUT);
        }
        state.phase = Phase::Idle;
    }

    fn exports_dir(&self) -> Option<PathBuf> {
        self.state
            .lock()
            .ok()?
            .data_dir
            .as_ref()
            .map(|dir| dir.join("exports"))
    }
}

impl EngineProcess {
    /// Some(description) once the engine process has exited.
    fn exit_status(&mut self) -> Option<String> {
        match self.child.as_mut().map(Child::try_wait) {
            Some(Ok(None)) => None,
            Some(Ok(Some(status))) => Some(status.to_string()),
            Some(Err(error)) => Some(error.to_string()),
            None => Some("không có tiến trình".to_owned()),
        }
    }
}

fn stop_child(mut child: Child, timeout: Duration) {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) => thread::sleep(Duration::from_millis(100)),
        }
    }
    // The engine stops PostgreSQL itself; only a hung engine gets here.
    let _ = child.kill();
    let _ = child.wait();
}

fn launch(resource_dir: &Path, data_dir: &Path) -> Result<Launched, String> {
    fs::create_dir_all(data_dir).map_err(|e| e.to_string())?;
    let token = random_token();
    let (mut child, mut stdin) = spawn_engine(resource_dir)?;
    let handshake = serde_json::json!({
        "token": token,
        "data_dir": data_dir,
        "log_level": "INFO"
    });
    writeln!(stdin, "{handshake}").map_err(|e| format!("Cannot start engine handshake: {e}"))?;
    stdin.flush().map_err(|e| e.to_string())?;

    let stdout = child.stdout.take().ok_or("Engine stdout is unavailable")?;
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            let reader = BufReader::new(stderr);
            for line in reader.lines().map_while(Result::ok) {
                eprintln!("[ir-engine] {line}");
            }
        });
    }

    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let reader = BufReader::new(stdout);
        for line in reader.lines().map_while(Result::ok) {
            let line = line.trim().to_owned();
            if !line.is_empty() && tx.send(line).is_err() {
                break;
            }
        }
    });

    let fail = |mut child: Child, message: String| {
        let _ = child.kill();
        let _ = child.wait();
        Err(message)
    };
    let Ok(response) = rx.recv_timeout(START_TIMEOUT) else {
        return fail(
            child,
            format!(
                "Engine did not become ready within {} seconds",
                START_TIMEOUT.as_secs()
            ),
        );
    };
    match serde_json::from_str::<EngineEvent>(&response) {
        Ok(EngineEvent::Ready {
            port,
            version,
            contract_version,
        }) if contract_version == CONTRACT_VERSION => {
            if port == 0 {
                return fail(child, format!("Engine returned invalid port {port}"));
            }
            eprintln!("[ir-engine] ready: version {version} on port {port}");
            Ok(Launched {
                child,
                stdin,
                endpoint: Endpoint {
                    base_url: format!("http://127.0.0.1:{port}"),
                    token,
                },
            })
        }
        Ok(EngineEvent::Ready {
            contract_version, ..
        }) => fail(
            child,
            format!("Contract version mismatch: engine={contract_version}, app={CONTRACT_VERSION}"),
        ),
        Ok(EngineEvent::Error { code, message }) => fail(child, format!("{code}: {message}")),
        Err(_) => fail(child, format!("Invalid engine ready response: {response}")),
    }
}

fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rng().fill_bytes(&mut bytes);
    hex::encode(bytes)
}

fn spawn_engine(resource_dir: &Path) -> Result<(Child, ChildStdin), String> {
    let configured_exe = resource_dir.join("engine").join("ir-engine.exe");
    let dev_exe = find_dev_engine(resource_dir);
    let mut command = if configured_exe.is_file() {
        let mut command = Command::new(configured_exe);
        command.arg("serve");
        command
    } else if let Some(exe) = dev_exe.filter(|path| path.is_file()) {
        let mut command = Command::new(exe);
        command.arg("serve");
        command
    } else {
        let mut command = Command::new("python");
        command.args(["-m", "ir_engine", "serve"]);
        command.current_dir(repository_root(resource_dir).join("engine"));
        command
    };

    let mut child = command
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("Cannot launch IR engine: {e}"))?;
    let stdin = child.stdin.take().ok_or("Engine stdin is unavailable")?;
    Ok((child, stdin))
}

fn repository_root(resource_dir: &Path) -> PathBuf {
    env::current_dir()
        .ok()
        .filter(|dir| dir.join("contracts").is_dir())
        .unwrap_or_else(|| {
            let mut path = resource_dir.to_path_buf();
            for _ in 0..6 {
                if path.join("contracts").is_dir() {
                    return path;
                }
                if !path.pop() {
                    break;
                }
            }
            resource_dir.to_path_buf()
        })
}

fn find_dev_engine(resource_dir: &Path) -> Option<PathBuf> {
    let root = repository_root(resource_dir);
    Some(root.join("packaging/windows/dist/engine/ir-engine.exe"))
}

#[tauri::command]
async fn engine_endpoint(engine: State<'_, Arc<Engine>>) -> Result<Endpoint, String> {
    let engine = Arc::clone(&engine);
    tauri::async_runtime::spawn_blocking(move || engine.wait_endpoint(START_TIMEOUT))
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
async fn engine_restart(
    engine: State<'_, Arc<Engine>>,
    app: tauri::AppHandle,
) -> Result<Endpoint, String> {
    let engine = Arc::clone(&engine);
    tauri::async_runtime::spawn_blocking(move || {
        engine.stop();
        engine.begin_start(&app);
        engine.wait_endpoint(START_TIMEOUT)
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Opens a case report the engine wrote, with the default browser. Only .html files directly inside the exports
/// folder are accepted, so the frontend cannot open arbitrary paths.
#[tauri::command]
fn open_export(engine: State<'_, Arc<Engine>>, path: String) -> Result<(), String> {
    let exports = engine.exports_dir().ok_or("Engine chưa khởi động")?;
    let file = export_file(&exports, Path::new(&path))?;
    open_with_default_app(&file)
}

fn export_file(exports: &Path, requested: &Path) -> Result<PathBuf, String> {
    let root = exports
        .canonicalize()
        .map_err(|_| "Chưa có thư mục exports".to_owned())?;
    let file = requested
        .canonicalize()
        .map_err(|_| "Không tìm thấy file báo cáo".to_owned())?;
    let is_html = file
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case("html"));
    if file.parent() != Some(root.as_path()) || !is_html || !file.is_file() {
        return Err("Chỉ mở được file báo cáo .html trong thư mục exports".to_owned());
    }
    Ok(file)
}

/// canonicalize() on Windows returns \\?\C:\..., which Explorer does not accept.
fn plain_path(path: &Path) -> PathBuf {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC") => PathBuf::from(rest),
        _ => path.to_path_buf(),
    }
}

fn open_with_default_app(file: &Path) -> Result<(), String> {
    let file = plain_path(file);
    let mut command = if cfg!(target_os = "windows") {
        Command::new("explorer")
    } else if cfg!(target_os = "macos") {
        Command::new("open")
    } else {
        Command::new("xdg-open")
    };
    command
        .arg(&file)
        .spawn()
        .map(|_| ())
        .map_err(|e| format!("Không mở được file: {e}"))
}

pub fn run() {
    let engine = Arc::new(Engine::new());
    tauri::Builder::default()
        .manage(Arc::clone(&engine))
        .setup(move |app| {
            engine.begin_start(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            engine_endpoint,
            engine_restart,
            open_export
        ])
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                if let Some(engine) = window.app_handle().try_state::<Arc<Engine>>() {
                    engine.stop();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running IR Desktop");
}

fn data_dir(local_data: &Path) -> PathBuf {
    local_data.join(DATA_FOLDER)
}

#[cfg(test)]
mod tests {
    use super::{data_dir, export_file, plain_path, stop_child, EngineEvent};
    use std::{
        fs,
        path::Path,
        process::Command,
        time::{Duration, Instant},
    };

    #[test]
    fn data_lives_outside_the_identifier_folder_the_uninstaller_may_delete() {
        let local = Path::new(r"C:\Users\lab\AppData\Local");
        assert_eq!(data_dir(local), local.join("IRDesktop"));
        assert_ne!(data_dir(local), local.join("vn.irdesktop.app"));
    }

    #[test]
    fn parses_ready_response() {
        let response: EngineEvent = serde_json::from_str(
            r#"{"event":"ready","port":53127,"version":"0.1.0","contract_version":"1.0.0"}"#,
        )
        .unwrap();
        assert!(matches!(response, EngineEvent::Ready { port: 53127, .. }));
    }

    #[test]
    fn parses_error_response() {
        let response: EngineEvent = serde_json::from_str(
            r#"{"event":"error","code":"DB_UNAVAILABLE","message":"database failed"}"#,
        )
        .unwrap();
        assert!(matches!(response, EngineEvent::Error { code, .. } if code == "DB_UNAVAILABLE"));
    }

    #[test]
    fn opens_only_html_reports_inside_the_exports_folder() {
        let base = std::env::temp_dir().join(format!("ir-export-test-{}", std::process::id()));
        let exports = base.join("exports");
        fs::create_dir_all(&exports).unwrap();
        let report = exports.join("case-1.html");
        fs::write(&report, "<html></html>").unwrap();
        fs::write(exports.join("notes.txt"), "x").unwrap();
        fs::write(base.join("outside.html"), "x").unwrap();

        assert_eq!(
            export_file(&exports, &report).unwrap(),
            report.canonicalize().unwrap()
        );
        assert!(export_file(&exports, &exports.join("notes.txt")).is_err());
        assert!(export_file(&exports, &base.join("outside.html")).is_err());
        assert!(export_file(&exports, &exports.join("..").join("outside.html")).is_err());
        assert!(export_file(&exports, &exports.join("missing.html")).is_err());
        fs::remove_dir_all(&base).unwrap();
    }

    #[test]
    fn strips_the_verbatim_prefix_for_explorer() {
        assert_eq!(
            plain_path(Path::new(r"\\?\C:\Users\lab\x.html")),
            Path::new(r"C:\Users\lab\x.html")
        );
        assert_eq!(
            plain_path(Path::new(r"\\?\UNC\server\share")),
            Path::new(r"\\?\UNC\server\share")
        );
        assert_eq!(
            plain_path(Path::new("/tmp/x.html")),
            Path::new("/tmp/x.html")
        );
    }

    #[cfg(unix)]
    #[test]
    fn a_hung_engine_is_killed_after_the_stop_timeout() {
        let child = Command::new("sleep").arg("30").spawn().unwrap();
        let started = Instant::now();
        stop_child(child, Duration::from_millis(300));
        assert!(started.elapsed() < Duration::from_secs(5));
    }
}
