use std::env;
use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::Deserialize;
use tauri::{AppHandle, Manager};

const HOST: &str = "127.0.0.1";
const PORT: u16 = 18000;
const READY_TIMEOUT: Duration = Duration::from_secs(45);
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(8);

#[derive(Debug, Deserialize)]
struct ReadyEvent {
    event: String,
    host: String,
    port: u16,
}

#[derive(Default)]
pub struct BackendState {
    child: Mutex<Option<Child>>,
    token: Mutex<Option<String>>,
    ready: AtomicBool,
    startup_error: Mutex<Option<String>>,
    shutting_down: AtomicBool,
    shutdown_complete: AtomicBool,
}

impl BackendState {
    pub fn shutdown_complete(&self) -> bool {
        self.shutdown_complete.load(Ordering::Acquire)
    }

    pub fn begin_shutdown(self: &Arc<Self>, on_complete: impl FnOnce() + Send + 'static) {
        if self.shutting_down.swap(true, Ordering::AcqRel) {
            return;
        }
        let state = Arc::clone(self);
        thread::spawn(move || {
            state.shutdown();
            state.shutdown_complete.store(true, Ordering::Release);
            on_complete();
        });
    }

    pub fn shutdown(&self) {
        // No HTTP request is necessary when startup failed before spawning a child.
        if self
            .child
            .lock()
            .map(|child| child.is_none())
            .unwrap_or(true)
        {
            return;
        }
        let token = self.token.lock().ok().and_then(|value| value.clone());
        if let Some(token) = token {
            let _ = request_shutdown(&token);
        }

        let deadline = Instant::now() + SHUTDOWN_TIMEOUT;
        while Instant::now() < deadline {
            if let Ok(mut child) = self.child.lock() {
                if child.is_none() {
                    return;
                }
                if let Some(process) = child.as_mut() {
                    if matches!(process.try_wait(), Ok(Some(_))) {
                        *child = None;
                        return;
                    }
                }
            }
            thread::sleep(Duration::from_millis(100));
        }

        if let Ok(mut child) = self.child.lock() {
            if let Some(process) = child.as_mut() {
                terminate_process(process);
                let _ = process.wait();
            }
            *child = None;
        }
    }
}

pub fn start_backend(
    app: &AppHandle,
    state: Arc<BackendState>,
) -> Result<(), Box<dyn std::error::Error>> {
    if port_is_in_use() {
        return Err(format!(
            "Port {PORT} is already in use. Stop the process using it, then reopen llms.py."
        )
        .into());
    }

    let token = random_token()?;
    *state
        .token
        .lock()
        .map_err(|_| "desktop backend token lock is poisoned")? = Some(token.clone());

    let executable = sidecar_executable(app)?;
    if !executable.is_file() {
        return Err(format!(
            "The packaged llms-py runtime was not found at {}. Run desktop/scripts/build-sidecar.py first.",
            executable.display()
        )
        .into());
    }

    let log_path = desktop_log_path(app)?;
    let log = Arc::new(Mutex::new(open_log(&log_path)?));
    write_log(&log, &format!("Starting {}", executable.display()));

    let mut command = Command::new(&executable);
    command
        .args(["--serve", &PORT.to_string()])
        .env("LLMS_DESKTOP_TOKEN", &token)
        .env(
            "LLMS_DESKTOP_PREFERENCES_PATH",
            crate::preferences::preferences_path(app)?,
        )
        .env("PYTHONUNBUFFERED", "1")
        .env("PATH", desktop_path())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Ok(home) = app.path().home_dir() {
        command.current_dir(home);
    }

    let mut child = command.spawn()?;
    let stdout = child
        .stdout
        .take()
        .ok_or("failed to capture llms-py stdout")?;
    let stderr = child
        .stderr
        .take()
        .ok_or("failed to capture llms-py stderr")?;
    *state
        .child
        .lock()
        .map_err(|_| "desktop backend process lock is poisoned")? = Some(child);

    let stdout_app = app.clone();
    let stdout_state = Arc::clone(&state);
    let stdout_log = Arc::clone(&log);
    let bootstrap_token = token.clone();
    thread::spawn(move || {
        for line in BufReader::new(stdout).lines().map_while(Result::ok) {
            write_log(&stdout_log, &line);
            if let Ok(event) = serde_json::from_str::<ReadyEvent>(&line) {
                if event.event == "ready" && event.host == HOST && event.port == PORT {
                    match wait_for_health(Duration::from_secs(5)) {
                        Ok(()) => {
                            stdout_state.ready.store(true, Ordering::Release);
                            navigate_to_app(&stdout_app, &bootstrap_token);
                        }
                        Err(error) => show_error(&stdout_app, &error),
                    }
                }
            }
        }
    });

    let stderr_log = Arc::clone(&log);
    thread::spawn(move || {
        for line in BufReader::new(stderr).lines().map_while(Result::ok) {
            write_log(&stderr_log, &format!("stderr: {line}"));
        }
    });

    let timeout_app = app.clone();
    let timeout_state = Arc::clone(&state);
    thread::spawn(move || {
        let deadline = Instant::now() + READY_TIMEOUT;
        while Instant::now() < deadline {
            if timeout_state.ready.load(Ordering::Acquire)
                || timeout_state.shutting_down.load(Ordering::Acquire)
            {
                return;
            }
            if let Ok(mut child) = timeout_state.child.lock() {
                if let Some(process) = child.as_mut() {
                    if let Ok(Some(status)) = process.try_wait() {
                        show_error(
                            &timeout_app,
                            &format!("The llms-py backend exited before startup completed ({status}). Open the log for details."),
                        );
                        return;
                    }
                }
            }
            thread::sleep(Duration::from_millis(200));
        }
        show_error(
            &timeout_app,
            "The llms-py backend did not become ready within 45 seconds.",
        );
    });

    Ok(())
}

