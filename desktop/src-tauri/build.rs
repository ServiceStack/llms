fn main() {
    // Inline the canonical UI loader before Tauri embeds the startup page.
    // Keep a checked-in copy so the static page also works outside a build.
    let root = std::path::PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").unwrap());
    let source = root.join("../../llms/index.html");
    let target = root.join("../static/index.html");
    println!("cargo:rerun-if-changed={}", source.display());
    println!("cargo:rerun-if-changed={}", target.display());
    let ui = std::fs::read_to_string(source).expect("read UI loading sprite");
    let original = std::fs::read_to_string(&target).expect("read desktop startup page");
    let mut desktop = original.clone();
    for name in ["styles", "sprite"] {
        let start = format!("<!-- llms-loading-{name}:start -->");
        let end = format!("<!-- llms-loading-{name}:end -->");
        let ui_start = ui.find(&start).expect("UI loader start marker") + start.len();
        let ui_end = ui[ui_start..].find(&end).expect("UI loader end marker") + ui_start;
        let desktop_start =
            desktop.find(&start).expect("desktop loader start marker") + start.len();
        let desktop_end = desktop[desktop_start..]
            .find(&end)
            .expect("desktop loader end marker")
            + desktop_start;
        desktop.replace_range(desktop_start..desktop_end, &ui[ui_start..ui_end]);
    }
    if desktop != original {
        std::fs::write(target, desktop).expect("sync desktop loading sprite");
    }
    tauri_build::build()
}
