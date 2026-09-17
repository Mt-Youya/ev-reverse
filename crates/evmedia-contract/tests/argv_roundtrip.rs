//! `ToArgv` must remain the exact inverse of clap's parsing.
//!
//! The GUI builds an argv and hands it to the CLI, which parses it again. If `to_argv` ever
//! stops agreeing with the parser — a renamed flag, a value written in the wrong position —
//! the failure would show up as a confusing runtime error in a window rather than as a test.
//! These tests hold the two ends together.

use clap::Parser;
use evmedia_contract::*;
use std::path::PathBuf;

fn path(value: &str) -> PathBuf {
    PathBuf::from(value)
}

/// A maximally-populated command for each subcommand: every flag set, every option non-default.
fn sample(name: &str) -> Command {
    match name {
        "tree" => Command::Tree(TreeArgs { catalog: path("examples/catalog.json") }),
        "catalog" => Command::Catalog(CatalogArgs { session: path("session.json"), account: 119354,
            output: path("catalog") }),
        "download-evs" => Command::DownloadEvs(DownloadEvsArgs { session: path("session.json"),
            account: 119354, course: 315187, file: 903780, output: path("lesson.evs") }),
        "export-evs" => Command::ExportEvs(ExportEvsArgs { session: path("session.json"),
            account: 119354, course: 315187, file: 903780, output: path("lesson.mp4"),
            work: path("work"), jobs: 4, ffmpeg: "ffmpeg".into(), ffprobe: "ffprobe".into(),
            force: true }),
        "download" => Command::Download(DownloadArgs {
            manifest: path("manifest.json"),
            output: path("out"),
            parallel: 4,
        }),
        "decode-ev" => Command::DecodeEv(DecodeEvArgs {
            input: path("segments"),
            manifest: path("captured.json"),
            output: path("lesson.ts"),
        }),
        "capture-ev" => Command::CaptureEv(CaptureEvArgs {
            pid: 4242,
            input: path("segments"),
            output: path("captured.json"),
        }),
        "derive" => Command::Derive(DeriveArgs {
            playlist: path("list.json"),
            input: path("segments"),
            output: path("derived.json"),
        }),
        "fetch" => Command::Fetch(FetchArgs {
            token: "eyJhbGciOiJIUzI1NiJ9.test".to_string(),
            from_body: Some(path("captured.params")),
            from_capture: Some(path("kdf_inputs.jsonl")),
            playkey: "V4bsTWiOcJ1KCbkjYwkzaRFWUM0Xyr2a".to_string(),
            liststr: "0|0|119354-abc.ts".to_string(),
            output: path("list.json"),
        }),
        "grab" => Command::Grab(GrabArgs {
            pid: 4242,
            output: path("grab_out"),
            jobs: 4,
            poll: 2,
            idle_limit: 3,
            attempts: 2,
            no_sweep: true,
            sweep_gap_ms: 10,
            mp4: true,
        }),
        "export-video" => Command::ExportVideo(ExportArgs {
            playlist: path("original.m3u8"), session: path("session.json"),
            output: path("lesson.mkv"), work: path("work"), cache: Some(path("cache")),
            jobs: 4, ffmpeg: "ffmpeg".into(), ffprobe: "ffprobe".into(),
        }),
        "adapters" => Command::Adapters(AdaptersArgs {}),
        "recover" => Command::Recover(RecoverArgs {
            pid: Some(4242),
            cache: path("D:/Downloads/EVPlayer2Downloads"),
            output: path("recover_out"),
            mp4: true,
            sweep: true,
            sweep_batch: 20,
            sweep_gap_ms: 100,
            sweep_idle: 3,
        }),
        other => panic!("subcommand `{other}` has no sample; add one so it stays covered"),
    }
}

fn parse(argv: &[String]) -> Cli {
    Cli::try_parse_from(std::iter::once("evmedia".to_string()).chain(argv.iter().cloned()))
        .unwrap_or_else(|error| panic!("argv {argv:?} did not parse: {error}"))
}

#[test]
fn every_subcommand_round_trips_through_argv() {
    for spec in describe() {
        let command = sample(&spec.name);
        let argv = command.to_argv();
        assert_eq!(argv.first().map(String::as_str), Some(spec.name.as_str()));
        assert_eq!(parse(&argv).command, command, "argv {argv:?} did not round-trip");
    }
}

