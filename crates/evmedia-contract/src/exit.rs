//! Exit codes, frozen at the values the pre-workspace CLI already returned.
//!
//! Rich outcomes are deliberately *not* given their own codes. "Ran to completion", "ran but
//! the lesson is incomplete" and "found nothing to do" all return [`OK`] today, and scripts
//! already depend on that; the distinction belongs in [`crate::Event::Finished`]'s `status`,
//! which is strictly more expressive and costs nothing to extend.

/// The command ran. Includes a partial merge and an empty harvest — both returned 0 before.
pub const OK: i32 = 0;
/// A runtime error, surfaced as an `anyhow::Error`.
pub const FAILED: i32 = 1;
/// The arguments did not parse. Emitted by clap.
pub const USAGE: i32 = 2;
