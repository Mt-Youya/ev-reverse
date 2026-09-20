//! The one surface shared by the `evmedia` CLI and `evmedia-gui`.
//!
//! Everything the GUI is allowed to know about the CLI lives here: the argv shape, the
//! newline-delimited event schema, and the exit codes. No business logic, no I/O, no
//! platform code — both binaries depend on this crate and on nothing of each other.

pub mod args;
pub mod catalog_args;
pub mod export_args;
pub mod event;
pub mod exit;
pub mod report;
pub mod spec;

pub use args::*;
pub use catalog_args::{CatalogArgs, DownloadEvsArgs, ExportBatchArgs, ExportBatchPlan, ExportBatchVideo, ExportEvsArgs, ExportPhase};
pub use export_args::ExportArgs;
pub use event::{ArtifactKind, Event, Level, SegmentState, Stage, StageState, Status};
pub use report::{Reporter, ReporterMode};
pub use spec::{describe, ArgSpec, CommandSpec};
