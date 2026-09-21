//! The player's signed segment-list request, made without the player.

use anyhow::Result;
use evmedia_contract::{FetchArgs, Reporter};
use evmedia_core::api;

pub(crate) async fn run(args: FetchArgs, reporter: &Reporter) -> Result<()> {
    let (playkey, liststr) = if let Some(path) = &args.from_capture {
        let (playkey, liststr) = api::fields_from_capture(path)?;
        reporter.info(format!(
            "play key and {} segment name(s) read from {}",
            liststr.matches(',').count() + 1,
            path.display()
        ));
        (playkey, liststr)
    } else if let Some(path) = &args.from_body {
        let body = std::fs::read(path)
            .map_err(|error| anyhow::anyhow!("read {}: {error}", path.display()))?;
        let (playkey, liststr) = api::request_fields(&body)?;
        reporter.info(format!(
            "play key and {} segment name(s) read from {}",
            liststr.matches(',').count() + 1,
            path.display()
        ));
        (playkey, liststr)
    } else {
        if args.playkey.is_empty() || args.liststr.is_empty() {
            anyhow::bail!("give --from-body, or both --playkey and --liststr");
        }
        (args.playkey.clone(), args.liststr.clone())
    };

    let request = api::ListRequest::new(playkey, liststr);
    let req_time = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs())
        .unwrap_or(0);
    reporter.info(format!(
        "POST {}{} for {} segment(s); {} bytes of signed fields, sign {}",
        request.host,
        request.endpoint,
        request.segment_count(),
        request.sign_input(req_time).len(),
        request.sign(req_time)
    ));

    let list = api::fetch_list(&request, &args.token).await?;
    if let Some(parent) = args.output.parent() {
        std::fs::create_dir_all(parent)?;
    }
    std::fs::write(&args.output, serde_json::to_vec_pretty(&list)?)?;
    let signed = list
        .get("k_l")
        .and_then(|value| value.as_array())
        .map(Vec::len)
        .unwrap_or(0);
    reporter.info(format!(
        "{} segment(s) signed to {}",
        signed,
        args.output.display()
    ));
    Ok(())
}
