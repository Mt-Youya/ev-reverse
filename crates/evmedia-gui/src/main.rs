//! The window itself: a Tauri shell around the queue in the library half of this crate.
//!
//! Release builds have no console, so a panic before the window exists would otherwise be
//! completely invisible. That is what the log file is for.

#![cfg_attr(all(windows, not(debug_assertions)), windows_subsystem = "windows")]

use evmedia_gui::{config, locate, log, server, AppState};

/// Without a console, a panic during startup looks like nothing happening at all.
fn install_panic_log() {
    std::panic::set_hook(Box::new(|info| {
        log::warn(&format!("panic: {info}"));
    }));
}

fn main() {
    log::rotate_if_large();
    log::info(&format!(
        "evmedia-gui {} starting; config at {}",
        env!("CARGO_PKG_VERSION"),
        config::config_path().display()
    ));
    install_panic_log();
    tauri::Builder::default()
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![
            locate::check_cli,
            server::get_config,
            server::set_config,
            server::command_spec,
            server::check_environment,
            server::sniff_session,
            locate::pick_directory,
            locate::pick_file,
            server::open_path,
            server::inspect_session,
            server::load_catalog,
            server::refresh_catalog,
            server::preview_export,
            server::enqueue_export,
            server::start_export,
            server::stop_export,
            server::force_stop,
            server::clear_finished,
            server::retry_finished,
            server::set_halt,
            server::queue_snapshot,
            server::job_log,
            server::log_note,
            server::log_path,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the evmedia desktop shell");
}
