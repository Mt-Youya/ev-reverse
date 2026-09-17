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

    // The contract in `docs/CLI-CONTRACT.md` says `finished` is the last line of *every* run, and
    // this is where that is guaranteed. `grab` reports its own outcome — `complete`, `partial`,
    // `nothing`, `cancelled` are all real distinctions — so this only supplies the line for the
    // commands that would otherwise end with a `stage` and no verdict.
    //
    // It is not cosmetic: a reader that refuses to call a run successful without a reported status
    // read every finished `export-evs` as a failure, because the CLI exited 0 in silence.
    if !reporter.finished() {
        reporter.event(&Event::Finished {
            status: Status::Complete,
            exit_code: exit::OK,
            message: String::new(),
        });
    }
}
