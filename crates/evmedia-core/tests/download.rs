use evmedia_core::download::{download_all, DownloadManifest, RemoteSegment, RemoteVideo};
use evmedia_contract::Reporter;
use std::{collections::BTreeMap, io::{Read, Write}, net::TcpListener, time::Duration};

#[tokio::test]
async fn more_segments_than_parallel_permits_finish_and_keep_their_bytes() {
    let server = TcpListener::bind("127.0.0.1:0").unwrap();
    let address = server.local_addr().unwrap();
    let worker = std::thread::spawn(move || {
        for stream in server.incoming().take(5) {
            let mut stream = stream.unwrap();
            let mut request = [0; 4096];
            stream.read(&mut request).unwrap();
            stream.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\nConnection: close\r\n\r\nDATA").unwrap();
        }
    });
    let stamp = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
    let output = std::env::temp_dir().join(format!("evmedia-download-{}-{stamp}", std::process::id()));
    std::fs::create_dir_all(&output).unwrap();
    let manifest = DownloadManifest {
        version: 1, course_title: "test".into(),
        videos: vec![RemoteVideo {
            id: "lesson".into(), relative_path: String::new(),
            segments: (0..5).map(|index| RemoteSegment {
                index, url: format!("http://{address}/{index}"),
                headers: BTreeMap::new(), sha256: None,
                filename: Some(format!("segment-{index}.ts")),
            }).collect(),
        }],
    };
    let reporter = Reporter::from_cli(false, None);
    tokio::time::timeout(Duration::from_secs(10), download_all(manifest, output.clone(), 2, &reporter))
        .await.expect("download scheduling deadlocked").unwrap();
    worker.join().unwrap();
    for index in 0..5 {
        assert_eq!(std::fs::read(output.join(format!("segment-{index}.ts"))).unwrap(), b"DATA");
    }
    std::fs::remove_dir_all(output).unwrap();
}
