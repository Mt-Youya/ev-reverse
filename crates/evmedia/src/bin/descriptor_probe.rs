// Reads each video's EVS descriptor and prints the fields the export pipeline never uses.
//
// `Descriptor` parses `dkey_ver` and then ignores it. The retrospective names it as the one per-course
// switch that might separate lessons whose picture decodes from lessons that come out flat grey, and
// the only way to find out is to compare the values on a lesson that works with one that does not.
//
//   descriptor_probe --session <session.json> --account <id> --course <id> --file <id> [--file <id>...]

use evmedia_core::{catalog_api::CatalogApi, evs_manifest::Descriptor, read_json};
use std::path::PathBuf;

#[derive(clap::Parser)]
struct Args {
    #[arg(long)]
    session: PathBuf,
    #[arg(long)]
    account: i64,
    #[arg(long)]
    course: i64,
    #[arg(long = "file", required = true)]
    files: Vec<i64>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args = <Args as clap::Parser>::parse();
    let session: evmedia_core::catalog_api::CatalogSession = read_json(&args.session)?;
    let api = CatalogApi::new(session)?;

    for file in &args.files {
        let key = api.download_key(*file).await?;
        let tkey = key["tkey"].as_str().unwrap_or_default();
        let descriptor = Descriptor::open(tkey)?;
        println!(
            "course={} file={} dkey_ver={} dkey_len={} base_key_len={} req={} host={} cache_key_len={}",
            args.course,
            file,
            descriptor.dkey_ver,
            descriptor.dkey.len(),
            descriptor.base_key.len(),
            descriptor.req,
            descriptor.host,
            descriptor.cache_key.len()
        );
    }
    Ok(())
}
