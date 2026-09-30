mod backend;
mod updates;

use std::sync::Arc;

use backend::{start_backend, BackendState};
use tauri::menu::{MenuBuilder, SubmenuBuilder};
use tauri::webview::PageLoadEvent;
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

const DESKTOP_PORT: u16 = 18000;

fn allowed_navigation(url: &tauri::Url, dev_url: Option<&tauri::Url>) -> bool {
    if !url.username().is_empty() || url.password().is_some() {
        return false;
    }
    if url.scheme() == "tauri" || url.host_str() == Some("tauri.localhost") {
        return true;
    }
    // `cargo tauri dev` serves frontendDist from its own loopback port.
    // Allow that configured origin, rather than arbitrary local servers.
    if cfg!(debug_assertions) && dev_url.is_some_and(|dev_url| url.origin() == dev_url.origin()) {
        return true;
    }
    url.scheme() == "http"
        && url.host_str() == Some("127.0.0.1")
        && url.port_or_known_default() == Some(DESKTOP_PORT)
}

fn is_external_navigation(url: &tauri::Url) -> bool {
    matches!(url.scheme(), "http" | "https")
        && !matches!(url.host_str(), Some("127.0.0.1" | "localhost" | "::1"))
}

fn install_menu(app: &tauri::App) -> tauri::Result<()> {
    let application = SubmenuBuilder::new(app, "llms.py")
        .about(None)
        .separator()
        .text("check-updates", "Check for Updates…")
        .separator()
        .services()
        .separator()
        .hide()
        .hide_others()
        .separator()
        .quit()
        .build()?;
    let file = SubmenuBuilder::new(app, "File")
        .text("open-log", "Open Desktop Log")
        .separator()
        .close_window()
        .build()?;
    let edit = SubmenuBuilder::new(app, "Edit")
        .undo()
        .redo()
        .separator()
        .cut()
        .copy()
        .paste()
        .select_all()
        .build()?;
    let view = SubmenuBuilder::new(app, "View")
        .text("reload", "Reload")
        .separator()
        .fullscreen()
        .build()?;
    let window = SubmenuBuilder::new(app, "Window")
        .minimize()
        .maximize()
        .build()?;
    let menu = MenuBuilder::new(app)
        .items(&[&application, &file, &edit, &view, &window])
        .build()?;
    app.set_menu(menu)?;

    app.on_menu_event(|app, event| match event.id().as_ref() {
        "open-log" => {
            if let Ok(path) = backend::desktop_log_path(app) {
                let _ = app.opener().open_path(path.to_string_lossy(), None::<&str>);
            }
        }
        "reload" => {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.eval("window.location.reload()");
            }
        }
        "check-updates" => updates::check_for_updates(app.clone()),
        _ => {}
    });
    Ok(())
}

pub fn run() {
    #[cfg(target_os = "linux")]
    if std::path::Path::new("/sys/module/nvidia").exists()
        && std::env::var_os("WEBKIT_DISABLE_DMABUF_RENDERER").is_none()
    {
        // WebKitGTK's NVIDIA DMABUF path can disconnect from Wayland (Error 71).
        // Apply the workaround only in this process, before GTK creates threads.
        std::env::set_var("WEBKIT_DISABLE_DMABUF_RENDERER", "1");
    }

    let backend = Arc::new(BackendState::default());
    let managed_backend = Arc::clone(&backend);

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .manage(managed_backend)
        .setup(|app| {
            install_menu(app)?;
            let opener_app = app.handle().clone();
            let dev_url = app.config().build.dev_url.clone();
            let window =
                WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                    .title("llms.py")
                    .decorations(!cfg!(target_os = "linux"))
                    .background_color(tauri::webview::Color(17, 24, 39, 255))
                    .inner_size(1280.0, 820.0)
                    .min_inner_size(720.0, 560.0)
                    .center()
                    .on_page_load(|window, payload| {
                        if payload.event() == PageLoadEvent::Finished {
                            backend::show_pending_error(&window);
                        }
                    })
                    .on_navigation(move |url| {
                        if allowed_navigation(url, dev_url.as_ref()) {
                            return true;
                        }
                        if is_external_navigation(url) {
                            let _ = opener_app.opener().open_url(url.as_str(), None::<&str>);
                        }
                        false
                    })
                    .build()?;
            #[cfg(target_os = "linux")]
            window.hide_menu()?;
            window.show()?;

            let state = app.state::<Arc<BackendState>>();
            if let Err(error) = start_backend(&app.handle().clone(), Arc::clone(state.inner())) {
                backend::show_error(&app.handle().clone(), &error.to_string());
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("failed to build llms.py desktop application");

    app.run(move |_app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::Exit | tauri::RunEvent::ExitRequested { .. }
        ) {
            backend.shutdown();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn navigation_is_limited_to_packaged_ui_and_fixed_desktop_origin() {
        assert!(allowed_navigation(
            &"tauri://localhost/index.html".parse().unwrap(),
            None,
        ));
        assert!(allowed_navigation(
            &"http://127.0.0.1:18000/".parse().unwrap(),
            None,
        ));
        assert!(!allowed_navigation(
            &"http://127.0.0.1:18000@evil.example/".parse().unwrap(),
            None,
        ));
        assert!(!allowed_navigation(
            &"http://127.0.0.1:8000/".parse().unwrap(),
            None,
        ));
        assert!(!allowed_navigation(
            &"https://example.com/".parse().unwrap(),
            None,
        ));
    }

    #[test]
    fn development_navigation_allows_only_the_configured_origin() {
        let dev_url = "http://127.0.0.1:1430/".parse().unwrap();
        assert_eq!(
            allowed_navigation(
                &"http://127.0.0.1:1430/index.html".parse().unwrap(),
                Some(&dev_url),
            ),
            cfg!(debug_assertions),
        );
        for blocked in [
            "http://localhost:1430/",
            "http://127.0.0.1:1431/",
            "https://127.0.0.1:1430/",
            "http://127.0.0.1:1430@evil.example/",
        ] {
            assert!(!allowed_navigation(
                &blocked.parse().unwrap(),
                Some(&dev_url)
            ));
        }
    }

    #[test]
    fn only_non_loopback_web_links_are_external() {
        assert!(is_external_navigation(
            &"https://example.com/".parse().unwrap()
        ));
        assert!(!is_external_navigation(
            &"http://127.0.0.1:8000/".parse().unwrap()
        ));
        assert!(!is_external_navigation(
            &"tauri://localhost/index.html".parse().unwrap()
        ));
    }
}
