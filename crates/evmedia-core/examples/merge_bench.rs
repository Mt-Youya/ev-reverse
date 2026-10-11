//! Measures the phases `export-evs` runs after the download, on a lesson's real byte volumes.
//!
//! `full_export::run` merges with an inline sequential loop while `decode::decode_ev_parallel`
//! does the same work on every core, and the same lesson is AES-decrypted twice more just to
//! validate cached files. Those three facts are worth numbers rather than adjectives.
//!
//! The fixtures are generated here because a real lesson's segment keys only exist in the
//! player's memory: this reads the cache directory's *filenames and sizes*, then encrypts its own
//! TS-shaped plaintext the way the player does. The work measured — read, SHA-256, XOR, AES-256-ECB,
//! write — is the same work either way, because the keys differ and nothing else does.
//!
//! An instrument, not a product path: delete it when the question is settled.
//!
//! ```text
//! cargo run --release -p evmedia-core --example merge_bench -- <cache-dir> [megabytes]
// ```

use aes::Aes256;
use ecb::cipher::{block_padding::NoPadding, BlockDecryptMut, BlockEncryptMut, KeyInit};
use evmedia_contract::Reporter;
use evmedia_core::{
    crypto,
    decode::{self, EvManifest, EvSegment},
};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

const TS_PACKET: usize = 188;

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let cache = PathBuf::from(args.next().unwrap_or_else(|| r"D:\EVPlayer2_download".into()));
    let budget: usize = args.next().and_then(|value| value.parse().ok()).unwrap_or(300) << 20;
    let cores = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
    let root = std::env::temp_dir().join("evmedia-merge-bench");
    let enc = root.join("enc");
    let _ = fs::remove_dir_all(&root);
    fs::create_dir_all(&enc)?;

    println!("cache        {}", cache.display());
    println!("scratch      {}", root.display());
    println!("cores        {cores}");

    let sources = lesson_files(&cache, budget)?;
    let bytes: u64 = sources.iter().map(|(_, size)| *size).sum();
    println!("lesson       {} segments, {:.1} MiB", sources.len(), bytes as f64 / 1048576.0);

    let started = Instant::now();
    let segments = build_fixtures(&sources, &enc)?;
    println!("fixtures     {:.2} s\n", started.elapsed().as_secs_f64());

    let table = [
        ("sequential merge (today)", time(|| sequential_merge(&segments, &enc, &root))),
        ("decode_ev_parallel, 1 job", time(|| parallel_merge(&segments, &enc, &root, 1))),
        ("decode_ev_parallel, all cores", time(|| parallel_merge(&segments, &enc, &root, cores))),
        ("one valid_cipher pass", time(|| valid_cipher_pass(&segments, &enc))),
        ("build_manifest_of (SHA-256)", time(|| manifest_pass(&segments, &enc))),
        ("bare read (IO floor)", time(|| read_pass(&segments, &enc))),
        ("bare AES-256-ECB (in memory)", time(|| aes_pass(&segments, &enc))),
    ];

    println!("{:<32} {:>9} {:>9} {:>10}", "phase", "seconds", "MiB/s", "vs today");
    let baseline = table[0].1.as_secs_f64();
    for (name, elapsed) in table {
        let seconds = elapsed.as_secs_f64();
        println!(
            "{name:<32} {seconds:>9.3} {:>9.1} {:>9.2}x",
            bytes as f64 / 1048576.0 / seconds,
            seconds / baseline
        );
    }
    let _ = fs::remove_dir_all(&root);
    Ok(())
}

fn time<T>(run: impl FnOnce() -> anyhow::Result<T>) -> Duration {
    let started = Instant::now();
    run().expect("bench phase failed");
    started.elapsed()
}

/// The cache's own filenames and sizes, capped by `budget` bytes of total ciphertext.
fn lesson_files(cache: &Path, budget: usize) -> anyhow::Result<Vec<(String, u64)>> {
    let mut files: Vec<(String, u64)> = Vec::new();
    for entry in fs::read_dir(cache)? {
        let entry = entry?;
        let name = entry.file_name().to_string_lossy().to_string();
        let size = entry.metadata()?.len();
        if name.ends_with(".ts") && size > 0 && size % 16 == 0 {
            files.push((name, size));
        }
    }
    files.sort();
    let mut total = 0usize;
    files.retain(|(_, size)| {
        total += *size as usize;
        total <= budget
    });
    Ok(files)
}

fn build_fixtures(sources: &[(String, u64)], enc: &Path) -> anyhow::Result<Vec<EvSegment>> {
    let mut segments = Vec::with_capacity(sources.len());
    for (index, (name, size)) in sources.iter().enumerate() {
        let plain_len = *size as usize / TS_PACKET * TS_PACKET;
        let mut plain = ts_plaintext(plain_len, index as u64);
        plain.extend(std::iter::repeat(b'#').take(*size as usize - plain_len));

        // The live key form: 32 hex characters used directly as 32 key bytes.
        let key_text = crypto::md5_hex(name.as_bytes());
        let key = *<&[u8; 32]>::try_from(key_text.as_bytes()).expect("md5 hex is 32 characters");
        let mask = crypto::mask_from_filename(name);
        let ciphertext = encrypt(&plain, &key, &mask);

        fs::write(enc.join(name), &ciphertext)?;
        segments.push(EvSegment {
            index: index as u32,
            file: name.clone(),
            key_hex: hex::encode(key_text.as_bytes()),
            xor_mask_hex: hex::encode(mask),
            encrypted_sha256: crypto::sha256_hex(&ciphertext),
        });
    }
    Ok(segments)
}

