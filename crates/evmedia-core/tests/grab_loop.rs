//! The harvest loop's core behaviour: resume, retry, the completeness verdict, the merge, and
//! the ordering of download against key derivation. Everything here runs against a fixture — no
//! player, no Windows, no network.

mod harvest_fixture;

use evmedia_contract::{Event, Reporter, SegmentState};
use evmedia_core::harvest::grab;
use harvest_fixture::{options, scratch, segment_name, serve, stage};
use std::collections::HashMap;
use std::time::Duration;

#[test]
fn a_complete_lesson_merges_to_lesson_ts() {
    let output = scratch("complete");
    let (fixture, plains) = stage(&output, 6);
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();

    let merged = std::fs::read(output.join("lesson.ts")).expect("lesson.ts");
    let expected: Vec<u8> = plains.concat();
    assert_eq!(merged, expected);
    assert!(!output.join("lesson.partial.ts").exists());
    // Every segment is cached decrypted, independently of the merge.
    for index in 0..6u32 {
        assert!(output.join("dec").join(format!("{index:06}.ts")).exists());
    }
}

#[test]
fn a_lesson_with_a_hole_stays_partial_and_is_never_called_lesson_ts() {
    let output = scratch("hole");
    let (fixture, plains) = stage(&output, 6);
    // The player never decrypted index 3, so its key does not exist. Its URL is gone too, which
    // is how the loop knows the segment is not part of this lesson rather than a gap in it.
    let file = fixture.forget_key(3);
    fixture.forget_url(&file);
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();

    assert!(!output.join("lesson.ts").exists(), "a hole must not produce a complete-looking file");
    let merged = std::fs::read(output.join("lesson.partial.ts")).expect("lesson.partial.ts");
    let expected: Vec<u8> = [0usize, 1, 2, 4, 5].iter().flat_map(|i| plains[*i].clone()).collect();
    assert_eq!(merged, expected);
}

#[test]
fn a_rerun_resumes_and_reproduces_the_same_bytes() {
    let output = scratch("resume");
    let (fixture, plains) = stage(&output, 5);
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();
    let first = std::fs::read(output.join("lesson.ts")).unwrap();

    // Delete the ciphertext cache: if the rerun needed the network it would now fail, so this
    // also proves it resumed from `dec/` rather than re-downloading.
    std::fs::remove_dir_all(output.join("enc")).unwrap();
    // And delete the merge output. Without this the assertion below reads the first run's file
    // and passes even if the second run wrote nothing at all -- which it did not, before this
    // line existed: the second read was of a stale file and the test proved nothing.
    std::fs::remove_file(output.join("lesson.ts")).unwrap();
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();
    let second = std::fs::read(output.join("lesson.ts")).unwrap();

    assert_eq!(first, second);
    assert_eq!(first, plains.concat());
}

#[test]
fn a_segment_that_cannot_be_decrypted_is_given_up_on_rather_than_retried_forever() {
    let output = scratch("corrupt");
    let (fixture, _) = stage(&output, 3);
    // Corrupt one AES block *past* the 752-byte key probe. The probe is what decides whether a
    // key opens a segment, so the key still tests as correct and the segment is still fetched —
    // it is the decryption that then fails. The block is chosen to hold the 0x47 at offset 940,
    // because a garbled block fails the packet-alignment check. Corrupting bytes inside the probe
    // instead would just mean no candidate solves the segment, which is a different path.
    let file = fixture.file_of(1);
    let key = fixture.key_of(1);
    let mut broken = std::fs::read(output.join("enc").join(&file)).unwrap();
    assert!(broken.len() > 944, "the fixture must extend past the key probe");
    for byte in broken[928..944].iter_mut() {
        *byte ^= 0xa5;
    }
    std::fs::write(output.join("enc").join(&file), &broken).unwrap();
    fixture.restore_key(1, file, key);

    // The idle limit is lifted out of the way so that the attempt cap is the only thing that can
    // stop the loop retrying this segment. Left at its default the loop stops on idle after three
    // idle polls, and the test passed with the cap removed -- it was pinning the idle limit, not
    // the cap, under a name that claimed the opposite.
    let (reporter, events) = Reporter::capturing();
    let mut opts = options(&output);
    opts.idle_limit = 40;
    let started = std::time::Instant::now();
    grab::run(&fixture, &opts, &reporter).unwrap();
    assert!(started.elapsed() < Duration::from_secs(30), "the loop must not spin on a bad segment");

    let attempts: Vec<usize> = events
        .lock()
        .unwrap()
        .iter()
        .filter_map(|event| match event {
            Event::Segment { index: 1, state: SegmentState::Failed, attempt, .. } => Some(*attempt),
            _ => None,
        })
        .collect();
    assert_eq!(attempts, vec![1, 2], "two attempts, then this segment is left alone");
    assert!(output.join("lesson.partial.ts").exists());
}

/// The live contexts are a key source in their own right, and the loop has to consume them: they
/// carry index, filename and key together, which is both the cheapest and the most exact answer
/// available. With the candidate path switched off there is nothing else to fall back on, so a
/// lesson that merges completely can only have been built from them.
#[test]
fn the_loop_uses_the_keys_the_live_contexts_carry() {
    let output = scratch("live-contexts");
    let (fixture, plains) = stage(&output, 4);
    fixture.use_only_live_contexts();
    grab::run(&fixture, &options(&output), &Reporter::silent()).unwrap();

    assert_eq!(
        std::fs::read(output.join("lesson.ts")).expect("lesson.ts"),
        plains.concat(),
        "with no candidates offered, only the live contexts could have supplied these keys"
    );
}

/// The player releases signed URLs as playback moves past them, but a key and an index are
/// permanent, and so is a cached ciphertext. A segment that has all three must not be stranded
/// by the one thing it no longer needs — which is exactly what a URL requirement does to it.
#[test]
fn a_cached_segment_is_still_decrypted_after_its_url_is_released() {
    let output = scratch("released-url");
    let (fixture, plains) = stage(&output, 4);
    fixture.forget_url(&fixture.file_of(2));

    let mut opts = options(&output);
    opts.idle_limit = 6;
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert!(
        output.join("dec").join("000002.ts").exists(),
        "index 2 must decrypt from its cache without a URL"
    );
    assert_eq!(
        std::fs::read(output.join("lesson.ts")).expect("lesson.ts"),
        plains.concat(),
        "a lesson with no gaps must still merge completely"
    );
}

/// The download must never be gated on already having a key: no bytes means no key, and waiting
/// for the key first would wait forever. This is also the only test that reaches
/// `download_missing`'s fetching branch — every other one starts with `enc/` populated, which is
/// exactly the state it early-returns from.
#[test]
fn ciphertext_is_fetched_without_any_key_existing_yet() {
    let output = scratch("download");
    let (fixture, plains) = stage(&output, 3);

    let bodies: HashMap<String, Vec<u8>> = (0..3)
        .map(|index| {
            let file = segment_name(index);
            (format!("/{index}.ts"), std::fs::read(output.join("enc").join(&file)).unwrap())
        })
        .collect();
    std::fs::remove_dir_all(output.join("enc")).unwrap();
    let (base, _server) = serve(bodies);
    fixture.retarget(&base);

    let mut opts = options(&output);
    opts.idle_limit = 8;
    grab::run(&fixture, &opts, &Reporter::silent()).unwrap();

    assert_eq!(
        std::fs::read(output.join("lesson.ts")).expect("lesson.ts"),
        plains.concat(),
        "every segment must be fetched, decrypted and merged"
    );
}
