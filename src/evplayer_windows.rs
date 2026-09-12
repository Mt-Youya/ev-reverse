use anyhow::{bail, Context, Result};
use md5::{Digest as _, Md5};
use serde::Serialize;
use sha2::{Digest as _, Sha256};
use std::{
    collections::{HashMap, HashSet},
    ffi::c_void,
    fs,
    io::Read,
    path::Path,
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::{
        Diagnostics::Debug::ReadProcessMemory,
        Memory::{
            VirtualQueryEx, MEMORY_BASIC_INFORMATION, MEM_COMMIT, MEM_PRIVATE, PAGE_GUARD,
            PAGE_NOACCESS,
        },
        Threading::{OpenProcess, PROCESS_QUERY_INFORMATION, PROCESS_VM_READ},
    },
};

#[derive(Clone)]
struct ContextRecord {
    file: String,
    token: String,
    schedule: [u8; 32],
    index: u32,
    ready: bool,
}
#[derive(Serialize)]
struct Manifest {
    tool: String,
    segment_count: usize,
    segments: Vec<Segment>,
}
#[derive(Serialize)]
struct Segment {
    index: u32,
    file: String,
    key_hex: String,
    xor_mask_hex: String,
    encrypted_sha256: String,
}
struct Process(HANDLE);
impl Drop for Process {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}