pub fn show_error(app: &AppHandle, message: &str) {
    if let Ok(mut error) = app.state::<Arc<BackendState>>().startup_error.lock() {
        *error = Some(message.to_string());
    }
    if let Some(window) = app.get_webview_window("main") {
        show_pending_error(&window);
    }
}

pub fn show_pending_error(window: &tauri::WebviewWindow) {
    let message = window
        .state::<Arc<BackendState>>()
        .startup_error
        .lock()
        .ok()
        .and_then(|error| error.clone());
    if let Some(message) = message {
        let encoded =
            serde_json::to_string(&message).unwrap_or_else(|_| "\"Desktop startup failed\"".into());
        let _ = window.eval(format!("window.llmsDesktopShowError?.({encoded})"));
    }
}

fn navigate_to_app(app: &AppHandle, token: &str) {
    if let Some(window) = app.get_webview_window("main") {
        let url = format!("http://{HOST}:{PORT}/~desktop/bootstrap/{token}");
        match url.parse() {
            Ok(url) => {
                let _ = window.navigate(url);
            }
            Err(error) => show_error(app, &format!("Invalid desktop URL: {error}")),
        }
    }
}

fn random_token() -> Result<String, String> {
    let mut bytes = [0_u8; 32];
    getrandom::fill(&mut bytes)
        .map_err(|error| format!("failed to generate desktop session token: {error}"))?;
    Ok(hex::encode(bytes))
}

fn sidecar_executable(app: &AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let name = if cfg!(windows) {
        "llms-desktop.exe"
    } else {
        "llms-desktop"
    };
    Ok(app
        .path()
        .resource_dir()?
        .join("resources")
        .join("sidecar")
        .join(name))
}

pub fn desktop_log_path(app: &AppHandle) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let directory = app.path().app_log_dir()?;
    fs::create_dir_all(&directory)?;
    Ok(directory.join("desktop.log"))
}

fn open_log(path: &Path) -> std::io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

fn write_log(log: &Arc<Mutex<File>>, message: &str) {
    if let Ok(mut output) = log.lock() {
        let _ = writeln!(output, "{message}");
        let _ = output.flush();
    }
}

fn desktop_path() -> String {
    let mut paths: Vec<PathBuf> = env::var_os("PATH")
        .map(|value| env::split_paths(&value).collect())
        .unwrap_or_default();
    for path in ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"] {
        let path = PathBuf::from(path);
        if !paths.contains(&path) {
            paths.push(path);
        }
    }
    env::join_paths(paths)
        .unwrap_or_default()
        .to_string_lossy()
        .into_owned()
}

