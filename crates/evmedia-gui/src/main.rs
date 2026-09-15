//! evmedia desktop shell.
//!
//! The CLI is the product; this window is a way to drive it. Every button here ends up as an
//! argv for `evmedia.exe`, run as a child process, and the log pane is that process's own
//! output. Nothing about catalogues, segments or keys is implemented on this side.

// Release builds have no console, so a panic would otherwise be invisible.
#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

mod config;
mod locate;
mod runner;
#[cfg(windows)]
mod winjob;

#[tauri::command]
fn get_config() -> config::GuiConfig {
    config::load()
}

#[tauri::command]
fn set_config(settings: config::GuiConfig) -> Result<(), String> {
    config::save(&settings)
}

/// The CLI's own argument list, so the window's forms are generated rather than maintained.
#[tauri::command]
fn command_spec() -> Vec<evmedia_contract::CommandSpec> {
    evmedia_contract::describe()
}

/// Without a console, a panic during startup looks like nothing happening at all.
fn install_panic_log() {
    let path = config::config_path().with_file_name("panic.log");
    std::panic::set_hook(Box::new(move |info| {
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let _ = std::fs::write(&path, format!("{info}\n"));
    }));
}

fn main() {
    install_panic_log();
    tauri::Builder::default()
        .manage(runner::JobState::default())
        .invoke_handler(tauri::generate_handler![
            locate::check_cli,
            locate::pick_directory,
            runner::start_job,
            runner::cancel_job,
            runner::force_kill,
            runner::job_running,
            get_config,
            set_config,
            command_spec,
        ])
        .run(tauri::generate_context!())
        .expect("error while running evmedia GUI");
}
