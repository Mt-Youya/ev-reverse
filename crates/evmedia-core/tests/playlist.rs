//! The segment-list contract: what one captured list response has to contain for a lesson to be
//! derivable, downloaded and merged without a player ever running.

use evmedia_core::crypto::key_from_tk;
use evmedia_core::playlist::Playlist;

const LIST: &str = r#"{
  "d_p": "http://cn28027.evplayer.cn/5d8f0047-8585-48ad-a65f-822d56ddde79",
  "k_l": [
    {"idx": 1, "sf": "/119354-bbbbbbbb-0000-4000-8000-000000000002.ts?bid=119354&sid=1113723&t=6aa6975c&v=2.0&sign=aa", "tk": "b8bdf97567b24ae998bcf4392838d65d"},
    {"idx": 0, "sf": "/119354-aaaaaaaa-0000-4000-8000-000000000001.ts?bid=119354&sid=1113723&t=6aa6975c&v=2.0&sign=bb", "tk": "6b682ec8bf813f0bd20c323992b4c9eb"}
  ]
}"#;

fn list() -> Playlist {
    serde_json::from_str(LIST).expect("list parses")
}

#[test]
fn a_list_yields_one_key_per_segment_named_by_its_file() {
    let list = list();
    let keys = list.keys().unwrap();
    assert_eq!(keys.len(), 2);
    let (index, key) = keys
        .get("119354-aaaaaaaa-0000-4000-8000-000000000001.ts")
        .expect("first segment");
    assert_eq!(*index, 0);
    assert_eq!(
        key,
        &key_from_tk("6b682ec8bf813f0bd20c323992b4c9eb", "119354-aaaaaaaa-0000-4000-8000-000000000001.ts").unwrap()
    );
    // the query string is not part of the filename, and the key must not include it
    assert_eq!(keys.get("119354-bbbbbbbb-0000-4000-8000-000000000002.ts").unwrap().0, 1);
}

#[test]
fn the_summary_orders_the_indexes_and_reports_the_range() {
    let summary = evmedia_core::playlist::summarize(&list()).unwrap();
    assert_eq!(summary.segment_count, 2);
    assert_eq!(summary.first_index, 0);
    assert_eq!(summary.last_index, 1);
    assert_eq!(summary.host, "http://cn28027.evplayer.cn/5d8f0047-8585-48ad-a65f-822d56ddde79");
}

/// A list with a hole cannot produce a correct merge, and must say so rather than emit a
/// manifest that *looks* complete: the merge concatenates by index and checks completeness from
/// the highest one, so a gap is a shorter file with no other symptom.
#[test]
fn a_list_with_a_gap_is_refused() {
    let holed = LIST.replace("\"idx\": 1", "\"idx\": 7");
    let list: Playlist = serde_json::from_str(&holed).unwrap();
    let error = evmedia_core::playlist::summarize(&list).unwrap_err().to_string();
    assert!(error.contains("contiguous"), "{error}");
}

#[test]
fn a_filename_named_twice_is_refused_rather_than_resolved_by_order() {
    let doubled = LIST.replace(
        "119354-bbbbbbbb-0000-4000-8000-000000000002.ts",
        "119354-aaaaaaaa-0000-4000-8000-000000000001.ts",
    );
    let list: Playlist = serde_json::from_str(&doubled).unwrap();
    let error = list.keys().unwrap_err().to_string();
    assert!(error.contains("twice"), "{error}");
}

#[test]
fn a_signed_url_is_joined_to_the_host_it_belongs_to() {
    let list = list();
    let entry = &list.k_l[0];
    assert_eq!(
        entry.url(&list.d_p),
        format!("{}{}", list.d_p, entry.sf)
    );
}
