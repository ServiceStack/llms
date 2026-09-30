use tauri::{AppHandle, Manager, Theme};

pub fn preferences_path(app: &AppHandle) -> tauri::Result<std::path::PathBuf> {
    Ok(app.path().app_config_dir()?.join("preferences.json"))
}

fn parse_color_scheme(contents: &str) -> Option<Theme> {
    let value: serde_json::Value = serde_json::from_str(contents).ok()?;
    match value.get("colorScheme")?.as_str()? {
        "dark" => Some(Theme::Dark),
        "light" => Some(Theme::Light),
        _ => None,
    }
}

pub fn color_scheme(app: &AppHandle) -> Option<Theme> {
    let contents = std::fs::read_to_string(preferences_path(app).ok()?).ok()?;
    parse_color_scheme(&contents)
}

pub fn initialization_script(theme: Option<Theme>) -> String {
    let value = match theme {
        Some(Theme::Dark) => "\"dark\"",
        Some(Theme::Light) => "\"light\"",
        _ => "null",
    };
    format!(
        "window.llmsDesktopColorScheme = {value};\n{}",
        include_str!("theme.js")
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_valid_saved_schemes_override_the_system_theme() {
        assert_eq!(
            parse_color_scheme(r#"{"colorScheme":"dark"}"#),
            Some(Theme::Dark)
        );
        assert_eq!(
            parse_color_scheme(r#"{"colorScheme":"light"}"#),
            Some(Theme::Light)
        );
        for contents in [
            "",
            "{",
            "{}",
            r#"{"colorScheme":"other"}"#,
            r#"{"colorScheme":true}"#,
        ] {
            assert_eq!(parse_color_scheme(contents), None);
        }
    }
}