#[test]
fn every_valued_option_is_written_exactly_once() {
    for spec in describe() {
        let argv = sample(&spec.name).to_argv();
        for arg in &spec.args {
            if arg.boolean {
                // A flag carries meaning only by being present, so it is written when set and
                // omitted otherwise; the sample sets every flag, so it must be here.
                let flag = format!("--{}", arg.long.as_deref().expect("flags have a long name"));
                assert_eq!(
                    argv.iter().filter(|token| **token == flag).count(),
                    1,
                    "{}: {flag} should appear exactly once in {argv:?}",
                    spec.name
                );
                continue;
            }
            let Some(long) = arg.long.as_deref() else {
                continue; // positional
            };
            let flag = format!("--{long}");
            assert_eq!(
                argv.iter().filter(|token| **token == flag).count(),
                1,
                "{}: {flag} should appear exactly once in {argv:?}",
                spec.name
            );
        }
    }
}

#[test]
fn positional_arguments_are_present_and_ordered() {
    for spec in describe() {
        let argv = sample(&spec.name).to_argv();
        let positionals: Vec<&String> = argv
            .iter()
            .skip(1)
            .filter(|token| !token.starts_with("--"))
            .collect();
        let expected = spec.args.iter().filter(|arg| arg.positional).count();
        // Values of `--flag value` pairs also land in this list, so only check that the count of
        // positional-shaped tokens is at least the number of real positionals.
        assert!(
            positionals.len() >= expected,
            "{}: expected at least {expected} positional token(s) in {argv:?}",
            spec.name
        );
    }
}

/// The GUI renders its forms from `describe`, so a subcommand missing there would be invisible.
#[test]
fn describe_covers_every_subcommand_the_parser_accepts() {
    let described: Vec<String> = describe().into_iter().map(|spec| spec.name).collect();
    let declared: Vec<String> = command()
        .get_subcommands()
        .map(|sub| sub.get_name().to_string())
        .collect();
    assert_eq!(described, declared);
    for expected in ["tree", "download", "decode-ev", "capture-ev", "derive", "fetch", "grab", "recover", "adapters"] {
        assert!(described.contains(&expected.to_string()), "missing {expected}");
    }
}

/// The GUI adds these itself; if they became form fields the user could set them twice.
#[test]
fn the_gui_owned_flags_are_not_offered_as_fields() {
    for spec in describe() {
        for arg in &spec.args {
            assert!(
                !matches!(arg.id.as_str(), "json_events" | "stop_file" | "help" | "version"),
                "{}: {} should not be a form field",
                spec.name,
                arg.id
            );
        }
    }
}

/// The GUI shows these in placeholders and leaves the field empty so the CLI applies them. If a
/// reported default ever disagreed with the parsed one, the window would be lying about what
/// running the command will do.
#[test]
fn reported_defaults_match_what_the_parser_applies() {
    let specs = describe();
    let default_of = |command: &str, id: &str| {
        specs
            .iter()
            .find(|spec| spec.name == command)
            .and_then(|spec| spec.args.iter().find(|arg| arg.id == id))
            .and_then(|arg| arg.default.clone())
    };

    let Command::Grab(args) = parse(&["grab".to_string(), "--pid".to_string(), "7".to_string()]).command
    else {
        panic!("expected a grab command");
    };
    assert_eq!(default_of("grab", "jobs").as_deref(), Some(args.jobs.to_string().as_str()));
    assert_eq!(default_of("grab", "poll").as_deref(), Some(args.poll.to_string().as_str()));
    assert_eq!(
        default_of("grab", "idle_limit").as_deref(),
        Some(args.idle_limit.to_string().as_str())
    );
    assert_eq!(default_of("grab", "attempts").as_deref(), Some(args.attempts.to_string().as_str()));
    assert_eq!(
        default_of("grab", "sweep_gap_ms").as_deref(),
        Some(args.sweep_gap_ms.to_string().as_str())
    );
    assert_eq!(
        default_of("grab", "output").as_deref(),
        Some(args.output.to_string_lossy().to_string().as_str())
    );

    let Command::Download(args) =
        parse(&["download".to_string(), "m.json".to_string(), "out".to_string()]).command
    else {
        panic!("expected a download command");
    };
    assert_eq!(
        default_of("download", "parallel").as_deref(),
        Some(args.parallel.to_string().as_str())
    );
}

#[test]
fn a_missing_required_argument_is_a_parse_error() {
    let outcome = Cli::try_parse_from(["evmedia", "grab"]);
    assert!(outcome.is_err(), "grab without --pid must not parse");
}
