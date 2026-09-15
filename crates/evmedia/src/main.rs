//! `evmedia` — the command line tool. The GUI drives this binary; it does not reimplement it.

mod commands;

use clap::Parser;
use evmedia_contract::{
    exit,
    event::PROTOCOL_V1,
    Cli,
    Event,
    Reporter,
    Status,
};

#[tokio::main]
async fn main() {
    let cli = Cli::parse();
    let reporter = Reporter::from_cli(cli.json_events, cli.stop_file.clone());

    reporter.event(&Event::Started {
        protocol: PROTOCOL_V1,
        command: std::env::args().skip(1).collect(),
        app_version: env!("CARGO_PKG_VERSION").to_string(),
    });

    if let Err(error) = commands::dispatch(cli.command, &reporter).await {
        // Matches what `fn main() -> Result<()>` produced before: anyhow's Debug on stderr.
        eprintln!("Error: {error:?}");
        reporter.event(&Event::Finished {
            status: Status::Failed,
            exit_code: exit::FAILED,
            message: error.to_string(),
        });
        std::process::exit(exit::FAILED);
    }
}