fn open(pid: u32) -> Result<Process> {
    let handle = unsafe { OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, 0, pid) };
    if handle == 0 {
        bail!("cannot read process {pid}; run as the same user or with sufficient permissions");
    }
    Ok(Process(handle))
}
fn read(process: HANDLE, address: usize, len: usize) -> Option<Vec<u8>> {
    let mut bytes = vec![0; len];
    let mut actual = 0;
    let ok = unsafe {
        ReadProcessMemory(
            process,
            address as *const c_void,
            bytes.as_mut_ptr() as *mut c_void,
            len,
            &mut actual,
        )
    };
    if ok == 0 || actual != len {
        None
    } else {
        Some(bytes)
    }
}
fn regions(process: HANDLE) -> Vec<(usize, usize)> {
    let mut output = Vec::new();
    let mut address = 0usize;
    loop {
        let mut info: MEMORY_BASIC_INFORMATION = unsafe { std::mem::zeroed() };
        let length = unsafe {
            VirtualQueryEx(
                process,
                address as *const c_void,
                &mut info,
                std::mem::size_of::<MEMORY_BASIC_INFORMATION>(),
            )
        };
        if length == 0 || info.RegionSize == 0 {
            break;
        }
        let base = info.BaseAddress as usize;
        let end = base.saturating_add(info.RegionSize);
        if info.State == MEM_COMMIT
            && info.Type == MEM_PRIVATE
            && info.Protect & (PAGE_GUARD | PAGE_NOACCESS) == 0
        {
            output.push((base, end));
        }
        if end <= address {
            break;
        }
        address = end;
    }
    output
}
fn number(bytes: &[u8], offset: usize) -> Option<u64> {
    bytes
        .get(offset..offset + 8)
        .map(|v| u64::from_le_bytes(v.try_into().unwrap()))
}
fn remote_string(process: HANDLE, object: &[u8], offset: usize) -> Option<String> {
    let len = number(object, offset + 16)? as usize;
    let cap = number(object, offset + 24)? as usize;
    if len > 8192 || cap < len || cap > 1_048_576 {
        return None;
    }
    let bytes = if cap < 16 {
        object.get(offset..offset + len)?.to_vec()
    } else {
        read(process, number(object, offset)? as usize, len)?
    };
    String::from_utf8(bytes).ok()
}
fn is_hex32(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

fn collect_contexts(process: HANDLE) -> Vec<ContextRecord> {
    let mut output = Vec::new();
    let mut seen = HashSet::new();
    for (base, end) in regions(process) {
        let mut chunk = base;
        while chunk < end {
            let size = (end - chunk).min(0x100300);
            if let Some(bytes) = read(process, chunk, size) {
                for offset in (0..bytes.len().saturating_sub(8)).step_by(8) {
                    let address = chunk + offset;
                    if !seen.insert(address) {
                        continue;
                    }
                    let Some(object) = read(process, address, 0x2a8) else {
                        continue;
                    };
                    let Some(mask) = remote_string(process, &object, 0x268) else {
                        continue;
                    };
                    let Some(file) = remote_string(process, &object, 0x18) else {
                        continue;
                    };
                    if !is_hex32(&mask) || !file.ends_with(".ts") {
                        continue;
                    }
                    let token = remote_string(process, &object, 0x288).unwrap_or_default();
                    let mut schedule = [0; 32];
                    schedule.copy_from_slice(&object[0x120..0x140]);
                    output.push(ContextRecord {
                        file,
                        token,
                        schedule,
                        index: u32::from_le_bytes(object[8..12].try_into().unwrap()),
                        ready: object[0x264] == 1,
                    });
                }
            }
            chunk = chunk.saturating_add(size);
        }
    }
    output
}
fn xtime(value: u8) -> u8 {
    (value << 1) ^ if value & 0x80 != 0 { 0x1b } else { 0 }
}
fn mix(input: &[u8]) -> [u8; 16] {
    let mut output = [0; 16];
    for base in (0..16).step_by(4) {
        let t = input[base] ^ input[base + 1] ^ input[base + 2] ^ input[base + 3];
        for index in 0..4 {
            output[base + index] = input[base + index]
                ^ t
                ^ xtime(input[base + index] ^ input[base + (index + 1) % 4]);
        }
    }
    output
}
fn schedule_key(schedule: &[u8; 32]) -> Result<String> {
    let mut key = [0; 32];
    key[..16].copy_from_slice(&schedule[..16]);
    key[16..].copy_from_slice(&mix(&schedule[16..]));
    String::from_utf8(key.to_vec()).context("invalid active AES schedule")
}
fn md5_hex(value: &[u8]) -> String {
    let mut hash = Md5::new();
    hash.update(value);
    hex::encode(hash.finalize())
}
fn find_salt(process: HANDLE, token: &str, file: &str, expected: &str) -> Option<String> {
    let prefix = format!("{token}{file}").into_bytes();
    let mut tested = HashSet::new();
    for (base, end) in regions(process) {
        let mut chunk = base;
        while chunk < end {
            let size = (end - chunk).min(0x100100);
            if let Some(bytes) = read(process, chunk, size) {
                let mut pos = 0;
                while pos < bytes.len() {
                    if !(32..=126).contains(&bytes[pos]) {
                        pos += 1;
                        continue;
                    }
                    let start = pos;
                    while pos < bytes.len() && (32..=126).contains(&bytes[pos]) {
                        pos += 1;
                    }
                    if pos == bytes.len() || bytes[pos] != 0 || pos - start > 128 {
                        continue;
                    }
                    let candidate = String::from_utf8_lossy(&bytes[start..pos]).to_string();
                    if !tested.insert(candidate.clone()) {
                        continue;
                    }
                    let mut value = prefix.clone();
                    value.extend_from_slice(candidate.as_bytes());
                    if md5_hex(&value) == expected {
                        return Some(candidate);
                    }
                }
            }
            chunk = chunk.saturating_add(size);
        }
    }
    None
}
fn members(path: &Path) -> Result<HashMap<String, Vec<u8>>> {
    if path.is_dir() {
        let mut map = HashMap::new();
        for entry in fs::read_dir(path)? {
            let entry = entry?;
            if entry.file_type()?.is_file() {
                let name = entry.file_name().to_string_lossy().to_string();
                if map.insert(name.clone(), fs::read(entry.path())?).is_some() {
                    bail!("duplicate input filename: {name}");
                }
            }
        }
        return Ok(map);
    }
    let mut archive = zip::ZipArchive::new(fs::File::open(path)?)?;
    let mut map = HashMap::new();
    for index in 0..archive.len() {
        let mut entry = archive.by_index(index)?;
        if entry.is_dir() {
            continue;
        }
        let name = Path::new(entry.name())
            .file_name()
            .and_then(|value| value.to_str())
            .context("invalid ZIP filename")?
            .to_string();
        let mut bytes = Vec::new();
        entry.read_to_end(&mut bytes)?;
        if map.insert(name.clone(), bytes).is_some() {
            bail!("duplicate ZIP filename: {name}");
        }
    }
    Ok(map)
}
pub fn capture(pid: u32, input: &Path, output: &Path) -> Result<()> {
    let input = members(input)?;
    let process = open(pid)?;
    let mut contexts: Vec<_> = collect_contexts(process.0)
        .into_iter()
        .filter(|item| input.contains_key(&item.file) && item.token.len() == 32)
        .collect();
    contexts.sort_by_key(|item| item.index);
    if contexts.is_empty()
        || contexts
            .iter()
            .enumerate()
            .any(|(index, item)| item.index != index as u32)
    {
        bail!("no complete current playback set matches the input archive");
    }
    let probe = contexts
        .iter()
        .find(|item| item.ready)
        .context("start the target video and retry after a few seconds")?;
    let salt = find_salt(
        process.0,
        &probe.token,
        &probe.file,
        &schedule_key(&probe.schedule)?,
    )
    .context("could not recover the initialized EVPlayer2 parameter")?;
    let segments = contexts
        .into_iter()
        .map(|item| {
            let key = md5_hex(format!("{}{}{}", item.token, item.file, salt).as_bytes());
            let mask = &md5_hex(item.file.as_bytes())[..16];
            Segment {
                index: item.index,
                file: item.file.clone(),
                key_hex: hex::encode(key.as_bytes()),
                xor_mask_hex: hex::encode(mask.as_bytes()),
                encrypted_sha256: hex::encode(Sha256::digest(&input[&item.file])),
            }
        })
        .collect::<Vec<_>>();
    if output.exists() {
        bail!("refusing to overwrite {}", output.display());
    }
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)?;
    }
    serde_json::to_writer_pretty(
        fs::File::create(output)?,
        &Manifest {
            tool: "EVPlayer2 5.0.5 Rust live collector".to_string(),
            segment_count: segments.len(),
            segments,
        },
    )?;
    println!("Created {}", output.display());
    Ok(())
}
