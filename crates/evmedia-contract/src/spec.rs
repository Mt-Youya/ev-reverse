//! A JSON description of the CLI surface, derived from the clap definition at runtime.
//!
//! The GUI builds its forms from this instead of from a hand-written list of fields, so a new
//! flag, a renamed option or a changed default appears in the window without anyone having to
//! remember to update a second copy. It is also what makes "the GUI runs the CLI" checkable:
//! every widget on screen exists because the CLI accepts that argument.

use serde::Serialize;

/// Arguments the GUI supplies itself, or that clap adds, and which must not become form fields.
const NOT_A_FORM_FIELD: &[&str] = &["help", "version", "json_events", "stop_file"];

/// Argument ids that are numbers even though their default does not look like one.
const NUMERIC_IDS: &[&str] = &[
    "pid",
    "jobs",
    "poll",
    "idle_limit",
    "attempts",
    "sweep_gap_ms",
    "sweep_batch",
    "sweep_idle",
    "parallel",
];

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct ArgSpec {
    pub id: String,
    pub long: Option<String>,
    pub help: String,
    /// clap's declared default, or `None` when the argument is optional and unset by default.
    pub default: Option<String>,
    pub required: bool,
    /// Positional arguments are rendered as plain fields; the rest get a `--flag`.
    pub positional: bool,
    /// A flag that carries no value of its own.
    pub boolean: bool,
    /// The form should validate this as a number.
    pub numeric: bool,
    /// The form should offer a file/folder picker.
    pub path: bool,
}

#[derive(Serialize, Clone, Debug)]
#[serde(rename_all = "camelCase")]
pub struct CommandSpec {
    pub name: String,
    pub about: String,
    pub args: Vec<ArgSpec>,
}

fn looks_like_a_path(id: &str) -> bool {
    ["input", "output", "manifest", "catalog"]
        .iter()
        .any(|needle| id.contains(needle))
}

pub fn describe() -> Vec<CommandSpec> {
    crate::args::command()
        .get_subcommands()
        .map(|sub| CommandSpec {
            name: sub.get_name().to_string(),
            about: sub.get_about().map(|about| about.to_string()).unwrap_or_default(),
            args: sub
                .get_arguments()
                .filter_map(|arg| {
                    let id = arg.get_id().to_string();
                    if NOT_A_FORM_FIELD.contains(&id.as_str()) {
                        return None;
                    }
                    let boolean = matches!(arg.get_action(), clap::ArgAction::SetTrue);
                    let default = arg
                        .get_default_values()
                        .first()
                        .map(|value| value.to_string_lossy().into_owned());
                    let numeric = !boolean
                        && (NUMERIC_IDS.contains(&id.as_str())
                            || default.as_deref().is_some_and(|d| d.parse::<f64>().is_ok()));
                    Some(ArgSpec {
                        long: arg.get_long().map(str::to_string),
                        help: arg.get_help().map(|help| help.to_string()).unwrap_or_default(),
                        required: arg.is_required_set(),
                        positional: arg.is_positional(),
                        numeric,
                        path: !boolean && looks_like_a_path(&id),
                        boolean,
                        default,
                        id,
                    })
                })
                .collect(),
        })
        .collect()
}