fn port_is_in_use() -> bool {
    let address = SocketAddr::from(([127, 0, 0, 1], PORT));
    TcpStream::connect_timeout(&address, Duration::from_millis(250)).is_ok()
}

fn wait_for_health(timeout: Duration) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    let mut last_error = String::new();
    while Instant::now() < deadline {
        match raw_http_request("GET", "/~desktop/health", &[]) {
            Ok(response) if response.starts_with("HTTP/1.1 200") => return Ok(()),
            Ok(response) => {
                last_error = response
                    .lines()
                    .next()
                    .unwrap_or("unexpected response")
                    .to_string()
            }
            Err(error) => last_error = error.to_string(),
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err(format!("The llms-py health check failed: {last_error}"))
}

fn request_shutdown(token: &str) -> std::io::Result<()> {
    let response = raw_http_request(
        "POST",
        "/~desktop/shutdown",
        &[("X-LLMS-Desktop-Token", token)],
    )?;
    if response.starts_with("HTTP/1.1 200") {
        Ok(())
    } else {
        Err(std::io::Error::other(
            "desktop shutdown request was rejected",
        ))
    }
}

fn raw_http_request(method: &str, path: &str, headers: &[(&str, &str)]) -> std::io::Result<String> {
    let address = SocketAddr::from(([127, 0, 0, 1], PORT));
    let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(1))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    let mut request =
        format!("{method} {path} HTTP/1.1\r\nHost: {HOST}:{PORT}\r\nConnection: close\r\n");
    for (name, value) in headers {
        request.push_str(&format!("{name}: {value}\r\n"));
    }
    request.push_str("Content-Length: 0\r\n\r\n");
    stream.write_all(request.as_bytes())?;
    let mut response = String::new();
    stream.read_to_string(&mut response)?;
    Ok(response)
}

#[cfg(unix)]
fn terminate_process(process: &mut Child) {
    unsafe {
        libc::kill(process.id() as i32, libc::SIGTERM);
    }
    thread::sleep(Duration::from_secs(1));
    if process.try_wait().ok().flatten().is_none() {
        let _ = process.kill();
    }
}

#[cfg(windows)]
fn terminate_process(process: &mut Child) {
    let _ = process.kill();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn shutdown_without_a_child_completes_once() {
        let state = Arc::new(BackendState::default());
        let (sender, receiver) = std::sync::mpsc::channel();
        let duplicate_sender = sender.clone();
        state.begin_shutdown(move || sender.send(()).unwrap());
        state.begin_shutdown(move || duplicate_sender.send(()).unwrap());
        receiver.recv_timeout(Duration::from_secs(1)).unwrap();
        assert!(state.shutdown_complete());
        assert!(receiver.recv_timeout(Duration::from_millis(100)).is_err());
    }

    #[cfg(unix)]
    #[test]
    fn unresponsive_child_shutdown_does_not_block_the_caller() {
        let state = Arc::new(BackendState::default());
        *state.child.lock().unwrap() = Some(Command::new("sleep").arg("30").spawn().unwrap());
        let (sender, receiver) = std::sync::mpsc::channel();
        let started = Instant::now();
        state.begin_shutdown(move || sender.send(()).unwrap());
        let returned_after = started.elapsed();
        receiver.recv_timeout(Duration::from_secs(12)).unwrap();
        assert!(returned_after < Duration::from_millis(250));
        assert!(state.shutdown_complete());
        assert!(state.child.lock().unwrap().is_none());
    }

    #[test]
    fn generated_tokens_have_256_bits_of_random_input() {
        let first = random_token().unwrap();
        let second = random_token().unwrap();
        assert_eq!(first.len(), 64);
        assert_ne!(first, second);
    }

    #[test]
    fn desktop_path_includes_standard_gui_application_locations() {
        let path = desktop_path();
        assert!(path.contains("/usr/bin"));
        assert!(path.contains("/usr/local/bin"));
    }
}