/// TS-shaped bytes that pass `decode::decode_segment`'s stricter packet check.
fn ts_plaintext(len: usize, seed: u64) -> Vec<u8> {
    let mut state = seed | 1;
    let mut bytes = Vec::with_capacity(len);
    while bytes.len() < len {
        let start = bytes.len();
        bytes.resize(start + TS_PACKET, 0);
        let packet = &mut bytes[start..];
        packet[0] = 0x47;
        packet[3] = 0x30; // adaptation-field control 3, which `decode_segment` demands
        for byte in &mut packet[4..] {
            state = state.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            *byte = (state >> 33) as u8;
        }
    }
    bytes.truncate(len);
    bytes
}

/// The exact inverse of `crypto::decrypt`, which unmasks *before* decrypting.
fn encrypt(plain: &[u8], key: &[u8; 32], mask: &[u8; 16]) -> Vec<u8> {
    let mut buffer = plain.to_vec();
    let len = buffer.len();
    ecb::Encryptor::<Aes256>::new_from_slice(key)
        .expect("32-byte key")
        .encrypt_padded_mut::<NoPadding>(&mut buffer, len)
        .expect("length is AES-block aligned");
    for (index, byte) in buffer.iter_mut().enumerate() {
        *byte ^= mask[index % 16];
    }
    buffer
}

fn manifest(segments: &[EvSegment]) -> EvManifest {
    EvManifest {
        tool: "EVPlayer2 5.0.5 merge_bench".into(),
        variant: "xor16_then_aes256ecb_hash_padding".into(),
        segments: segments.to_vec(),
    }
}

/// `full_export::run`'s loop, verbatim in shape.
fn sequential_merge(segments: &[EvSegment], enc: &Path, root: &Path) -> anyhow::Result<()> {
    let mut merged = fs::File::create(root.join("lesson.sequential.ts"))?;
    for item in segments {
        merged.write_all(&decode::decode_segment(&fs::read(enc.join(&item.file))?, item)?)?;
    }
    merged.sync_all()?;
    Ok(())
}

fn parallel_merge(segments: &[EvSegment], enc: &Path, root: &Path, jobs: usize) -> anyhow::Result<()> {
    let output = root.join(format!("lesson.parallel.{jobs}.ts"));
    decode::decode_ev_parallel(enc, manifest(segments), &output, jobs, &Reporter::silent())?;
    fs::remove_file(output)?;
    Ok(())
}

/// What `full_export::run` spends on `valid_cipher`, once per pass over the lesson.
fn valid_cipher_pass(segments: &[EvSegment], enc: &Path) -> anyhow::Result<()> {
    for item in segments {
        let data = fs::read(enc.join(&item.file))?;
        let key = crypto::key_from_hex(&item.key_hex)?;
        let mask = crypto::mask_from_hex(&item.xor_mask_hex)?;
        let _ = crypto::decrypt(&data, &key, &mask, &item.file);
    }
    Ok(())
}

fn manifest_pass(segments: &[EvSegment], enc: &Path) -> anyhow::Result<()> {
    let keys: std::collections::HashMap<String, (u32, String)> = segments
        .iter()
        .map(|item| {
            let key_text = String::from_utf8(hex::decode(&item.key_hex)?).expect("live key form is text");
            Ok((item.file.clone(), (item.index, key_text)))
        })
        .collect::<anyhow::Result<_>>()?;
    let names: Vec<String> = segments.iter().map(|item| item.file.clone()).collect();
    decode::build_manifest_of(enc, names, &keys, "EVPlayer2 5.0.5 merge_bench")?;
    Ok(())
}

fn read_pass(segments: &[EvSegment], enc: &Path) -> anyhow::Result<()> {
    let mut total = 0usize;
    for item in segments {
        total += fs::read(enc.join(&item.file))?.len();
    }
    std::hint::black_box(total);
    Ok(())
}

fn aes_pass(segments: &[EvSegment], enc: &Path) -> anyhow::Result<()> {
    let mut total = 0usize;
    for item in segments {
        let mut buffer = fs::read(enc.join(&item.file))?;
        let key = crypto::key_from_hex(&item.key_hex)?;
        let mask = crypto::mask_from_hex(&item.xor_mask_hex)?;
        for (index, byte) in buffer.iter_mut().enumerate() {
            *byte ^= mask[index % 16];
        }
        let len = buffer.len();
        ecb::Decryptor::<Aes256>::new_from_slice(&key)
            .expect("32-byte key")
            .decrypt_padded_mut::<NoPadding>(&mut buffer)
            .expect("AES-block aligned");
        total += len;
        std::hint::black_box(buffer.last().copied());
    }
    let _ = total;
    Ok(())
}
