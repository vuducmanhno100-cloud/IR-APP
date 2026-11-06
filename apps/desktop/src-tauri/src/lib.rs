use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::{
    env,
    fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{Child, ChildStdin, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::Duration,
};
use tauri::{Manager, State};

const CONTRACT_VERSION: &str = "1.0.0";

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

pub struct EngineProcess {
    child: Option<Child>,
    stdin: Option<ChildStdin>,
    endpoint: Option<Endpoint>,
    last_error: Option<String>,
}

impl EngineProcess {
    fn start(&mut self, app: &tauri::AppHandle) -> Result<(), String> {
        let resource_dir = app.path().resource_dir().map_err(|e| e.to_string())?;
        let data_dir = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
        fs::create_dir_all(&data_dir).map_err(|e| e.to_string())?;

        let token = random_token();
        let (mut child, mut stdin) = spawn_engine(&resource_dir)?;
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

        let response = rx
            .recv_timeout(Duration::from_secs(60))
            .map_err(|_| "Engine did not become ready within 60 seconds".to_owned())?;
        match serde_json::from_str::<EngineEvent>(&response) {
            Ok(EngineEvent::Ready {
                port,
                contract_version,
                ..
            }) if contract_version == CONTRACT_VERSION => {
                if !(1..=65535).contains(&port) {
                    return Err(format!("Engine returned invalid port {port}"));
                }
                self.endpoint = Some(Endpoint {
                    base_url: format!("http://127.0.0.1:{port}"),
                    token,
                });
                self.child = Some(child);
                self.stdin = Some(stdin);
                Ok(())
            }
            Ok(EngineEvent::Ready { contract_version, .. }) => Err(format!(
                "Contract version mismatch: engine={contract_version}, app={CONTRACT_VERSION}"
            )),
            Ok(EngineEvent::Error { code, message }) => Err(format!("{code}: {message}")),
            Err(_) => Err(format!("Invalid engine ready response: {response}")),
        }
    }

    fn stop(&mut self) {
        drop(self.stdin.take());
        if let Some(mut child) = self.child.take() {
            let _ = child.wait();
        }
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
fn engine_endpoint(state: State<'_, Mutex<EngineProcess>>) -> Result<Endpoint, String> {
    let state = state.lock().map_err(|_| "Engine state is locked")?;
    state.endpoint.clone().ok_or_else(|| {
        state
            .last_error
            .clone()
            .unwrap_or_else(|| "Engine is not ready".to_owned())
    })
}

#[tauri::command]
fn engine_start(state: State<'_, Mutex<EngineProcess>>, app: tauri::AppHandle) -> Result<(), String> {
    let mut state = state.lock().map_err(|_| "Engine state is locked")?;
    if state.endpoint.is_some() {
        return Ok(());
    }
    match state.start(&app) {
        Ok(()) => {
            state.last_error = None;
            Ok(())
        }
        Err(error) => {
            state.last_error = Some(error.clone());
            Err(error)
        }
    }
}

pub fn run() {
    tauri::Builder::default()
        .manage(Mutex::new(EngineProcess {
            child: None,
            stdin: None,
            endpoint: None,
            last_error: None,
        }))
        .setup(|app| {
            let state = app.state::<Mutex<EngineProcess>>();
            if let Err(error) = engine_start(state, app.handle().clone()) {
                eprintln!("[ir-engine] {error}");
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![engine_endpoint, engine_start])
        .on_window_event(|_window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                let app = _window.app_handle();
                if let Some(state) = app.try_state::<Mutex<EngineProcess>>() {
                    if let Ok(mut state) = state.lock() {
                        state.stop();
                    }
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running IR Desktop");
}

#[cfg(test)]
mod tests {
    use super::EngineEvent;

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
        let response: EngineEvent =
            serde_json::from_str(r#"{"event":"error","code":"DB_UNAVAILABLE","message":"database failed"}"#)
                .unwrap();
        assert!(matches!(response, EngineEvent::Error { code, .. } if code == "DB_UNAVAILABLE"));
    }
}
